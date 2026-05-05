"""
tv_server.py — Self-hosted local indicator WebSocket server (port 8887).

Fetches broker candles every 30 seconds, computes multi-timeframe indicators
using the same logic as MarketAnalyzer, and streams them via WebSocket to
tv_client.py consumers.

This replaces the external TradingView indicator server. The bot now has its
own always-on indicator feed — no external dependency required.

Wire-up in continuous_trader.py:
    from bridges.tv_server import start_tv_server
    start_tv_server(bridge)   # starts in background, idempotent
"""
from __future__ import annotations

import asyncio
import json
from core.logger_factory import get_logger
import threading
import time
from typing import Optional

log = get_logger("tv_server")

PORT          = 8887
_REFRESH_SECS = 30                                    # candle fetch interval
_TF_CANDLES   = {"M15": 300, "H1": 200, "H4": 100, "D1": 60}
_SYMBOLS      = [("XAUUSD", "GOLD.i#"), ("XAGUSD", "SILVER.i#")]


class TVIndicatorServer:
    """
    Local WebSocket server that computes and streams live indicators.

    Protocol (matches what tv_client.py expects):
      snapshot       → {"type": "snapshot",        "data": {"session": ..., "symbols": {...}}}
      indicator_update → {"type": "indicator_update", "symbol": ..., "indicators": {...}, "timeframes": {...}}
    """

    def __init__(self, bridge, port: int = PORT):
        self._bridge  = bridge
        self._port    = port
        self._clients: set = set()
        self._latest:  dict = {}          # display_symbol → {indicators, timeframes, _last_update}
        self._running  = False
        self._loop:    Optional[asyncio.AbstractEventLoop] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        """Start the WS server + fetch loop in a background daemon thread."""
        self._running = True
        t = threading.Thread(target=self._run_in_thread, daemon=True, name="tv-server")
        t.start()
        log.info("[TVServer] Starting on ws://localhost:%d", self._port)

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def is_ready(self) -> bool:
        """True once at least one symbol has a full indicator set."""
        return bool(self._latest)

    # ── Thread entry ───────────────────────────────────────────────────────────

    def _run_in_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            log.error("[TVServer] Fatal: %s", e)

    async def _async_main(self):
        try:
            import websockets
            fetch_task = asyncio.create_task(self._fetch_loop())
            async with websockets.serve(self._handle_client, "0.0.0.0", self._port):
                log.info("[TVServer] Listening on ws://0.0.0.0:%d", self._port)
                await fetch_task          # runs until self._running is False
        except OSError as e:
            log.error("[TVServer] Cannot bind :%d — %s (is another instance running?)", self._port, e)

    # ── Client handler ──────────────────────────────────────────────────────────

    async def _handle_client(self, ws):
        self._clients.add(ws)
        log.info("[TVServer] Client connected  (%d total)", len(self._clients))

        # Push current snapshot immediately so the client doesn't have to wait
        if self._latest:
            snap = {
                "type": "snapshot",
                "data": {"session": self._session(), "symbols": dict(self._latest)},
            }
            try:
                await ws.send(json.dumps(snap, default=str))
            except Exception as e:
                try: log.debug(f'Caught exception: {e}')
                except: pass
                pass

        try:
            async for _ in ws:
                pass           # server-push only; ignore incoming messages
        except Exception as e:
            try: log.debug(f'Caught exception: {e}')
            except: pass
            pass
        finally:
            self._clients.discard(ws)
            log.info("[TVServer] Client disconnected (%d remaining)", len(self._clients))

    # ── Fetch loop ──────────────────────────────────────────────────────────────

    async def _fetch_loop(self):
        await asyncio.sleep(3)                  # let server bind before first fetch
        while self._running:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._refresh_all)
            except Exception as e:
                log.warning("[TVServer] Refresh error: %s", e)
            await asyncio.sleep(_REFRESH_SECS)

    def _refresh_all(self):
        """Blocking: fetch candles + compute indicators for every symbol/timeframe."""
        from core.analyzer import MarketAnalyzer
        ma = MarketAnalyzer(use_ai=False)

        updated: dict = {}
        for display, broker_sym in _SYMBOLS:
            timeframes: dict = {}
            candles_by_tf: dict = {}
            for tf, count in _TF_CANDLES.items():
                try:
                    df = self._bridge.get_candles(broker_sym, tf, count)
                    if df is not None and len(df) >= 30:
                        timeframes[tf] = ma._compute_indicators(df)
                        candles_by_tf[tf] = self._serialize_candles(df.tail(120))
                except Exception as e:
                    log.debug("[TVServer] %s/%s candle error: %s", display, tf, e)

            if not timeframes:
                log.warning("[TVServer] %s: no candle data — skipping this cycle", display)
                continue

            indicators = timeframes.get("M15", next(iter(timeframes.values())))
            self._latest[display] = {
                "indicators": indicators,
                "timeframes": timeframes,
                "candles": candles_by_tf,
                "_last_update": time.time(),
            }
            updated[display] = self._latest[display]

        if updated:
            _adx = self._latest.get("XAUUSD", {}).get("indicators", {}).get("adx", "?")
            _rsi = self._latest.get("XAUUSD", {}).get("indicators", {}).get("rsi", "?")
            log.info("[TVServer] Indicators refreshed — XAUUSD ADX=%s RSI=%s", _adx, _rsi)
            self._publish_supabase(updated)
            # Persist snapshot to disk so dashboard /api/live-snapshot can read it
            try:
                from core.paths import STATE_DIR
                snap_file = STATE_DIR / "live_snapshot.json"
                snap_file.write_text(json.dumps({
                    "session": self._session(),
                    "symbols": dict(self._latest),
                    "_updated": time.time(),
                }, default=str))
            except Exception as e:
                try: log.debug(f'Caught exception: {e}')
                except: pass
                pass
            self._broadcast_sync({
                "type": "snapshot",
                "data": {"session": self._session(), "symbols": dict(self._latest)},
            })

    def _publish_supabase(self, updated: dict):
        """Best-effort publish of indicator snapshots to Supabase."""
        try:
            from core.supabase_db import SupabaseDB
            db = SupabaseDB()
            broker_map = dict(_SYMBOLS)
            for symbol, payload in updated.items():
                merged = dict(payload)
                merged["session"] = self._session()
                db.upsert_live_market_snapshot(
                    symbol,
                    broker_symbol=broker_map.get(symbol),
                    payload=merged,
                    source="tv_server",
                )
            db.log_live_event(
                "indicator_snapshot",
                {"symbols": list(updated.keys()), "session": self._session()},
                source="tv_server",
            )
        except Exception as e:
            log.debug("[TVServer] Supabase publish skipped: %s", e)

    @staticmethod
    def _serialize_candles(df):
        candles = []
        for row in df.to_dict("records"):
            ts = row.get("time")
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            candles.append({
                "time": ts,
                "o": row.get("o"),
                "h": row.get("h"),
                "l": row.get("l"),
                "c": row.get("c"),
                "vol": row.get("vol"),
            })
        return candles

    # ── Broadcast ───────────────────────────────────────────────────────────────

    def _broadcast_sync(self, msg: dict):
        """Schedule a broadcast from a synchronous (non-async) context."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._broadcast_async(json.dumps(msg, default=str)), self._loop
            )

    async def _broadcast_async(self, data: str):
        dead: set = set()
        for ws in list(self._clients):
            try:
                await ws.send(data)
            except Exception as e:
                try: log.debug(f'Caught exception: {e}')
                except: pass
                dead.add(ws)
        for ws in dead:
            self._clients.discard(ws)

    # ── Helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _session() -> str:
        from datetime import datetime, timezone
        h = datetime.now(timezone.utc).hour
        if  8 <= h < 13: return "LONDON"
        if 13 <= h < 17: return "LONDON_NY_OVERLAP"
        if 17 <= h < 22: return "NEW_YORK"
        return "ASIAN"


# ── Singleton ──────────────────────────────────────────────────────────────────

_server: Optional[TVIndicatorServer] = None
_server_lock = threading.Lock()


def start_tv_server(bridge, port: int = PORT) -> TVIndicatorServer:
    """Start the local indicator server in the background. Safe to call multiple times."""
    global _server
    with _server_lock:
        if _server is None:
            _server = TVIndicatorServer(bridge, port)
            _server.start()
    return _server


def get_tv_server() -> Optional[TVIndicatorServer]:
    return _server
