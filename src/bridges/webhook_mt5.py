"""
webhook_mt5.py — Full-featured MT5 API client via HTTP webhook.

Drop-in replacement for the `MetaTrader5` Python package.
Routes all calls to the Windows MT5 webhook server (win_webhook_mt5.py).

Capabilities:
  ✅ Connection health + auto-retry
  ✅ Account info (balance, equity, margin, leverage)
  ✅ Live tick data (bid/ask/spread)
  ✅ Symbol specifications (digits, contract size, volumes)
  ✅ Historical OHLCV candles (any timeframe)
  ✅ Multi-TF technical indicators (RSI, MACD, ADX, BB, Stoch, ATR, EMA, Williams%R)
  ✅ Open positions with P&L
  ✅ Trade history (closed deals)
  ✅ Order execution (BUY/SELL/CLOSE)
  ✅ Position modification (SL/TP)
  ✅ Terminal info
"""
import os
import time
import logging
import requests
import numpy as np
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from dotenv import load_dotenv

# Load .env from the project root (two levels up from src/bridges/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
log = logging.getLogger("webhook_mt5")

# ── Server URL ────────────────────────────────────────────────────────────────
WIN_WEBHOOK_URL = os.getenv("WIN_WEBHOOK_URL", "http://206.72.198.54:5001")
AUTH_TOKEN = os.getenv("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")
_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}

# ── MT5 Constants (match real MetaTrader5 package values) ─────────────────────
TIMEFRAME_M1  = 1
TIMEFRAME_M5  = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1  = 60
TIMEFRAME_H4  = 240
TIMEFRAME_D1  = 1440

ORDER_TYPE_BUY   = 0
ORDER_TYPE_SELL  = 1
TRADE_ACTION_DEAL   = 1
TRADE_ACTION_SLTP   = 6
ORDER_TIME_GTC      = 0
ORDER_FILLING_IOC   = 1
TRADE_RETCODE_DONE  = 10009

_TF_INT_TO_STR = {
    1: "M1", 5: "M5", 15: "M15", 30: "M30",
    60: "H1", 240: "H4", 1440: "D1"
}

# ── Connection state ──────────────────────────────────────────────────────────
_last_successful_call = 0.0
_consecutive_failures = 0
_MAX_RETRIES = 2
_TIMEOUT_SHORT = 4      # ticks, status
_TIMEOUT_MEDIUM = 10    # candles, positions
_TIMEOUT_LONG = 25      # indicators (heavy computation)

# ══════════════════════════════════════════════════════════════════════════════
#  HTTP helpers with retry + logging
# ══════════════════════════════════════════════════════════════════════════════

def _get(path, timeout=_TIMEOUT_SHORT, retries=_MAX_RETRIES):
    """GET with retry. Returns parsed JSON or None."""
    global _last_successful_call, _consecutive_failures
    url = f"{WIN_WEBHOOK_URL}{path}"
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            if r.ok:
                _last_successful_call = time.time()
                _consecutive_failures = 0
                return r.json()
            if r.status_code == 401:
                log.error(
                    "[WEBHOOK] ❌ 401 Unauthorized — WEBHOOK_AUTH_TOKEN mismatch. "
                    "Make sure .env on both Mac and Windows has the same WEBHOOK_AUTH_TOKEN."
                )
                _consecutive_failures += 1
                return None  # no point retrying auth failures
            log.warning(f"[WEBHOOK] GET {path} → HTTP {r.status_code}")
        except requests.exceptions.Timeout:
            log.warning(f"[WEBHOOK] GET {path} timeout ({timeout}s) attempt {attempt}/{retries}")
        except requests.exceptions.ConnectionError:
            _consecutive_failures += 1
            log.warning(f"[WEBHOOK] GET {path} connection refused (fail #{_consecutive_failures})")
            break  # no point retrying connection errors
        except Exception as e:
            log.warning(f"[WEBHOOK] GET {path} error: {e}")
    _consecutive_failures += 1
    return None


def _post(path, json_data, timeout=_TIMEOUT_MEDIUM):
    """POST with logging. Returns parsed JSON or None."""
    global _last_successful_call, _consecutive_failures
    url = f"{WIN_WEBHOOK_URL}{path}"
    try:
        r = requests.post(url, json=json_data, headers=_HEADERS, timeout=timeout)
        if r.ok:
            _last_successful_call = time.time()
            _consecutive_failures = 0
            return r.json()
        if r.status_code == 401:
            log.error(
                "[WEBHOOK] ❌ 401 Unauthorized — WEBHOOK_AUTH_TOKEN mismatch. "
                "Make sure .env on both Mac and Windows has the same WEBHOOK_AUTH_TOKEN."
            )
            _consecutive_failures += 1
            return None
        log.warning(f"[WEBHOOK] POST {path} → HTTP {r.status_code}")
    except requests.exceptions.Timeout:
        log.warning(f"[WEBHOOK] POST {path} timeout ({timeout}s)")
    except requests.exceptions.ConnectionError:
        _consecutive_failures += 1
        log.warning(f"[WEBHOOK] POST {path} connection refused")
    except Exception as e:
        log.warning(f"[WEBHOOK] POST {path} error: {e}")
    _consecutive_failures += 1
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Connection & Health
# ══════════════════════════════════════════════════════════════════════════════

def initialize(**kwargs) -> bool:
    """Initialize connection to the Windows MT5 webhook server."""
    res = _get("/status", timeout=8)
    if res and res.get("status") == "running":
        login = res.get("login", "?")
        server = res.get("server", "?")
        log.info(f"[WEBHOOK] ✅ Connected to MT5 (login={login} server={server})")
        return True
    log.warning("[WEBHOOK] ❌ MT5 server not reachable or terminal not running")
    return False


def shutdown():
    """No persistent connection to close."""
    pass


def last_error():
    if _consecutive_failures > 0:
        return (_consecutive_failures, f"Last {_consecutive_failures} calls failed")
    return (0, "No error (webhook)")


def is_connected() -> bool:
    """Check if we've had a successful call recently (within 60s)."""
    return (time.time() - _last_successful_call) < 60 and _consecutive_failures < 5


def terminal_info():
    """Get MT5 terminal info from the server."""
    data = _get("/status", timeout=8)
    if not data:
        return None
    return SimpleNamespace(
        connected=data.get("terminal_connected", False),
        login=data.get("login", 0),
        server=data.get("server", ""),
        build=data.get("build", 0),
        name=data.get("name", "MetaTrader 5"),
        company=data.get("company", ""),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Symbol Info & Ticks
# ══════════════════════════════════════════════════════════════════════════════

def symbol_info(symbol: str):
    """Get symbol specifications (digits, contract size, volumes, point)."""
    data = _get(f"/symbol?symbol={quote(symbol, safe='')}", timeout=_TIMEOUT_SHORT)
    if not data or data.get("status") != "ok":
        return None
    return SimpleNamespace(**data)


def symbol_info_tick(symbol: str):
    """Get current bid/ask/last tick for a symbol."""
    data = _get(f"/tick?symbol={quote(symbol, safe='')}", timeout=_TIMEOUT_SHORT)
    if not data or data.get("status") != "ok":
        return None
    return SimpleNamespace(**data)


# ══════════════════════════════════════════════════════════════════════════════
#  Historical Candles (OHLCV)
# ══════════════════════════════════════════════════════════════════════════════

def copy_rates_from_pos(symbol: str, timeframe: int, pos: int, count: int):
    """Fetch OHLCV candles as numpy structured array (MT5-compatible format)."""
    tf_str = _TF_INT_TO_STR.get(timeframe, "M15")
    data = _get(f"/candles?symbol={quote(symbol, safe='')}&tf={tf_str}&count={count}", timeout=_TIMEOUT_MEDIUM)
    if not data or data.get("status") != "ok":
        return None

    candles = data.get("candles", [])
    if not candles:
        return None

    records = []
    for c in candles:
        records.append((
            c.get("time", 0),
            c.get("o", c.get("open", 0.0)),
            c.get("h", c.get("high", 0.0)),
            c.get("l", c.get("low", 0.0)),
            c.get("c", c.get("close", 0.0)),
            c.get("vol", c.get("tick_volume", 0)),
            c.get("spread", 0),
            c.get("real_vol", c.get("real_volume", 0))
        ))

    dtype = [
        ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
        ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")
    ]
    return np.array(records, dtype=dtype)


# ══════════════════════════════════════════════════════════════════════════════
#  Account Info
# ══════════════════════════════════════════════════════════════════════════════

def account_info():
    """Get full account info: balance, equity, margin, leverage, currency."""
    data = _get("/account", timeout=_TIMEOUT_SHORT)
    if not data or data.get("status") != "ok":
        return None
    return SimpleNamespace(**data)


# ══════════════════════════════════════════════════════════════════════════════
#  Positions
# ══════════════════════════════════════════════════════════════════════════════

def positions_get(symbol: str = None, ticket: int = None):
    """Get open positions. Filter by symbol or ticket."""
    path = f"/positions/{quote(symbol, safe='')}" if symbol else "/positions"
    data = _get(path, timeout=_TIMEOUT_MEDIUM)
    if not data or data.get("status") != "ok":
        return None

    pos_list = data.get("positions", [])
    if ticket is not None:
        pos_list = [p for p in pos_list if p.get("ticket") == ticket]

    if not pos_list:
        return None

    out = []
    for p in pos_list:
        ns = SimpleNamespace(**p)
        if hasattr(ns, "type_id"):
            ns.type = ns.type_id
        out.append(ns)
    return out


def modify_position_sltp(ticket: int, sl: float, tp: float):
    """Modify SL/TP on an existing position."""
    res = _post("/modify", {"ticket": ticket, "sl": sl, "tp": tp})
    if res and res.get("status") in ("ok", "success"):
        return SimpleNamespace(retcode=TRADE_RETCODE_DONE, comment="Modified via webhook")
    return SimpleNamespace(retcode=10004, comment="Modification failed via webhook")


# ══════════════════════════════════════════════════════════════════════════════
#  Orders (BUY / SELL / CLOSE)
# ══════════════════════════════════════════════════════════════════════════════

def order_send(request: dict):
    """Send a trade order (open, close, or modify SL/TP)."""
    action_type = request.get("action")

    # ── SL/TP Modification ────────────────────────────────────────────────
    if action_type == TRADE_ACTION_SLTP:
        ticket = request.get("position")
        sl = request.get("sl")
        tp = request.get("tp")
        return modify_position_sltp(ticket, sl, tp)

    # ── Close existing position ───────────────────────────────────────────
    if "position" in request:
        ticket = request["position"]
        symbol = request.get("symbol", "")
        res = _post("/webhook", {"action": "CLOSE", "symbol": symbol, "ticket": ticket})
    else:
        # ── Open new position ─────────────────────────────────────────────
        action = "BUY" if request.get("type") == ORDER_TYPE_BUY else "SELL"
        payload = {
            "action": action,
            "symbol": request.get("symbol"),
            "lot": request.get("volume", 0.01),
            "sl": request.get("sl"),
            "tp": request.get("tp"),
            "comment": request.get("comment", ""),
        }
        res = _post("/webhook", payload)

    if res and res.get("status") == "success":
        order_num = res.get("ticket", res.get("order", int(time.time())))
        return SimpleNamespace(
            retcode=TRADE_RETCODE_DONE,
            order=order_num,
            volume=res.get("volume", request.get("volume", 0.01)),
            price=res.get("price", 0),
            comment="Order executed via webhook",
        )

    retcode = res.get("retcode", 10004) if res else 10004
    msg = res.get("message", "Failed via webhook") if res else "No response"
    return SimpleNamespace(retcode=retcode, order=0, comment=msg)


# ══════════════════════════════════════════════════════════════════════════════
#  🧠 SMART BOT DATA — Indicators, History, Analytics
# ══════════════════════════════════════════════════════════════════════════════

def get_indicators(symbol: str, timeframes: str = "M1,M15,H1,H4,D1", count: int = 300) -> dict:
    """Fetch multi-timeframe technical indicators computed on the Windows MT5 server.

    Returns dict of timeframe -> indicator values:
        RSI, MACD, ADX (+DI/-DI), Stochastic (K/D), Bollinger Bands,
        EMA (20/50/200), ATR(14), Williams %R, price, price_change.

    These are computed from raw MT5 candles on the Windows side — no local
    computation needed. This is the primary data source for the smart bot.
    """
    data = _get(
        f"/indicators?symbol={quote(symbol, safe='')}&tf={timeframes}&count={count}",
        timeout=_TIMEOUT_LONG,
        retries=2,
    )
    if not data or data.get("status") != "ok":
        return {}
    return data.get("timeframes", {})


def get_trade_history(days: int = 7) -> dict:
    """Get closed trade history for the past N days.

    Returns dict with:
        status, deals (list of closed trades with ticket, symbol, type,
        volume, price, profit, commission, swap, time, comment).
    """
    data = _get(f"/history?days={days}", timeout=_TIMEOUT_MEDIUM)
    if not data:
        return {}
    return data


def get_connection_health() -> dict:
    """Get connection health diagnostics for monitoring."""
    return {
        "server_url": WIN_WEBHOOK_URL,
        "last_success_ago_s": round(time.time() - _last_successful_call, 1) if _last_successful_call else None,
        "consecutive_failures": _consecutive_failures,
        "is_healthy": is_connected(),
    }
