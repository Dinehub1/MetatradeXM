"""
tv_client.py — TradingView WebSocket client singleton.

Maintains a persistent connection to the local indicator server (port 8887)
and exposes the latest multi-timeframe indicator snapshot to the rest of the bot.

Usage:
    from tv_client import get_tv_client
    tv = get_tv_client()          # starts background thread on first call
    data = tv.get("XAUUSD")       # returns latest indicators dict or None
    snap = tv.snapshot()          # full snapshot for all symbols
"""
from __future__ import annotations

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

import os
_WS_URL    = os.environ.get("TV_WS_URL", "ws://localhost:8887")
_RECONNECT = 30   # seconds between reconnect attempts (don't spam on failure)


class TradingViewClient:
    def __init__(self, url: str = _WS_URL):
        self._url     = url
        self._data    = {}          # symbol -> {"indicators": {...}, "timeframes": {...}}
        self._session = None        # last received session label
        self._lock    = threading.Lock()
        self._running = False
        self._thread  = None
        self._connected = False
        self._first_error_logged = False  # only log connection error once visibly

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, symbol: str) -> dict | None:
        """Return latest indicator dict for a symbol (primary timeframe = M15)."""
        with self._lock:
            sym = self._data.get(symbol)
            if sym:
                return sym.get("indicators") or sym.get("M15") or None
            return None

    def get_tf(self, symbol: str, timeframe: str = "M15") -> dict | None:
        """Return indicators for a specific timeframe."""
        with self._lock:
            sym = self._data.get(symbol)
            if not sym:
                return None
            return sym.get("timeframes", {}).get(timeframe) or sym.get(timeframe)

    def snapshot(self) -> dict:
        """Return full snapshot: {session, symbols: {XAUUSD: {...}, XAGUSD: {...}}}"""
        with self._lock:
            return {"session": self._session, "symbols": dict(self._data)}

    def is_connected(self) -> bool:
        return self._connected

    def wait_for_data(self, symbol: str = "XAUUSD", timeout: float = 15.0) -> bool:
        """Block until data arrives for symbol or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get(symbol) is not None:
                return True
            time.sleep(0.2)
        return False

    # ── Internal WebSocket loop ───────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("[TV] TradingView client started → %s", self._url)

    def stop(self):
        self._running = False

    def _run_loop(self):
        while self._running:
            try:
                self._connect_and_read()
            except Exception as e:
                if not self._first_error_logged:
                    logger.info("[TV] Not available — will retry in background")
                    self._first_error_logged = True
                else:
                    logger.debug("[TV] Reconnect failed: %s", e)
                self._connected = False
            if self._running:
                time.sleep(_RECONNECT)

    def _connect_and_read(self):
        # Use the websocket-client library (already installed)
        import websocket as _ws_lib

        ws = _ws_lib.WebSocketApp(
            self._url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.run_forever(ping_interval=25, ping_timeout=10)

    def _on_open(self, ws):
        self._connected = True
        logger.info("[TV] Connected to %s", self._url)

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.debug("[TV] Connection closed (%s %s)", code, msg)

    def _on_error(self, ws, err):
        # Only log first error visibly — rest at DEBUG to avoid log spam
        if not self._first_error_logged:
            logger.info("[TV] Not available — will retry in background")
            self._first_error_logged = True
        else:
            logger.debug("[TV] Error: %s", err)

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
            mtype = msg.get("type")
            now = time.time()

            if mtype == "snapshot":
                data = msg.get("data", {})
                with self._lock:
                    self._session = data.get("session")
                    self._data    = data.get("symbols", {})
                    for sym in self._data.values():
                        if isinstance(sym, dict):
                            sym["_last_update"] = now
                logger.debug("[TV] Snapshot received for %d symbols", len(self._data))

            elif mtype == "indicator_update":
                symbol    = msg.get("symbol")
                indicators = msg.get("indicators")
                timeframes = msg.get("timeframes")
                if symbol:
                    with self._lock:
                        existing = self._data.setdefault(symbol, {})
                        if indicators:
                            existing["indicators"] = indicators
                        if timeframes:
                            existing["timeframes"] = timeframes
                        existing["_last_update"] = now
                        if not self._session:
                            self._session = msg.get("session")
                logger.debug("[TV] Update: %s ADX=%.1f",
                             symbol,
                             (indicators or {}).get("adx", 0) or 0)

        except Exception as e:
            logger.debug("[TV] Message parse error: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: TradingViewClient | None = None
_lock = threading.Lock()


def get_tv_client() -> TradingViewClient:
    global _instance
    with _lock:
        if _instance is None:
            _instance = TradingViewClient()
            _instance.start()
    return _instance
