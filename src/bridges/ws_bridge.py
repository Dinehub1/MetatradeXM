"""
ws_bridge.py — WebSocket Client Bridge to Windows MT5 Server.

Receives real-time market data (ticks, candles, positions, account) via
WebSocket from the Windows VM running win_webhook_mt5.py.
Delegates trade execution to WebhookBridge (HTTP POST).

Drop-in replacement for WebhookBridge — same public method signatures.

Config:
    WIN_WS_URL=ws://<windows-ip>:5002        # real-time data stream
    WIN_WEBHOOK_URL=http://<windows-ip>:5001  # trade execution (HTTP)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, Optional

import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
log = logging.getLogger("ws_bridge")
AUTH_TOKEN = os.environ.get("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")

# Try to import websockets (async) — fall back to websocket-client (sync)
try:
    import websocket as _ws_client  # websocket-client library (sync)
    _HAS_WS_CLIENT = True
except ImportError:
    _HAS_WS_CLIENT = False
    log.warning("[WS_BRIDGE] websocket-client not installed — pip install websocket-client")


class WSBridge:
    """
    WebSocket client that receives real-time data from Windows MT5.
    Uses WebhookBridge for trade execution (HTTP).
    Same public method signatures as WebhookBridge — drop-in replacement.
    """

    def __init__(self, ws_url: str = None, http_url: str = None):
        self.ws_url = (ws_url or os.environ.get("WIN_WS_URL", "")).rstrip("/")
        self.http_url = (http_url or os.environ.get("WIN_WEBHOOK_URL", "")).rstrip("/")
        self.connected = False

        # HTTP bridge for trade execution (BUY/SELL/CLOSE/MODIFY)
        from bridges.webhook_bridge import WebhookBridge
        self._webhook = WebhookBridge(self.http_url)

        # ── Real-time data caches (populated by WebSocket) ───────────────
        self._latest_ticks: Dict[str, dict] = {}       # symbol → {bid, ask, time}
        self._candle_buffers: Dict[str, Dict[str, pd.DataFrame]] = {}  # symbol → tf → DataFrame
        self._positions: list = []
        self._account: Optional[dict] = None

        # ── WebSocket connection state ───────────────────────────────────
        self._ws: Optional[object] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_connected = False
        self._ws_reconnect_count = 0
        self._ws_max_reconnect = 999999  # effectively unlimited — never give up
        self._ws_last_message_time = 0
        self._stop_event = threading.Event()

        # Lock for thread-safe cache access
        self._lock = threading.Lock()

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Connect both WebSocket (data) and HTTP (execution) bridges."""
        # First verify HTTP endpoint is reachable
        http_ok = self._webhook.connect()
        if not http_ok:
            log.error("[WS_BRIDGE] HTTP webhook connection failed")
            return False

        # Start WebSocket listener in background thread
        if self.ws_url and _HAS_WS_CLIENT:
            self._start_ws_listener()
            # Give WebSocket a moment to connect
            time.sleep(2)
            if self._ws_connected:
                log.info(f"[WS_BRIDGE] ✅ WebSocket connected to {self.ws_url}")
            else:
                log.warning(
                    f"[WS_BRIDGE] ⚠️ WebSocket not yet connected to {self.ws_url} "
                    f"— falling back to HTTP polling for data"
                )
        else:
            if not self.ws_url:
                log.info("[WS_BRIDGE] No WIN_WS_URL configured — HTTP-only mode")
            elif not _HAS_WS_CLIENT:
                log.warning("[WS_BRIDGE] websocket-client not installed — HTTP-only mode")

        self.connected = True
        return True

    def disconnect(self):
        """Shut down WebSocket listener and HTTP bridge."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._ws_connected = False
        self.connected = False
        self._webhook.disconnect()

    def _start_ws_listener(self):
        """Start WebSocket listener in a background daemon thread."""
        if self._ws_thread and self._ws_thread.is_alive():
            return  # already running

        self._stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._ws_listen_loop,
            name="ws-bridge-listener",
            daemon=True,
        )
        self._ws_thread.start()

    def _ws_listen_loop(self):
        """Reconnecting WebSocket listener loop."""
        while not self._stop_event.is_set():
            try:
                log.info(f"[WS_BRIDGE] Connecting to {self.ws_url}...")
                self._ws = _ws_client.WebSocketApp(
                    self.ws_url,
                    header=[f"Authorization: Bearer {AUTH_TOKEN}"],
                    on_open=self._on_ws_open,
                    on_message=self._on_ws_message,
                    on_error=self._on_ws_error,
                    on_close=self._on_ws_close,
                )
                self._ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10,
                    reconnect=0,  # we handle reconnect ourselves
                )
            except Exception as e:
                log.warning(f"[WS_BRIDGE] WebSocket error: {e}")

            self._ws_connected = False

            if self._stop_event.is_set():
                break

            # Reconnect backoff
            self._ws_reconnect_count += 1
            if self._ws_reconnect_count > self._ws_max_reconnect:
                log.error("[WS_BRIDGE] Max reconnect attempts reached — giving up")
                break

            backoff = min(self._ws_reconnect_count * 2, 30)
            log.info(f"[WS_BRIDGE] Reconnecting in {backoff}s (attempt {self._ws_reconnect_count})")
            self._stop_event.wait(backoff)

    def _on_ws_open(self, ws):
        log.info("[WS_BRIDGE] ✅ WebSocket connection established")
        self._ws_connected = True
        self._ws_reconnect_count = 0  # reset on successful connection

    def _on_ws_message(self, ws, message):
        """Process incoming WebSocket messages and update caches."""
        self._ws_last_message_time = time.time()
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")

            with self._lock:
                if msg_type == "tick":
                    symbol = data.get("symbol", "")
                    self._latest_ticks[symbol] = {
                        "bid": data.get("bid", 0),
                        "ask": data.get("ask", 0),
                        "last": data.get("last", data.get("ask", 0)),
                        "time": data.get("time", 0),
                        "spread": data.get("spread", 0),
                    }

                elif msg_type == "candles":
                    symbol = data.get("symbol", "")
                    tf = data.get("timeframe", "")
                    candles = data.get("candles", [])
                    if symbol and tf and candles:
                        df = pd.DataFrame(candles)
                        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                        self._candle_buffers.setdefault(symbol, {})[tf] = df

                elif msg_type == "positions":
                    self._positions = data.get("positions", [])

                elif msg_type == "account":
                    self._account = data

                elif msg_type == "bar_close":
                    # A new candle closed — update the specific timeframe buffer
                    symbol = data.get("symbol", "")
                    tf = data.get("timeframe", "")
                    candle = data.get("candle")
                    if symbol and tf and candle:
                        self._append_candle(symbol, tf, candle)

                elif msg_type == "heartbeat":
                    pass  # just update last_message_time

                else:
                    log.debug(f"[WS_BRIDGE] Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            log.debug(f"[WS_BRIDGE] Invalid JSON: {message[:100]}")
        except Exception as e:
            log.debug(f"[WS_BRIDGE] Message handling error: {e}")

    def _on_ws_error(self, ws, error):
        log.warning(f"[WS_BRIDGE] WebSocket error: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        log.info(f"[WS_BRIDGE] WebSocket closed (code={close_status_code})")
        self._ws_connected = False

    def _append_candle(self, symbol: str, tf: str, candle: dict):
        """Append a new closed candle to the buffer DataFrame."""
        buffers = self._candle_buffers.setdefault(symbol, {})
        if tf not in buffers:
            return  # no buffer yet for this TF

        new_row = pd.DataFrame([{
            "time": pd.to_datetime(candle["time"], unit="s", utc=True),
            "o": candle.get("o", candle.get("open", 0)),
            "h": candle.get("h", candle.get("high", 0)),
            "l": candle.get("l", candle.get("low", 0)),
            "c": candle.get("c", candle.get("close", 0)),
            "vol": candle.get("vol", candle.get("volume", 0)),
        }])
        buffers[tf] = pd.concat([buffers[tf], new_row], ignore_index=True).tail(500)

    # ── Data Methods (read from WebSocket cache — sub-second latency) ────────

    def get_tick(self, symbol: str):
        """Get latest tick for a symbol — from WS cache or HTTP fallback."""
        with self._lock:
            cached = self._latest_ticks.get(symbol)

        # Use cache if fresh (< 5 seconds old)
        if cached and (time.time() - cached.get("time", 0)) < 5:
            return SimpleNamespace(
                ask=cached["ask"],
                bid=cached["bid"],
                last=cached.get("last", cached["ask"]),
                time=cached.get("time", 0),
            )

        # Fallback to HTTP
        return self._webhook.get_tick(symbol)

    def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame | None:
        """Get candles — from WS cache if available, else HTTP fallback."""
        with self._lock:
            sym_buffers = self._candle_buffers.get(symbol, {})
            cached_df = sym_buffers.get(timeframe)

        if cached_df is not None and len(cached_df) >= min(count, 30):
            return cached_df.tail(count).copy()

        # Fallback to HTTP
        return self._webhook.get_candles(symbol, timeframe, count)

    def get_symbol_info(self, symbol: str):
        """Get symbol specifications — always from HTTP (static data)."""
        return self._webhook.get_symbol_info(symbol)

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_info(self):
        """Get account info — from WS cache if fresh, else HTTP fallback."""
        with self._lock:
            cached = self._account

        if cached and (time.time() - cached.get("time", 0)) < 60:
            return SimpleNamespace(
                login=cached.get("login", ""),
                server=cached.get("server", ""),
                balance=cached.get("balance", 0),
                equity=cached.get("equity", 0),
                margin=cached.get("margin", 0),
                margin_free=cached.get("margin_free", 0),
                leverage=cached.get("leverage", 100),
                currency=cached.get("currency", "USD"),
            )

        # Fallback to HTTP
        return self._webhook.get_account_info()

    def print_account_info(self):
        self._webhook.print_account_info()

    # ── Positions ────────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: str = None) -> list:
        """Get open positions — always from HTTP (need accurate state)."""
        # Positions are critical for trade decisions — always use HTTP for accuracy
        return self._webhook.get_open_positions(symbol)

    def print_open_positions(self):
        self._webhook.print_open_positions()

    # ── Orders (delegated to HTTP webhook) ───────────────────────────────────

    def place_order(self, params: dict):
        """Place a market order via HTTP webhook."""
        return self._webhook.place_order(params)

    def place_limit_order(self, params: dict):
        """Place a limit order via HTTP webhook."""
        return self._webhook.place_limit_order(params)

    def close_position(self, ticket, volume=None):
        """Close a position via HTTP webhook."""
        return self._webhook.close_position(ticket, volume)

    def close_position_partial(self, ticket, volume):
        """Partial close via HTTP webhook."""
        return self._webhook.close_position_partial(ticket, volume)

    def modify_position(self, ticket, sl=None, tp=None) -> bool:
        """Modify SL/TP via HTTP webhook."""
        return self._webhook.modify_position(ticket, sl, tp)

    # ── Trade History ────────────────────────────────────────────────────────

    def get_trade_history(self, days: int = 7) -> dict:
        """Get closed trade history via HTTP webhook."""
        return self._webhook.get_trade_history(days)

    # ── MT5-Computed Indicators ──────────────────────────────────────────────

    def get_indicators(self, symbol: str, timeframes: str = "M1,M15,H1,H4,D1") -> dict:
        """Fetch indicators — always from HTTP (computation happens on Windows)."""
        return self._webhook.get_indicators(symbol, timeframes)

    # ── Status ───────────────────────────────────────────────────────────────

    def is_ws_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._ws_connected

    def ws_status(self) -> dict:
        """Get WebSocket connection status and stats."""
        return {
            "ws_connected": self._ws_connected,
            "ws_url": self.ws_url,
            "http_url": self.http_url,
            "last_message_age_s": round(time.time() - self._ws_last_message_time, 1)
                if self._ws_last_message_time > 0 else None,
            "cached_symbols": list(self._latest_ticks.keys()),
            "reconnect_count": self._ws_reconnect_count,
        }
