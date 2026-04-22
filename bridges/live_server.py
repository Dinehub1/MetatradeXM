"""
live_server.py — Real-time indicator WebSocket server (port 8887).

Architecture (push, not poll):
  MetaApi Cloud ──streaming──► live_server ──WebSocket──► tv_client ──► Gemini + trader

vs. the old polling approach in tv_server.py:
  Broker candles ◄──HTTP poll/30s── tv_server.py

Why this wins:
  • MetaApi pushes candle updates to us on every tick — latency < 1s vs ≤ 30s
  • Single persistent connection instead of hundreds of HTTP round-trips per day
  • Indicators recompute on bar close (real bar events), not on a timer

On startup:
  1. Seeds rolling candle buffers via MetaApi historical REST (same as bridge)
  2. Opens a streaming MetaApi connection and subscribes to candle events
  3. Recomputes all indicators whenever an M15 (or higher TF) bar closes
  4. Pushes a snapshot WebSocket message to all connected tv_client consumers

The TV client (tv_client.py) and the trader (analyze_from_tv) are unchanged —
they read from ws://localhost:8887 exactly as before.

Run standalone (on VM, keep in screen/tmux):
    python3 bridges/live_server.py

Or embedded (started automatically by ContinuousTrader):
    from bridges.live_server import start_live_server
    start_live_server()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("live_server")

PORT     = 8887
_SYMBOLS = [("XAUUSD", "GOLD.i#"), ("XAGUSD", "SILVER.i#")]
_TF_MAP  = {"M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}
_TF_SEED = {"M15": 300, "H1": 200, "H4": 100, "D1": 60}      # historical bars per TF

# Map MetaApi timeframe strings → our labels
_REVERSE_TF = {v: k for k, v in _TF_MAP.items()}


# ── Synchronization listener ───────────────────────────────────────────────────

from metaapi_cloud_sdk.clients.metaapi.synchronization_listener import SynchronizationListener


class _MarketListener(SynchronizationListener):
    """
    Receives real-time candle events from MetaApi.
    Triggers indicator recomputation when an M15 (or larger TF) bar closes.
    """

    def __init__(self, server: "LiveIndicatorServer"):
        super().__init__()
        self._server = server

    async def on_candles_updated(
        self,
        instance_index: str,
        candles,
        equity=None, margin=None, free_margin=None,
        margin_level=None, account_currency_exchange_rate=None,
    ):
        """Called by MetaApi whenever a candle's OHLCV data changes (every tick)."""
        bar_closed = False
        for c in (candles or []):
            symbol   = c.get("symbol") if isinstance(c, dict) else getattr(c, "symbol", None)
            tf_str   = c.get("timeframe") if isinstance(c, dict) else getattr(c, "timeframe", None)
            candle_t = c.get("time") if isinstance(c, dict) else getattr(c, "time", None)

            for display, broker_sym in _SYMBOLS:
                if broker_sym != symbol:
                    continue
                tf_label = _REVERSE_TF.get(tf_str)
                if not tf_label:
                    continue
                c_dict = c if isinstance(c, dict) else {
                    "symbol": symbol, "timeframe": tf_str, "time": candle_t,
                    "open":  getattr(c, "open",  0),
                    "high":  getattr(c, "high",  0),
                    "low":   getattr(c, "low",   0),
                    "close": getattr(c, "close", 0),
                    "tickVolume": getattr(c, "tickVolume", 0),
                }
                if self._server._update_buffer(display, tf_label, c_dict):
                    # A new bar started = the previous bar just closed
                    if tf_label == "M15":
                        bar_closed = True

        if bar_closed:
            self._server._compute_all()
            self._server._broadcast_sync({
                "type": "snapshot",
                "data": {
                    "session": LiveIndicatorServer._session(),
                    "symbols": dict(self._server._latest),
                },
            })

    async def on_symbol_prices_updated(
        self,
        instance_index: str,
        prices,
        equity=None, margin=None, free_margin=None,
        margin_level=None, account_currency_exchange_rate=None,
    ):
        """Update the close price of the current M15 bar on every tick."""
        for p in (prices or []):
            symbol = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
            bid    = p.get("bid")    if isinstance(p, dict) else getattr(p, "bid",    None)
            ask    = p.get("ask")    if isinstance(p, dict) else getattr(p, "ask",    None)
            if not (bid and ask):
                continue
            mid = (bid + ask) / 2.0
            for display, broker_sym in _SYMBOLS:
                if broker_sym != symbol:
                    continue
                buf = self._server._buffers.get(display, {}).get("M15")
                if buf is not None and len(buf) > 0:
                    idx = len(buf) - 1
                    buf.at[idx, "c"] = mid
                    if mid > buf.at[idx, "h"]:
                        buf.at[idx, "h"] = mid
                    if mid < buf.at[idx, "l"]:
                        buf.at[idx, "l"] = mid


# ── Main server class ──────────────────────────────────────────────────────────


class LiveIndicatorServer:
    """
    Persistent streaming connection to MetaApi → real-time indicator WebSocket server.

    Drop-in replacement for tv_server.py — same port, same message protocol,
    but data arrives via MetaApi push instead of 30-second polling.
    """

    def __init__(
        self,
        token: str     = None,
        account_id: str = None,
        port: int       = PORT,
    ):
        self._token      = token      or os.environ.get("METAAPI_TOKEN", "")
        self._account_id = account_id or os.environ.get("METAAPI_ACCOUNT_ID", "")
        self._port       = port

        self._clients: set  = set()
        self._latest:  dict = {}   # display_sym → {indicators, timeframes, _last_update}
        self._buffers: dict = {}   # display_sym → tf_label → pd.DataFrame

        self._running = False
        self._loop:   Optional[asyncio.AbstractEventLoop] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        t = threading.Thread(target=self._run_in_thread, daemon=True, name="live-server")
        t.start()
        log.info("[LiveServer] Starting on ws://0.0.0.0:%d", self._port)

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def is_ready(self) -> bool:
        return bool(self._latest)

    # ── Thread / async entry ───────────────────────────────────────────────────

    def _run_in_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            log.error("[LiveServer] Fatal: %s", e)

    async def _async_main(self):
        import websockets
        from metaapi_cloud_sdk import MetaApi

        try:
            log.info("[LiveServer] Connecting to MetaApi...")
            api     = MetaApi(self._token)
            account = await api.metatrader_account_api.get_account(self._account_id)
            if account.state not in ("DEPLOYING", "DEPLOYED"):
                await account.deploy()
            await account.wait_connected()
            log.info("[LiveServer] MetaApi account connected")

            # ── Seed buffers from historical REST data ─────────────────────────
            await self._seed_buffers_rest(api)

            # ── Initial indicator computation ──────────────────────────────────
            self._compute_all()
            if self._latest:
                log.info("[LiveServer] Initial indicators ready — %s", list(self._latest))
            else:
                log.warning("[LiveServer] No indicator data after seed — will retry on first bar close")

            # ── Streaming connection ───────────────────────────────────────────
            conn     = account.get_streaming_connection()
            listener = _MarketListener(self)
            conn.add_synchronization_listener(listener)
            await conn.connect()
            await conn.wait_synchronized({"timeoutInSeconds": 60})
            log.info("[LiveServer] Streaming connection synchronized")

            for display, broker_sym in _SYMBOLS:
                await conn.subscribe_to_market_data(
                    broker_sym,
                    [
                        {"type": "quotes"},                       # tick-level price updates
                        {"type": "candles", "timeframe": "15m"},  # M15 bar events
                        {"type": "candles", "timeframe": "1h"},   # H1
                        {"type": "candles", "timeframe": "4h"},   # H4
                        {"type": "candles", "timeframe": "1d"},   # D1
                    ],
                )
                log.info("[LiveServer] Subscribed to %s market data", display)

            # ── WebSocket server ───────────────────────────────────────────────
            async with websockets.serve(self._handle_client, "0.0.0.0", self._port):
                log.info("[LiveServer] Listening on ws://0.0.0.0:%d (external: ws://<VM-IP>:%d)",
                         self._port, self._port)
                await asyncio.Future()   # run until loop stopped

        except Exception as e:
            log.error("[LiveServer] Error in _async_main: %s", e)
            raise

    # ── Historical seed (REST — reuses the bridge's URL pattern) ──────────────

    async def _seed_buffers_rest(self, api):
        """Seed rolling candle buffers from MetaApi historical market data REST API."""
        import requests as _req
        from urllib.parse import quote as _quote

        base = "https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
        hdrs = {"auth-token": self._token}

        tf_minutes = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}

        for display, broker_sym in _SYMBOLS:
            self._buffers[display] = {}
            sym_enc = _quote(broker_sym, safe="")

            for tf_label, count in _TF_SEED.items():
                tf_str  = _TF_MAP[tf_label]
                minutes = tf_minutes[tf_label] * (count + 20)
                start   = (datetime.now(timezone.utc) - timedelta(minutes=minutes)
                           ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                url = (f"{base}/users/current/accounts/{self._account_id}"
                       f"/historical-market-data/symbols/{sym_enc}"
                       f"/timeframes/{tf_str}/candles?startTime={start}&limit={count}")
                try:
                    r = await asyncio.get_event_loop().run_in_executor(
                        None, lambda u=url: _req.get(u, headers=hdrs, timeout=30)
                    )
                    if r.ok and r.json():
                        df = self._rest_candles_to_df(r.json())
                        self._buffers[display][tf_label] = df
                        log.info("[LiveServer] Seeded %s/%s: %d bars", display, tf_label, len(df))
                    else:
                        log.warning("[LiveServer] Seed %s/%s: HTTP %s", display, tf_label, r.status_code)
                except Exception as e:
                    log.warning("[LiveServer] Seed %s/%s error: %s", display, tf_label, e)

    @staticmethod
    def _rest_candles_to_df(data: list) -> pd.DataFrame:
        rows = [
            {
                "t": pd.to_datetime(c["time"]),
                "o": float(c["open"]),
                "h": float(c["high"]),
                "l": float(c["low"]),
                "c": float(c["close"]),
                "v": float(c.get("tickVolume", 0)),
            }
            for c in data
        ]
        df = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
        return df

    # ── Buffer management (called from listener thread) ────────────────────────

    def _update_buffer(self, display: str, tf: str, candle: dict) -> bool:
        """
        Merge incoming candle into the rolling buffer.
        Returns True if a NEW bar started (= previous bar just closed).
        """
        bufs = self._buffers.setdefault(display, {})
        df   = bufs.get(tf)

        c_time = candle.get("time")
        if hasattr(c_time, "timestamp"):
            c_time = c_time           # keep as datetime for comparison
        new_row = {
            "t": c_time,
            "o": float(candle.get("open",       0)),
            "h": float(candle.get("high",       0)),
            "l": float(candle.get("low",        0)),
            "c": float(candle.get("close",      0)),
            "v": float(candle.get("tickVolume", 0)),
        }

        if df is None or len(df) == 0:
            bufs[tf] = pd.DataFrame([new_row])
            return False

        last_t = df.iloc[-1]["t"]
        if _times_equal(last_t, c_time):
            # Same bar — update OHLCV in-place (current incomplete bar)
            for col, val in [("o", new_row["o"]), ("h", new_row["h"]),
                              ("l", new_row["l"]), ("c", new_row["c"]),
                              ("v", new_row["v"])]:
                df.at[df.index[-1], col] = val
            return False
        else:
            # New bar opened — previous bar is now closed; append and cap the window
            new_df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            bufs[tf] = new_df.tail(500).reset_index(drop=True)
            log.debug("[LiveServer] %s/%s bar closed → %d bars", display, tf, len(bufs[tf]))
            return True

    # ── Indicator computation ──────────────────────────────────────────────────

    def _compute_all(self):
        """Recompute indicators for every symbol/timeframe and update self._latest."""
        from core.analyzer import MarketAnalyzer
        ma = MarketAnalyzer(use_claude=False)

        for display, _ in _SYMBOLS:
            bufs = self._buffers.get(display, {})
            if not bufs:
                continue
            timeframes: dict = {}
            for tf in ("M15", "H1", "H4", "D1"):
                df = bufs.get(tf)
                if df is not None and len(df) >= 30:
                    try:
                        timeframes[tf] = ma._compute_indicators(df)
                    except Exception as e:
                        log.debug("[LiveServer] Indicator %s/%s: %s", display, tf, e)

            if not timeframes:
                continue

            indicators = timeframes.get("M15", next(iter(timeframes.values())))
            self._latest[display] = {
                "indicators": indicators,
                "timeframes": timeframes,
                "_last_update": time.time(),
            }

    # ── WebSocket client handler ───────────────────────────────────────────────

    async def _handle_client(self, ws):
        self._clients.add(ws)
        log.info("[LiveServer] Client connected (%d total)", len(self._clients))

        if self._latest:
            try:
                await ws.send(json.dumps({
                    "type": "snapshot",
                    "data": {"session": self._session(), "symbols": dict(self._latest)},
                }, default=str))
            except Exception:
                pass

        try:
            async for _ in ws:
                pass   # server-push only; no incoming messages expected
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            log.info("[LiveServer] Client disconnected (%d remaining)", len(self._clients))

    # ── Broadcast ─────────────────────────────────────────────────────────────

    def _broadcast_sync(self, msg: dict):
        """Schedule a broadcast from the synchronization listener (sync) thread."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._broadcast_async(json.dumps(msg, default=str)), self._loop
            )

    async def _broadcast_async(self, data: str):
        dead: set = set()
        for ws in list(self._clients):
            try:
                await ws.send(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._clients.discard(ws)

    @staticmethod
    def _session() -> str:
        h = datetime.now(timezone.utc).hour
        if  8 <= h < 13: return "LONDON"
        if 13 <= h < 17: return "LONDON_NY_OVERLAP"
        if 17 <= h < 22: return "NEW_YORK"
        return "ASIAN"


# ── Time comparison helper ─────────────────────────────────────────────────────

def _times_equal(a, b) -> bool:
    """Compare two timestamps that may be datetime objects or strings."""
    if a is None or b is None:
        return False
    try:
        ta = a.timestamp() if hasattr(a, "timestamp") else float(str(a))
        tb = b.timestamp() if hasattr(b, "timestamp") else float(str(b))
        return abs(ta - tb) < 1.0    # same second = same bar
    except Exception:
        return str(a) == str(b)


# ── Singleton ──────────────────────────────────────────────────────────────────

_server: Optional[LiveIndicatorServer] = None
_lock   = threading.Lock()


def start_live_server(
    token:      str = None,
    account_id: str = None,
    port:       int = PORT,
) -> LiveIndicatorServer:
    """Start the live indicator server in the background. Idempotent."""
    global _server
    with _lock:
        if _server is None:
            _server = LiveIndicatorServer(token, account_id, port)
            _server.start()
    return _server


def get_live_server() -> Optional[LiveIndicatorServer]:
    return _server


# ── Standalone entry-point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-14s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    _tok = os.environ.get("METAAPI_TOKEN", "")
    _acc = os.environ.get("METAAPI_ACCOUNT_ID", "")
    if not _tok or not _acc:
        print("Usage: METAAPI_TOKEN=xxx METAAPI_ACCOUNT_ID=yyy python3 bridges/live_server.py")
        sys.exit(1)

    print(f"Starting live indicator server on ws://0.0.0.0:{PORT}")
    print("Press Ctrl+C to stop.")

    server = LiveIndicatorServer(_tok, _acc, PORT)
    server._running = True
    try:
        asyncio.run(server._async_main())
    except KeyboardInterrupt:
        print("\nStopped.")
