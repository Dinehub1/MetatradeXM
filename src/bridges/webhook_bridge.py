"""
WebhookBridge — HTTP bridge to Windows MT5 webhook server.

Drop-in replacement for MetaApiBridge — same public method signatures.
Connects to the Flask server running on the Windows machine where MT5 is installed.

Config:
    WIN_WEBHOOK_URL=http://<windows-ip>:5001

The webhook server (win_webhook_mt5.py) must be running on the Windows machine.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
import requests
from urllib.parse import quote
import pandas as pd
from datetime import datetime, timezone
from types import SimpleNamespace
from dotenv import load_dotenv

# Load .env from project root (two levels up from src/bridges/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger("webhook_bridge")

# Timeframe mapping for display
_TF_MAP = {
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "H1", "H4": "H4", "D1": "D1", "W1": "W1",
}

# Default request timeout (seconds)
_TIMEOUT = 15


class WebhookBridge:
    """
    HTTP bridge to Windows MT5 webhook server.
    Drop-in replacement for MetaApiBridge — same public method signatures.
    """

    def __init__(self, url: str = None):
        self.url = (url or os.environ.get("WIN_WEBHOOK_URL", "")).rstrip("/")
        self._validate_url()  # SSRF prevention: validate URL at startup
        self.connected = False
        self._account_cache = None
        self._cache_ts = 0

        # Auth session — all requests automatically include the Bearer token
        auth_token = os.environ.get("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {auth_token}"})

    def _validate_url(self):
        """Validate webhook URL for SSRF prevention."""
        if not self.url:
            log.error("[WEBHOOK] WIN_WEBHOOK_URL not configured")
            return

        # Only allow http/https protocols
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"[WEBHOOK] Invalid URL scheme. Only http/https allowed: {self.url[:20]}")

        # Whitelist: localhost, 127.0.0.1, or private IP ranges (optional)
        # For now, we allow any http/https but log a warning for non-localhost
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        hostname = parsed.hostname or ""

        if hostname not in ("localhost", "127.0.0.1"):
            log.info(f"[WEBHOOK] Connecting to non-localhost server: {hostname} (ensure it's trusted)")

        # Reject suspicious hostnames
        suspicious = ["metadata.google", "169.254.169.254", "internal", "admin"]
        if any(s in hostname.lower() for s in suspicious):
            raise ValueError(f"[WEBHOOK] Suspicious hostname detected: {hostname}")

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Test connection to the Windows webhook server."""
        if not self.url:
            log.error("[WEBHOOK] WIN_WEBHOOK_URL not configured")
            return False
        try:
            resp = self._session.get(f"{self.url}/status", timeout=10)
            data = resp.json()
            if data.get("terminal_connected"):
                login = data.get("login", "?")
                server = data.get("server", "?")
                log.info(f"  ✅ Windows MT5 connected (login={login} server={server})")
                self.connected = True
                return True
            else:
                log.warning("[WEBHOOK] MT5 terminal not connected on Windows side")
                return False
        except requests.exceptions.ConnectionError:
            log.error(f"[WEBHOOK] Cannot reach Windows webhook at {self.url}")
            return False
        except Exception as e:
            log.error(f"[WEBHOOK] Connection error: {e}")
            return False

    def disconnect(self):
        """No persistent connection to close — just reset state."""
        self.connected = False

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame | None:
        """Fetch historical OHLCV candles from MT5 via webhook."""
        tf = _TF_MAP.get(timeframe, timeframe)
        try:
            resp = self._session.get(
                f"{self.url}/candles",
                params={"symbol": symbol, "tf": tf, "count": count},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                log.warning(f"[WEBHOOK] Candles {resp.status_code} for {symbol}/{tf}")
                return None

            data = resp.json()
            candles = data.get("candles", [])
            if not candles:
                return None

            df = pd.DataFrame(candles)
            # Convert epoch time to datetime
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            # Ensure expected column order
            for col in ("o", "h", "l", "c", "vol"):
                if col not in df.columns:
                    return None

            log.debug(f"[WEBHOOK] Got {len(df)} candles for {symbol}/{tf}")
            return df

        except requests.exceptions.ConnectionError:
            log.warning(f"[WEBHOOK] Cannot reach Windows server for candles")
            return None
        except Exception as e:
            log.warning(f"[WEBHOOK] get_candles error: {e}")
            return None

    def get_tick(self, symbol: str):
        """Get current tick (ask/bid/last) for a symbol."""
        try:
            resp = self._session.get(f"{self.url}/tick", params={"symbol": symbol}, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            return SimpleNamespace(
                ask=data["ask"],
                bid=data["bid"],
                last=data.get("last", data["ask"]),
                time=data.get("time", 0),
                received_at=time.time(),  # Mac-local arrival time (no clock-drift bias)
            )
        except Exception as e:
            log.warning(f"[WEBHOOK] get_tick error: {e}")
            return None

    def get_symbol_info(self, symbol: str):
        """Get symbol specifications."""
        try:
            resp = self._session.get(f"{self.url}/symbol", params={"symbol": symbol}, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            return SimpleNamespace(
                name=data.get("name", symbol),
                digits=data.get("digits", 5),
                trade_contract_size=data.get("trade_contract_size", 100_000),
                volume_min=data.get("volume_min", 0.01),
                volume_max=data.get("volume_max", 100.0),
                volume_step=data.get("volume_step", 0.01),
                point=data.get("point", 0.00001),
            )
        except Exception:
            return None

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_info(self):
        """Get account info (balance, equity, margin, etc)."""
        try:
            resp = self._session.get(f"{self.url}/account", timeout=_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            return SimpleNamespace(
                login=data.get("login", ""),
                server=data.get("server", ""),
                balance=data.get("balance", 0),
                equity=data.get("equity", 0),
                margin=data.get("margin", 0),
                margin_free=data.get("margin_free", 0),
                leverage=data.get("leverage", 100),
                currency=data.get("currency", "USD"),
            )
        except Exception as e:
            log.warning(f"[WEBHOOK] get_account_info error: {e}")
            return None

    def print_account_info(self):
        info = self.get_account_info()
        if not info:
            log.warning("  Could not fetch account info.")
            return
        log.info(f"  Account:  #{info.login}  ({info.server})")
        log.info(f"  Balance:  {info.balance:.2f} {info.currency}")
        log.info(f"  Equity:   {info.equity:.2f}")
        log.info(f"  Margin:   {info.margin:.2f}  |  Free: {info.margin_free:.2f}")
        log.info(f"  Leverage: 1:{info.leverage}")

    # ── Positions ────────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: str = None) -> list:
        """Get all open positions (optionally filtered by symbol)."""
        try:
            url = f"{self.url}/positions/{quote(symbol, safe='')}" if symbol else f"{self.url}/positions"
            resp = self._session.get(url, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []

            data = resp.json()
            positions = data.get("positions", [])
            result = []
            for p in positions:
                result.append(SimpleNamespace(
                    ticket=p.get("ticket", 0),
                    symbol=p.get("symbol", ""),
                    type=p.get("type_id", 0),  # 0=BUY, 1=SELL
                    volume=p.get("volume", 0),
                    price_open=p.get("price_open", 0),
                    sl=p.get("sl", 0),
                    tp=p.get("tp", 0),
                    profit=p.get("profit", 0),
                    comment=p.get("comment", ""),
                    time=p.get("time", 0),              # Unix open-time (seconds) — needed by SmartExit
                    time_update=p.get("time_update", 0),
                ))
            return result
        except Exception as e:
            log.warning(f"[WEBHOOK] get_open_positions error: {e}")
            return []

    def print_open_positions(self):
        positions = self.get_open_positions()
        if not positions:
            log.info("  No open positions.")
            return
        log.info(f"  Open positions ({len(positions)}):")
        for p in positions:
            direction = "BUY" if p.type == 0 else "SELL"
            log.info(
                f"  #{p.ticket}  {p.symbol}  {direction}  {p.volume} lots  "
                f"open@{p.price_open:.5f}  profit:{p.profit:.2f}"
            )

    # ── Orders ───────────────────────────────────────────────────────────────

    def place_order(self, params: dict):
        """Place a market order via the webhook."""
        try:
            payload = {
                "action": params["direction"],
                "symbol": params["symbol"],
                "lot": params["lot"],
                "comment": params.get("comment", "AI-Bot"),
            }
            if params.get("sl") is not None:
                payload["sl"] = params["sl"]
            if params.get("tp") is not None:
                payload["tp"] = params["tp"]

            resp = self._session.post(
                f"{self.url}/webhook",
                json=payload,
                timeout=_TIMEOUT,
            )
            data = resp.json()

            if data.get("status") == "success":
                return SimpleNamespace(
                    order=data.get("ticket", 0),
                    retcode=10009,  # MT5 success code
                )
            else:
                log.error(f"[WEBHOOK] Order failed: {data.get('message')}")
                return None

        except Exception as e:
            log.error(f"[WEBHOOK] place_order error: {e}")
            return None

    def place_limit_order(self, params: dict):
        """Place a limit order via the webhook."""
        try:
            payload = {
                "action": f"LIMIT_{params['direction']}",  # LIMIT_BUY or LIMIT_SELL
                "symbol": params["symbol"],
                "lot": params["lot"],
                "price": params["price"],
                "comment": params.get("comment", "AI-Bot-Limit"),
            }
            if params.get("sl") is not None:
                payload["sl"] = params["sl"]
            if params.get("tp") is not None:
                payload["tp"] = params["tp"]

            resp = self._session.post(
                f"{self.url}/webhook",
                json=payload,
                timeout=_TIMEOUT,
            )
            data = resp.json()

            if data.get("status") == "success":
                return SimpleNamespace(
                    order=data.get("ticket", 0),
                    retcode=10009,
                )
            else:
                log.error(f"[WEBHOOK] Limit order failed: {data.get('message')}")
                return None

        except Exception as e:
            log.error(f"[WEBHOOK] place_limit_order error: {e}")
            return None

    def close_position(self, ticket, volume=None):
        """Close a position by ticket."""
        try:
            # We need the symbol — get it from positions
            positions = self.get_open_positions()
            pos = next((p for p in positions if str(p.ticket) == str(ticket)), None)
            if not pos:
                log.warning(f"[WEBHOOK] Position {ticket} not found for close")
                return False

            payload = {
                "action": "CLOSE",
                "symbol": pos.symbol,
                "ticket": int(ticket),
            }
            resp = self._session.post(f"{self.url}/webhook", json=payload, timeout=_TIMEOUT)
            data = resp.json()
            return data.get("status") == "success"

        except Exception as e:
            log.error(f"[WEBHOOK] close_position error: {e}")
            return False

    def close_position_partial(self, ticket, volume):
        """Partially close a position by the specified volume.

        Sends CLOSE_PARTIAL to the Windows webhook server.
        Falls back to full close if the server doesn't support partial closes.
        """
        try:
            # Find the position to get its symbol
            positions = self.get_open_positions()
            pos = None
            for p in (positions or []):
                if str(getattr(p, "ticket", "")) == str(ticket):
                    pos = p
                    break
            if pos is None:
                log.warning(f"[WEBHOOK] Position {ticket} not found for partial close")
                return False

            payload = {
                "action": "CLOSE_PARTIAL",
                "symbol": pos.symbol,
                "ticket": int(ticket),
                "close_volume": round(float(volume), 2),
            }
            resp = self._session.post(f"{self.url}/webhook", json=payload, timeout=_TIMEOUT)
            data = resp.json()

            if data.get("status") == "success":
                log.info(f"[WEBHOOK] Partial close OK: #{ticket} vol={volume}")
                return True

            # Fall back to full close if server doesn't support partial
            if resp.status_code == 400 and "CLOSE_PARTIAL" in data.get("message", ""):
                log.warning("[WEBHOOK] Server doesn't support CLOSE_PARTIAL — falling back to full close")
                return self.close_position(ticket)

            log.warning(f"[WEBHOOK] Partial close failed: {data.get('message')}")
            return False

        except Exception as e:
            log.error(f"[WEBHOOK] close_position_partial error: {e}")
            return False

    def modify_position(self, ticket, sl=None, tp=None) -> bool:
        """Modify SL/TP of an open position."""
        try:
            payload = {"ticket": int(ticket)}
            if sl is not None:
                payload["sl"] = sl
            if tp is not None:
                payload["tp"] = tp

            resp = self._session.post(f"{self.url}/modify", json=payload, timeout=_TIMEOUT)
            data = resp.json()
            return data.get("status") == "success"

        except Exception as e:
            log.warning(f"[WEBHOOK] modify_position error: {e}")
            return False

    # ── Trade History ────────────────────────────────────────────────────────

    def get_trade_history(self, days: int = 7) -> dict:
        """Get closed trade history for the past N days."""
        try:
            resp = self._session.get(
                f"{self.url}/history",
                params={"days": days},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return {}
            return resp.json()
        except Exception as e:
            log.warning(f"[WEBHOOK] get_trade_history error: {e}")
            return {}

    # ── MT5-Computed Indicators ──────────────────────────────────────────────

    def get_indicators(self, symbol: str, timeframes: str = "M1,M15,H1,H4,D1") -> dict:
        """Fetch computed technical indicators from Windows MT5 webhook.
        
        Returns dict of timeframe -> indicator values (RSI, MACD, ADX, BB, etc.)
        computed directly from MT5 candle data on the Windows side.
        
        Args:
            symbol: MT5 symbol (e.g. "GOLD.i#", "SILVER.i#")
            timeframes: comma-separated TF list
        
        Returns:
            Dict with timeframe keys, each containing indicator values.
            Empty dict on failure.
        """
        try:
            resp = self._session.get(
                f"{self.url}/indicators",
                params={"symbol": symbol, "tf": timeframes},
                timeout=_TIMEOUT + 5,  # indicator computation needs more time
            )
            if resp.status_code != 200:
                log.warning(f"[WEBHOOK] get_indicators {symbol}: HTTP {resp.status_code}")
                return {}
            data = resp.json()
            return data.get("timeframes", {})
        except requests.exceptions.Timeout:
            log.warning(f"[WEBHOOK] get_indicators {symbol}: timeout")
            return {}
        except Exception as e:
            log.warning(f"[WEBHOOK] get_indicators error: {e}")
            return {}
