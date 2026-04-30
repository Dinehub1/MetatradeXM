#!/usr/bin/env python3
"""
continuous_trader.py — 24/7 autonomous trading engine for XAUUSD + XAGUSD

Trades Gold (GOLD.i#) and Silver (SILVER.i#) on XMGlobal MT5 demo account.
Monitors positions every 30 seconds, closes at targets, re-enters on signals.

Usage:
    python3 continuous_trader.py          # start trading
    python3 continuous_trader.py --dry    # paper-trade only
    python3 continuous_trader.py --close  # close all & exit
"""

import os
import sys
import json
import time
import logging
import traceback
import logging.handlers
import argparse
import signal
import concurrent.futures
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path
from core.paths import ROOT_DIR, CONFIG_DIR, STATE_DIR, LOG_DIR, DATA_DIR
# ── Environment loading — load .env (single source of truth) ──
_env_path = ROOT_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ[k.strip()] = v.strip()

# ── Paths ────────────────────────────────────────────────────────────────────
STATUS_FILE = STATE_DIR / "bot_status.json"
CANDLES_FILE = STATE_DIR / "candles_cache.json"
STATE_FILE    = STATE_DIR / "trader_state.json"
COOLDOWN_FILE = STATE_DIR / "cooldown_state.json"
STREAKS_FILE  = STATE_DIR / "loss_streaks.json"
LOG_FILE = LOG_DIR / "trading.log"

# ── Config integrity guard ────────────────────────────────────────────────────
# Protect buy_threshold / sell_threshold from being silently reset by any
# external process (Hermes, MCP, self_improver). Check every cycle.
# Threshold calibration (Senior Trader Overhaul 2026-04-23):
#   H4/D1 weights reduced → max trend-only score ~14 (was ~28)
#   |score| >= 8 allows M15 entry signals to fire in both directions
#   Combined with graduated MTF penalty (not veto), enables counter-trend trades
def _check_config_integrity():
    """Warn if thresholds fall outside valid range. Hard-clamp is in analyzer._load_weights()."""
    try:
        w = json.loads((CONFIG_DIR / "scoring_weights.json").read_text())
        bt = w.get("buy_threshold", 12)
        if bt < 8 or bt > 20:
            log.warning(f"CONFIG: buy_threshold={bt} outside valid 8-20 range — analyzer will clamp on next read")
    except Exception:
        pass

# ── Logging ──────────────────────────────────────────────────────────────────
from core.logger import setup_logging

setup_logging()  # root → bot.log (10 MB × 3); console at WARNING+
log = logging.getLogger("trader")

# ── Silence noisy third-party loggers ────────────────────────────────────────
for _noisy in (
    "socketio",
    "engineio",
    "metaapi_cloud_sdk",
    "asyncio",
    "urllib3",
    "requests",
    "websockets",
    "websocket",
    "aiohttp",
    "httpx",
    "httpcore",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
# websocket-client logs [Errno 61] at ERROR level internally — suppress completely
logging.getLogger("websocket").setLevel(logging.CRITICAL)

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    {
        "broker": "GOLD.i#",
        "display": "XAUUSD",
        "pip": 0.10,
        "contract_size": 100,
        "sl_pips": 35,           # fallback if ATR unavailable
        "tp_pips": 70,
        "lot": 0.01,
        # 2026-04-30 per-symbol gating (silver more volatile, needs higher bar)
        "score_threshold": 15,    # min score magnitude to consider trade
        "min_confidence":  0.65,  # AI confidence floor
        "adx_min":         22,    # min ADX for trend trades
        "rsi_oversold":    25,    # tightened from 30
        "rsi_overbought":  75,    # tightened from 70
        # ATR-based dynamic stops (preferred over fixed pips)
        "sl_atr_mult":     1.5,   # SL = 1.5 × M15 ATR
        "tp_atr_mult":     4.5,   # TP = 4.5 × M15 ATR (R:R 3.0)
        "trail_atr_mult":  1.0,   # trail distance = 1.0 × ATR
    },
    {
        "broker": "SILVER.i#",
        "display": "XAGUSD",
        "pip": 0.01,
        "contract_size": 5000,
        "sl_pips": 25,           # 15→25 (silver moves 1.7x more)
        "tp_pips": 80,           # 30→80 (R:R 3.2)
        "lot": 0.01,
        # Silver-specific gating (higher bar — historically choppy 27% WR)
        "score_threshold": 18,    # +18 vs gold's +15
        "min_confidence":  0.72,  # 72% vs gold's 65%
        "adx_min":         28,    # silver only trades strongest trends
        "rsi_oversold":    25,
        "rsi_overbought":  75,
        "sl_atr_mult":     1.5,
        "tp_atr_mult":     4.5,
        "trail_atr_mult":  1.2,   # silver trails wider (more breathing room)
    },
]

# ── Session-Aware Risk Configuration ─────────────────────────────────────────
# Gold behaves differently in each session. Asian = range, London = breakout,
# NY overlap = the kill zone. Adjust lot and confidence gates accordingly.
SESSION_CONFIG = {
    "ASIAN":             {"lot_mult": 0.5,  "min_conf": 0.85},
    "LONDON":            {"lot_mult": 0.7,  "min_conf": 0.82},
    "LONDON_NY_OVERLAP": {"lot_mult": 1.0,  "min_conf": 0.80},
    "NEW_YORK":          {"lot_mult": 0.7,  "min_conf": 0.82},
}

CONFIG = {
    "monitor_interval_s": 15,
    "analysis_interval_s": int(os.getenv("ANALYSIS_INTERVAL_S", "60")),
    "profit_close_pct": 3.0,   # let broker TP at 160 pips be primary exit
    "loss_close_pct": 1.0,     # align with wider 80-pip SL
    "min_confidence": 0.85,    # pyramid system: high-conviction entries only
    "max_trades_per_sym": 6,   # 10→6: pyramid max_tranches reduced (whipsaw protection)
    "max_total_positions": 6,  # 12→6: smaller blast radius if a pyramid goes wrong
    "dry_run": False,
    "use_ai": True,
    "max_reconnect_attempts": 5,
    "reconnect_backoff_s": 10,
}

# ── Helpers ──────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _sanitize(obj):
    """Make data JSON-safe (numpy types, NaN, Inf → Python builtins / None)."""
    import math

    try:
        import numpy as np

        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(obj, np.ndarray):
            return [_sanitize(x) for x in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


_HERMES_DIR = Path.home() / ".hermes"

def _write_status(data: dict):
    try:
        clean = _sanitize(data)
        STATUS_FILE.write_text(json.dumps(clean, indent=2))
        # Keep hermes status snapshots in sync so Hermes agent sees fresh data
        _hermes_payload = json.dumps({
            "bot": clean,
            "state": {
                "cycle": clean.get("cycle"),
                "total_trades": clean.get("stats", {}).get("total_trades", 0),
                "wins": clean.get("stats", {}).get("wins", 0),
                "losses": clean.get("stats", {}).get("losses", 0),
            },
            "server_time_utc": clean.get("ts", ""),
        }, indent=2)
        (_HERMES_DIR / "adx_status.json").write_text(_hermes_payload)
        (_HERMES_DIR / "adx_current.json").write_text(_hermes_payload)
    except Exception as e:
        log.warning(f"Status write error: {e}")


def _log_signal(
    symbol: str,
    direction: str,
    confidence: float,
    reason: str,
    action: str,
    ticket: int = 0,
):
    """Write signal to trades.db for dashboard display."""
    import sqlite3

    DB_FILE = ROOT_DIR / "data" / "trades.db"
    try:
        with sqlite3.connect(str(DB_FILE)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, symbol TEXT, direction TEXT,
                    confidence REAL, reason TEXT, action TEXT,
                    ticket INTEGER, order_json TEXT
                )""")
            conn.execute(
                "INSERT INTO signals (ts, symbol, direction, confidence, reason, action, ticket) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    direction,
                    round(confidence, 4),
                    reason[:200],
                    action,
                    ticket,
                ),
            )
    except Exception as e:
        log.debug(f"Signal log error: {e}")


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {"cycle": 0, "total_trades": 0, "wins": 0, "losses": 0}


def _atomic_save(file_path: Path, data: dict, label: str) -> bool:
    """Atomically save JSON to disk: write to temp, then rename. Prevents corruption on crash."""
    try:
        temp_path = file_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(data, indent=2))
        temp_path.replace(file_path)
        return True
    except Exception as e:
        log.warning(f"Failed to save {label}: {e}")
        return False


def _save_state(state: dict):
    """Async save (queued for background thread)."""
    _file_save_queue.put((STATE_FILE, state, "state"))


def _load_cooldown() -> dict:
    """Load per-symbol loss cooldown timestamps from disk (survives restarts)."""
    try:
        if COOLDOWN_FILE.exists():
            return json.loads(COOLDOWN_FILE.read_text())
    except json.JSONDecodeError:
        log.warning(f"Corrupted cooldown file, starting fresh")
    except Exception:
        pass
    return {}


def _save_cooldown(cooldown: dict):
    """Async save cooldown timestamps (queued for background thread)."""
    _file_save_queue.put((COOLDOWN_FILE, cooldown, "cooldown"))


def _load_streaks() -> dict:
    """Load per-symbol consecutive loss counts from disk (survives restarts)."""
    try:
        if STREAKS_FILE.exists():
            return json.loads(STREAKS_FILE.read_text())
    except json.JSONDecodeError:
        log.warning(f"Corrupted streaks file, starting fresh")
    except Exception:
        pass
    return {}


def _save_streaks(streaks: dict):
    """Async save consecutive loss counts (queued for background thread)."""
    _file_save_queue.put((STREAKS_FILE, streaks, "streaks"))


def is_forex_market_open() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    wd, h = now.weekday(), now.hour
    if wd == 5:
        return False, "MARKET_CLOSED"  # Saturday
    if wd == 4 and h >= 22:
        return False, "MARKET_CLOSED"  # Friday 22:00 onwards
    if wd == 6 and h < 22:
        return False, "MARKET_CLOSED"  # Sunday before 22:00
    # ── Correct Forex/Gold session boundaries (UTC) ───────────────────────────
    # ASIAN:              22:00–07:59  (Sunday 22:00 UTC → Monday-Friday 07:59)
    # LONDON:             08:00–12:59  (LSE open, pre-NY)
    # LONDON_NY_OVERLAP:  13:00–16:59  (PEAK — both centres open)
    # NEW_YORK:           17:00–21:59  (NY afternoon, London closed)
    if  8 <= h < 13: return True, "LONDON"
    if 13 <= h < 17: return True, "LONDON_NY_OVERLAP"
    if 17 <= h < 22: return True, "NEW_YORK"
    return True, "ASIAN"  # 22:00–23:59 or 00:00–07:59


def fmt_profit(p: float) -> str:
    arrow = "▲" if p >= 0 else "▼"
    color = "\033[92m" if p >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}{arrow} ${p:+.2f}{reset}"


def _compact_text(text: str, limit: int = 88) -> str:
    """Normalize whitespace and trim long log text for fast scanning."""
    text = " ".join(str(text or "").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _adx_regime(adx: float) -> str:
    if adx > 25:
        return "TRENDING"
    if adx < 18:
        return "RANGING"
    return "DEVELOPING"


def _format_factor_summary(factor_scores: dict) -> str:
    parts = []
    for key, label in [
        ("f1_h4_trend", "H4"),
        ("f2_h1_trend", "H1"),
        ("f3_rsi_zone", "RSI"),
        ("f4_macd_momentum", "MACD"),
        ("f5_adx_strength", "ADX"),
        ("f6_stoch_confirm", "Stoch"),
        ("f7_bb_action", "BB"),
        ("f10_d1_trend", "D1"),
        ("f12_fibonacci", "Fib"),
    ]:
        value = factor_scores.get(key, 0)
        if value != 0:
            parts.append(f"{label}={value:+.1f}")
    return " ".join(parts) if parts else "all neutral"


# ── Bridge factory with auto-reconnect ──────────────────────────────────────


def make_bridge():
    ws_url = os.environ.get("WIN_WS_URL", "")
    webhook_url = os.environ.get("WIN_WEBHOOK_URL", "")

    # Priority 1: WebSocket Bridge (real-time data + HTTP execution)
    if ws_url and webhook_url:
        from bridges.ws_bridge import WSBridge
        log.info(f"[BRIDGE] Using WebSocket Bridge → {ws_url}")
        return WSBridge(ws_url, webhook_url)

    # Priority 2: HTTP-only Webhook Bridge (polling fallback)
    if webhook_url:
        from bridges.webhook_bridge import WebhookBridge
        log.info(f"[BRIDGE] Using Windows MT5 Webhook → {webhook_url}")
        return WebhookBridge(webhook_url)

    # # Priority 3: MetaApi Cloud Bridge (DISABLED — kept as backup)
    # token = os.environ.get("METAAPI_TOKEN", "")
    # account_id = os.environ.get("METAAPI_ACCOUNT_ID", "")
    # if token and account_id:
    #     from bridges.metaapi_bridge import MetaApiBridge
    #     log.info("[BRIDGE] Using MetaApi Cloud Bridge")
    #     return MetaApiBridge(token, account_id)

    # Priority 4: Direct MT5 (Windows-only)
    from bridges.mt5_bridge import MT5Bridge
    log.info("[BRIDGE] Using Direct MT5 Bridge (Windows-only)")
    return MT5Bridge()


def connect_with_retry(bridge, max_attempts: int = 5) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            log.info(
                f"🔌 Connecting to MetaTrader (attempt {attempt}/{max_attempts})..."
            )
            if bridge.connect():
                log.info("✅ MetaTrader connected")
                return True
        except Exception as e:
            log.warning(f"Connection attempt {attempt} failed: {e}")
        if attempt < max_attempts:
            wait = CONFIG["reconnect_backoff_s"] * attempt
            log.info(f"   Retrying in {wait}s...")
            time.sleep(wait)
    log.error("❌ Could not connect after all attempts")
    return False


# ── Position management ──────────────────────────────────────────────────────


def get_positions_by_symbol(bridge) -> dict:
    """Returns {broker_symbol: [position, ...]}"""
    try:
        all_pos = bridge.get_open_positions()
    except Exception as e:
        log.warning(f"get_open_positions error: {e}")
        return {}
    by_sym = {}
    for p in all_pos or []:
        sym = getattr(p, "symbol", "")
        by_sym.setdefault(sym, []).append(p)
    return by_sym


# ── Background file I/O queue (prevents blocking main thread) ──────────────
_file_save_queue = queue.Queue()


def _background_file_writer():
    """Background thread: async file saves. Processes queue continuously."""
    while True:
        try:
            file_path, data, label = _file_save_queue.get(timeout=1)
            try:
                _atomic_save(file_path, data, label)
            except Exception as e:
                log.warning(f"Background save error ({label}): {e}")
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"Background writer crashed: {e}")
            break


# Start background file writer thread (daemon, exits with main process)
_writer_thread = threading.Thread(target=_background_file_writer, daemon=True)
_writer_thread.start()
log.debug("[IO] Background file writer started")


# ── Per-ticket peak profit tracking (persistent trailing SL) ────────────────
_PEAK_FILE = ROOT_DIR / "peak_profits.json"
_peak_profit_lock = threading.Lock()  # Protect against concurrent access


def _load_peaks() -> dict:
    try:
        if _PEAK_FILE.exists():
            return json.loads(_PEAK_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_peaks(d: dict):
    """Async save peak profits (queued for background thread)."""
    _file_save_queue.put((_PEAK_FILE, d, "peak_profits"))


_peak_profit: dict = _load_peaks()  # ticket → peak profit seen


def check_and_close_positions(
    bridge,
    account_balance: float,
    positions_by_sym: dict,
    state: dict,
    dry_run: bool,
    capital_mgr=None,
    skill_mgr=None,
    loss_cooldown: dict = None,
    consec_losses: dict = None,  # display_symbol -> consecutive loss count
    memory=None,
) -> list:
    """
    Close positions that hit profit or loss targets, OR if trailing SL is triggered.
    Trailing SL: once profit reaches 1% of balance, lock in 50% of peak profit.
    Returns list of closed broker symbols.
    """
    closed = []
    profit_target = account_balance * (CONFIG["profit_close_pct"] / 100)
    loss_limit = account_balance * (CONFIG["loss_close_pct"] / 100)
    trail_trigger = account_balance * 1.0 / 100  # was 0.5% → start trailing at +1% (don't strangle small winners)
    trail_lock_pct = 0.40  # was 0.50 → lock 40% of peak (keep more upside potential)

    for sym_cfg in SYMBOLS:
        broker_sym = sym_cfg["broker"]
        positions = positions_by_sym.get(broker_sym, [])
        for pos in positions:
            profit = getattr(pos, "profit", 0)
            ticket = str(getattr(pos, "ticket", "?"))
            direction = "BUY" if getattr(pos, "type", 1) == 0 else "SELL"

            # Track peak profit for trailing SL (persisted to disk)
            with _peak_profit_lock:
                prev_peak = _peak_profit.get(ticket, 0)
                if profit > prev_peak:
                    _peak_profit[ticket] = profit
                    _save_peaks(_peak_profit)
                peak = _peak_profit.get(ticket, 0)

            should_close = False
            reason = ""
            if profit >= profit_target:
                should_close = True
                reason = f"profit target +{CONFIG['profit_close_pct']}%"
            elif profit <= -loss_limit:
                should_close = True
                reason = f"loss limit -{CONFIG['loss_close_pct']}%"
            elif peak >= trail_trigger and profit < peak * trail_lock_pct:
                # Trailing SL triggered: gave back >50% of peak
                should_close = True
                reason = f"trailing SL (peak ${peak:+.2f} → now ${profit:+.2f})"

            if should_close:
                log.info(
                    f"🎯 Closing #{ticket} {sym_cfg['display']} {direction} "
                    f"{fmt_profit(profit)} — {reason}"
                )
                if not dry_run:
                    ok = bridge.close_position(ticket)
                    if ok:
                        state["total_trades"] += 1
                        outcome = "WIN" if profit > 0 else "LOSS"
                        if profit > 0:
                            state["wins"] += 1
                        else:
                            state["losses"] += 1
                        closed.append(broker_sym)

                        # Senior-trader cooldown: rest 15 min after a loss.
                        # Persisted to disk so process restarts don't reset it.
                        if outcome == "LOSS" and loss_cooldown is not None:
                            loss_cooldown[broker_sym] = time.time()
                            _save_cooldown(loss_cooldown)
                            log.info(f"   [COOLDOWN] {broker_sym}: 15-min cooldown after loss (saved)")

                        # Cleanup peak profit tracking
                        with _peak_profit_lock:
                            _peak_profit.pop(ticket, None)
                            _save_peaks(_peak_profit)

                        # Record outcome in trade memory
                        try:
                            mem = memory
                            if mem is None:
                                from learning.memory import TradeMemory
                                mem = TradeMemory()
                            pip_size = sym_cfg["pip"]
                            contract_size = sym_cfg.get("contract_size", 100)
                            open_price = getattr(pos, "price_open", 0)
                            _vol = getattr(pos, "volume", 0.01)
                            _pip_val = pip_size * contract_size * _vol
                            if _pip_val > 0:
                                pips = profit / _pip_val
                            else:
                                log.warning(f"Invalid pip value calculation: pip={pip_size} size={contract_size} vol={_vol}")
                                pips = 0
                            current_price = getattr(pos, "price_current", getattr(pos, "price_open", 0))
                            # Pass symbol/direction to avoid UNKNOWN records in learning loop
                            mem.record_outcome(
                                str(ticket), current_price, round(pips, 1), outcome,
                                symbol=sym_cfg["display"],
                                direction=direction,
                            )
                            # Update consecutive loss counter (Trading in the Zone edge tracking)
                            if consec_losses is not None:
                                disp = sym_cfg["display"]
                                if outcome == "WIN":
                                    consec_losses[disp] = 0
                                else:
                                    consec_losses[disp] = consec_losses.get(disp, 0) + 1
                                _save_streaks(consec_losses)

                            # Wire skill outcome recording
                            if skill_mgr:
                                try:
                                    import sqlite3 as _sql
                                    with _sql.connect(str(mem.db_path)) as _conn:
                                        _entry_row = _conn.execute(
                                            "SELECT skills_used FROM trade_entries WHERE ticket=? ORDER BY id DESC LIMIT 1",
                                            (str(ticket),)
                                        ).fetchone()
                                        if _entry_row and _entry_row[0]:
                                            import json as _json
                                            _skills = _json.loads(_entry_row[0])
                                            if _skills:
                                                for _sk in _skills:
                                                    skill_mgr.record_outcome(_sk, outcome, round(pips, 1))
                                except Exception as _se:
                                    log.debug(f"Skill outcome record: {_se}")

                            if capital_mgr:
                                capital_mgr.record_outcome(outcome, profit)
                        except Exception as e:
                            log.warning(f"Outcome record error: {e}")
                else:
                    log.info(f"   [DRY RUN] Would close #{ticket}")
                    closed.append(broker_sym)

    return closed


def build_order_params(
    sym_cfg: dict,
    tick,
    direction: str,
    confidence: float = 0.55,
    atr: float = 0,
    lot_reduction: float = 1.0,
    regime_data: dict = None,
) -> dict:
    """Build order parameters with validation. Returns None if parameters invalid."""
    import math
    import pandas as pd

    # Validate tick freshness (prevent stale price orders)
    tick_time = getattr(tick, 'time', 0)
    if tick_time > 0:
        tick_age_s = (time.time() - tick_time / 1000.0)  # MT5 time in ms
        if tick_age_s > 30:
            log.warning(f"Stale tick ({tick_age_s:.1f}s old) for {sym_cfg['display']} — rejecting order")
            return None

    pip = sym_cfg["pip"]
    price = tick.ask if direction == "BUY" else tick.bid
    if price <= 0:
        log.warning(f"Invalid price {price} for {sym_cfg['display']} — rejecting order")
        return None
    digits = 2 if pip >= 0.01 else 5

    # ── ATR-based SL/TP (per-symbol multipliers + regime adjustment) ──────
    # Validate ATR: must be positive number (not NaN, 0, or negative)
    if pd.isna(atr) or atr <= 0:
        log.debug(f"Invalid ATR ({atr}) for {sym_cfg['display']} — using fallback fixed stops")
        sl_pips = sym_cfg["sl_pips"]
        tp_pips = sym_cfg["tp_pips"]
    else:
        # ATR is valid — use it with regime-aware multipliers
        sl_mult = sym_cfg.get("sl_atr_mult", 1.5)
        tp_mult = sym_cfg.get("tp_atr_mult", 4.5)

        if regime_data:
            vol_state = regime_data.get('volatility_state', 'NORMAL')
            if vol_state in ("EXPANDING", "HIGH"):
                sl_mult *= 1.10   # +10% wider stops
                tp_mult *= 1.10
            elif vol_state == "COMPRESSED":
                sl_mult *= 0.85   # -15% tighter stops
                tp_mult *= 0.85

        # Pure ATR-based (no cap from fixed sl_pips — let vol regime drive size)
        sl_pips = max(int(atr * sl_mult / pip), 15 if pip >= 0.10 else 5)
        tp_pips = max(int(atr * tp_mult / pip), 35 if pip >= 0.10 else 10)
        # Enforce minimum R:R 2.5 (was 1.8 — too low for 49% WR)
        min_rr = 2.5
        if tp_pips < sl_pips * min_rr:
            tp_pips = int(sl_pips * min_rr)

    if direction == "BUY":
        sl = round(price - sl_pips * pip, digits)
        tp = round(price + tp_pips * pip, digits)
    else:
        sl = round(price + sl_pips * pip, digits)
        tp = round(price - tp_pips * pip, digits)

    # ── Kelly Criterion + Risk-Based Position Sizing ─────────────────────
    # 2026-04-30: Balance-aware sizing.
    # Risk 1% per trade × quarter-Kelly multiplier (conservative).
    # f* = p - (1-p)/b   where p=confidence, b=R:R
    p = min(confidence, 0.85)
    b = tp_pips / sl_pips if sl_pips > 0 else 2.0
    kelly_f = max(0.0, p - ((1 - p) / b))
    quarter_kelly = kelly_f / 4.0   # quarter-Kelly = much safer than half

    # Base risk: 1% of balance (capped if no balance available)
    balance = sym_cfg.get("_account_balance", 0) or 1000.0
    risk_pct = 0.01 * (1 + quarter_kelly)   # 1.0%–1.25% scaling
    risk_dollars = balance * risk_pct

    # Pip value per lot ($10/pip for gold, $50/pip for silver)
    pip_value_per_lot = pip * sym_cfg["contract_size"]
    sl_dollars_per_lot = sl_pips * pip_value_per_lot

    if sl_dollars_per_lot > 0:
        kelly_lot = risk_dollars / sl_dollars_per_lot
    else:
        kelly_lot = sym_cfg["lot"]

    # Apply confidence multiplier on top (favors higher-conviction trades)
    conf_mult = max(0.3, min((confidence - 0.5) / 0.35, 1.0))
    lot = round(max(kelly_lot * conf_mult * lot_reduction, 0.01), 2)
    # Hard cap at 0.50 (safety brake until live track record proves the system)
    lot = min(lot, 0.50)
    log.info(
        f"   [KELLY] p={p:.2f} b={b:.2f} | qK={quarter_kelly:.3f} | risk={risk_pct:.1%} "
        f"(${risk_dollars:.2f}) | sl=${sl_dollars_per_lot:.2f}/lot | lot={lot}"
    )

    return {
        "symbol": sym_cfg["broker"],
        "direction": direction,
        "lot": lot,
        "price": price,
        "sl": sl,
        "tp": tp,
        "sl_pips": sl_pips,
        "tp_pips": tp_pips,
        "comment": f"CT-{direction}-c{confidence:.0%}",
    }


# ── Main trading cycle ───────────────────────────────────────────────────────


class ContinuousTrader:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run or CONFIG["dry_run"]
        self.state = _load_state()
        self.bridge = make_bridge()
        self.connected = False
        self._stop = False
        self._last_analysis = 0  # epoch seconds

        # Test bridge connection at startup (fail fast if unavailable)
        if not connect_with_retry(self.bridge, max_attempts=CONFIG["max_reconnect_attempts"]):
            raise RuntimeError(
                f"Cannot connect to trading bridge. "
                f"Check MetaTrader/webhook server configuration."
            )
        self.connected = True

        # ── Self-improving systems ──────────────────────────────────────
        try:
            from learning.memory import TradeMemory

            self.memory = TradeMemory()
            # Validate that memory system works by checking DB path exists
            if not self.memory.db_path.exists():
                raise RuntimeError(f"Memory database not created at {self.memory.db_path}")
            log.info("  [MEMORY] Trade memory system initialized")
        except Exception as e:
            log.error(f"  [MEMORY] CRITICAL: Cannot start without working memory system: {e}")
            raise RuntimeError(f"Memory system initialization failed: {e}") from e

        try:
            from learning.skill_manager import SkillManager

            self.skill_mgr = SkillManager()
            skills = self.skill_mgr.list_skills()
            log.info(f"  [SKILLS] Loaded {len(skills)} trading skills")
        except Exception as e:
            log.warning(f"  [SKILLS] Failed to init: {e}")
            self.skill_mgr = None

        try:
            from risk.strategy_filters import FilterChain

            self.filter_chain = FilterChain()
            log.info(f"  [FILTERS] Filter chain initialized")
        except Exception as e:
            log.warning(f"  [FILTERS] Failed to init: {e}")
            self.filter_chain = None

        try:
            from learning.self_improver import PerformanceAnalyzer

            self.improver = PerformanceAnalyzer(self.memory, self.skill_mgr)
            log.info(f"  [IMPROVE] Self-improvement engine initialized")
        except Exception as e:
            log.warning(f"  [IMPROVE] Failed to init: {e}")
            self.improver = None

        try:
            from risk.position_scaler import PositionScaler

            self.scaler = PositionScaler()
            log.info(f"  [SCALER] Position scaling system initialized")
        except Exception as e:
            log.warning(f"  [SCALER] Failed to init: {e}")
            self.scaler = None

        try:
            from risk.pyramid_manager import PyramidManager

            self.pyramid = PyramidManager(memory=self.memory)
            log.info(f"  [PYRAMID] 🔺 Smart 10-tranche pyramid system initialized")
        except Exception as e:
            log.warning(f"  [PYRAMID] Failed to init: {e}")
            self.pyramid = None

        try:
            from risk.smart_exit import SmartExitManager

            self.smart_exit = SmartExitManager()
            log.info(f"  [SMART EXIT] Adaptive exit system initialized")
        except Exception as e:
            log.warning(f"  [SMART EXIT] Failed to init: {e}")
            self.smart_exit = None

        try:
            from risk.capital_manager import CapitalManager

            self.capital = CapitalManager()
            log.info(f"  [CAPITAL] Dynamic capital manager initialized")
        except Exception as e:
            log.warning(f"  [CAPITAL] Failed to init: {e}")
            self.capital = None

        # ── TradingView live data client (optional — quiet if unavailable) ──
        self.tv = None
        try:
            from bridges.tv_client import get_tv_client
            self.tv = get_tv_client()
            if self.tv.wait_for_data("XAUUSD", timeout=5):
                log.info("  [TV] ✅ Live TradingView indicators connected")
            else:
                log.info("  [TV] TradingView not available — using broker candles")
        except Exception:
            log.info("  [TV] TradingView not available — using broker candles")
            self.tv = None

        # Cache for cross-symbol correlation
        self._symbol_signals = {}

        # Per-symbol loss cooldown: persisted to disk so restarts don't reset it
        self._loss_cooldown: dict = _load_cooldown()

        # Per-symbol consecutive loss counter (Trading in the Zone: track the edge,
        # pause when the edge breaks down, never revenge-trade). Persisted to disk.
        self._consec_losses: dict = _load_streaks()  # display_symbol -> int
        # Cycle number at which the per-symbol circuit breaker pause expires.
        # Not persisted — a clean restart resets the marker while streak counts survive.
        self._circuit_break_until: dict = {}  # display_symbol -> cycle number

        # Post-trade cooldown: prevents churn re-entries.
        # 2026-04-30: Split into same-dir (5min) vs any-dir (90s) for flexibility:
        #   - Same direction within 5min = likely chasing/revenge — BLOCK
        #   - Opposite direction within 90s = legit reversal at level — ALLOW
        self._last_trade_time: dict = {}      # broker_symbol -> epoch time
        self._last_trade_dir:  dict = {}      # broker_symbol -> "BUY"/"SELL"
        self._TRADE_COOLDOWN_SAME_DIR = 300   # same direction: 5 min
        self._TRADE_COOLDOWN_ANY_DIR  = 90    # any direction: 90s

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, sig, frame):
        log.info("\n🛑 Shutdown signal received — closing gracefully...")
        self._stop = True

    def _get_other_signal(self, current_symbol: str) -> dict:
        """Get the other metal's signal for correlation filter."""
        for sym, sig in self._symbol_signals.items():
            if sym != current_symbol:
                return sig
        return {}

    def _ensure_connected(self) -> bool:
        if self.connected and getattr(self.bridge, "connected", False):
            return True
        self.connected = connect_with_retry(
            self.bridge, CONFIG["max_reconnect_attempts"]
        )
        return self.connected

    def _get_account(self):
        try:
            return self.bridge.get_account_info()
        except Exception as e:
            log.warning(f"Account info error: {e}")
            return None

    def _run_analysis(self, sym_cfg: dict, tf_data: dict, tick) -> dict:
        """Run multi-timeframe analysis — TradingView live data is primary source."""
        try:
            from core.analyzer import MarketAnalyzer

            analyzer = MarketAnalyzer(use_claude=CONFIG["use_ai"])

            # Prefetch memory context for AI reasoning.
            # Use indicators cached from the PREVIOUS cycle so the AI sees
            # similar-setup recall and session win-rate data (1 cycle lag is fine
            # since similar patterns span hours/days, not seconds).
            memory_context = ""
            if self.memory:
                try:
                    _prev = sym_cfg.get("_last_indicators", {})
                    memory_context = self.memory.prefetch_context(
                        sym_cfg["display"],
                        direction=_prev.get("direction"),
                        adx=_prev.get("adx", 0.0),
                        rsi=_prev.get("rsi", 50.0),
                    )
                except Exception as e:
                    log.warning(f"Memory prefetch error: {e}")

            # ── Fetch External Indicators (MT5 Webhook + TradingView) ───────────
            external_indicators = {}
            
            # 1. MT5 Webhook Indicators (computed directly on Windows MT5 terminal)
            try:
                webhook_inds = self.bridge.get_indicators(sym_cfg["broker"])
                if webhook_inds:
                    external_indicators.update(webhook_inds)
                    log.info(f"DATA | {sym_cfg['display']} | MT5 native indicators ready")
            except Exception as e:
                log.debug(f"Webhook indicator fetch failed: {e}")

            # 2. TradingView Live Indicators (highest quality, takes precedence if available)
            if self.tv and self.tv.is_connected():
                display = sym_cfg["display"]
                snap = self.tv.snapshot()
                sym_data = snap.get("symbols", {}).get(display)
                if sym_data:
                    tfs = sym_data.get("timeframes", {})
                    if tfs:
                        for tf, data in tfs.items():
                            external_indicators.setdefault(tf, {}).update(data)
                        log.info(f"DATA | {display} | TradingView indicators merged")
                    elif sym_data.get("indicators"):
                        # Flat structure (single timeframe)
                        external_indicators.setdefault("M15", {}).update(sym_data["indicators"])
                        log.info(f"DATA | {display} | TradingView M15 indicators merged")

            signal_data = analyzer.analyze(
                tf_data, tick, sym_cfg["display"],
                memory_context=memory_context,
                tv_indicators=external_indicators if external_indicators else None,
            )
            return signal_data
        except Exception as e:
            log.warning("Analysis error for %s: %s", sym_cfg["display"], str(e).replace('%', '%%'))
            return {
                "direction": "HOLD",
                "confidence": 0.0,
                "reason": f"Analysis failed: {str(e).replace('%', '%%')}",
            }



    def _fetch_candles(self, sym_cfg: dict) -> dict:
        broker_sym = sym_cfg["broker"]
        tf_data = {}
        for tf in ["M1", "M15", "H1", "H4", "D1"]:
            count = 60 if tf == "M1" else 500 if tf == "M15" else 200 if tf in ("H1", "H4") else 100
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(self.bridge.get_candles, broker_sym, tf, count)
                    df = fut.result(timeout=15)  # hard 15s cap per timeframe
                if df is not None and len(df) > 30:
                    tf_data[tf] = df
            except concurrent.futures.TimeoutError:
                log.warning(f"Candle fetch {broker_sym}/{tf}: timeout (15s) — skipped")
            except Exception as e:
                log.warning(f"Candle fetch {broker_sym}/{tf}: {e}")
        return tf_data

    def run(self):
        log.info("=" * 60)
        log.info("  🚀 CONTINUOUS TRADER — XAUUSD + XAGUSD")
        log.info(f"  Mode: {'📝 PAPER TRADE' if self.dry_run else '🔴 LIVE TRADING'}")
        log.info(f"  Symbols: XAUUSD (GOLD.i#) + XAGUSD (SILVER.i#)")
        log.info(f"  Monitor: every {CONFIG['monitor_interval_s']}s")
        log.info(f"  Analysis: every {CONFIG['analysis_interval_s']}s")
        log.info(
            f"  Profit target: +{CONFIG['profit_close_pct']}% | Loss limit: -{CONFIG['loss_close_pct']}%"
        )
        log.info("=" * 60)

        if not self._ensure_connected():
            log.error("Cannot connect to MetaTrader — will keep retrying every 60s")
            while not self._stop and not self._ensure_connected():
                time.sleep(60)
            if self._stop:
                return

        # Print initial account info
        acct = self._get_account()
        if acct:
            log.info(f"  Account: #{acct.login} ({acct.server})")
            log.info(f"  Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f}")

        self.state["cycle"] = self.state.get("cycle", 0)
        cycle = self.state["cycle"]

        symbols_status = {}
        candles_cache = {}
        next_analysis = 0  # run immediately on first cycle
        _prev_tickets: dict[str, dict] = {}  # ticket -> {profit, direction, sym_cfg}

        while not self._stop:
            now = time.time()
            cycle += 1
            self.state["cycle"] = cycle
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # ── Config integrity guard (detects external tampering) ──────────
            _check_config_integrity()

            # ── Connection health check ──────────────────────────────────────
            if not self._ensure_connected():
                log.error("❌ MetaTrader disconnected — retrying next cycle")
                _write_status(
                    {
                        "state": "reconnecting",
                        "cycle": cycle,
                        "ts": ts,
                        "dry_run": self.dry_run,
                        "symbols": symbols_status,
                        "connection": "DISCONNECTED",
                    }
                )
                time.sleep(CONFIG["monitor_interval_s"])
                self.bridge = make_bridge()  # fresh bridge
                continue

            # ── Market hours gate ────────────────────────────────────────────
            market_open, session = is_forex_market_open()
            if not market_open:
                log.info(f"💤 Market CLOSED ({session}) — sleeping 5 min")
                _write_status(
                    {
                        "state": "sleeping",
                        "cycle": cycle,
                        "ts": ts,
                        "dry_run": self.dry_run,
                        "session": session,
                        "reason": "Market closed — weekend",
                        "symbols": symbols_status,
                        "connection": "OK",
                    }
                )
                time.sleep(300)
                continue

            # ── Account info ─────────────────────────────────────────────────
            acct = self._get_account()
            balance = acct.balance if acct else 1000.0
            equity = acct.equity if acct else 1000.0
            acct_info = {
                "balance": round(balance, 2),
                "equity": round(equity, 2),
                "margin": round(getattr(acct, "margin", 0), 2),
                "free": round(getattr(acct, "margin_free", balance), 2),
                "currency": getattr(acct, "currency", "USD"),
            }
            pnl = equity - balance

            # ── Fetch ALL open positions (one RPC call) ──────────────────────
            positions_by_sym = get_positions_by_symbol(self.bridge)
            total_open = sum(len(v) for v in positions_by_sym.values())
            log.info(f"STATUS | Cycle #{cycle} | {session} | {ts}")
            log.info(
                f"ACCOUNT | Balance ${balance:.2f} | Equity ${equity:.2f} | Net {fmt_profit(pnl)} | Open positions {total_open}"
            )
            if total_open == 0:
                log.info("POSITIONS | none")

            # Build current ticket set and detect externally-closed positions
            _cur_tickets: dict[str, dict] = {}
            for sym_cfg in SYMBOLS:
                broker_sym = sym_cfg["broker"]
                disp = sym_cfg["display"]
                pos_list = positions_by_sym.get(broker_sym, [])
                for p in pos_list:
                    d = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
                    pr = getattr(p, "profit", 0)
                    op = getattr(p, "price_open", 0)
                    tk = str(getattr(p, "ticket", ""))
                    vol = getattr(p, "volume", 0.05)
                    log.info(
                        f"POSITION | {disp} {d} | entry @{op:.2f} | pnl {fmt_profit(pr)} | lot {vol:.2f}"
                    )
                    _cur_tickets[tk] = {"profit": pr, "direction": d, "sym_cfg": sym_cfg, "volume": getattr(p, "volume", 0.05)}

            # ── Pyramid hard sync: purge phantom tranches every cycle ────────
            # Queries MT5 directly via bridge to ensure pyramid state reflects
            # reality. Replaces the old force_reconcile approach.
            if self.pyramid:
                self.pyramid.hard_sync(
                    self.bridge, {s["display"]: s for s in SYMBOLS}
                )

            # Detect tickets that vanished since last cycle (closed by broker SL/TP)
            if _prev_tickets:
                for tk, info in _prev_tickets.items():
                    if tk not in _cur_tickets and not self.dry_run:
                        pr = info["profit"]
                        outcome = "WIN" if pr > 0 else "LOSS"
                        _sc = info["sym_cfg"]
                        log.info(
                            f"   [EXTERNAL CLOSE] #{tk} {_sc['display']} {info['direction']} "
                            f"{fmt_profit(pr)} → {outcome} (closed by broker)"
                        )
                        try:
                            _mem = self.memory
                            if _mem is None:
                                from learning.memory import TradeMemory
                                _mem = TradeMemory()
                            _pip = _sc["pip"]
                            _cs = _sc.get("contract_size", 100)
                            _vol = info.get("volume", 0.05)
                            _pv = _pip * _cs * _vol
                            _pips = pr / _pv if _pv > 0 else 0
                            # Pass symbol/direction so memory never records UNKNOWN
                            _mem.record_outcome(
                                tk, 0.0, round(_pips, 1), outcome,
                                symbol=_sc["display"],
                                direction=info["direction"],
                            )
                            # Notify pyramid manager
                            if self.pyramid:
                                self.pyramid.on_position_closed(_sc["display"], tk)
                            
                            self.state["total_trades"] += 1
                            if outcome == "WIN":
                                self.state["wins"] += 1
                                self._consec_losses[_sc["display"]] = 0
                            else:
                                self.state["losses"] += 1
                                self._loss_cooldown[_sc["broker"]] = time.time()
                                self._consec_losses[_sc["display"]] = (
                                    self._consec_losses.get(_sc["display"], 0) + 1
                                )
                            _save_streaks(self._consec_losses)
                            _save_state(self.state)
                        except Exception as _e:
                            log.warning(f"External close record error: {_e}")
            _prev_tickets = _cur_tickets

            # ── Profit Booking (partial closes at pip milestones) ─────────
            if self.capital:
                try:
                    booked = self.capital.book_profits(
                        bridge=self.bridge,
                        positions_by_sym=positions_by_sym,
                        symbols={s["display"]: s for s in SYMBOLS},
                        dry_run=self.dry_run,
                    )
                    if booked:
                        total_booked = sum(b.get("booked_usd", 0) for b in booked)
                        log.info(
                            f"   💵 PROFIT BOOKED: ${total_booked:.2f} across "
                            f"{len(booked)} partial close(s)"
                        )
                        time.sleep(0.5)
                        positions_by_sym = get_positions_by_symbol(self.bridge)
                except Exception as e:
                    log.warning(f"Capital book_profits error: {e}")

            # ── Smart exit checks (momentum reversal, adaptive TP) ────────────
            if self.smart_exit:
                try:
                    symbols_dict = {s["display"]: s for s in SYMBOLS}
                    smart_closed = self.smart_exit.evaluate_exits(
                        bridge=self.bridge,
                        positions_by_sym=positions_by_sym,
                        symbols=symbols_dict,
                        account_balance=balance,
                        state=self.state,
                        dry_run=self.dry_run,
                    )
                    if smart_closed:
                        _save_state(self.state)
                        time.sleep(1)
                        positions_by_sym = get_positions_by_symbol(self.bridge)
                except Exception as e:
                    log.warning(f"Smart exit error: {e}")

            # ── Check close conditions ───────────────────────────────────────
            closed_syms = check_and_close_positions(
                self.bridge,
                balance,
                positions_by_sym,
                self.state,
                self.dry_run,
                self.capital,
                getattr(self, "skill_mgr", None),
                self._loss_cooldown,
                self._consec_losses,
                memory=self.memory,
            )
            if closed_syms:
                _save_state(self.state)
                log.info(
                    f"   W:{self.state['wins']} L:{self.state['losses']} "
                    f"({self.state['total_trades']} total trades)"
                )
                # Notify pyramid manager of closed positions (reconciliation will handle the details)
                if self.pyramid:
                    for _csym in closed_syms:
                        _disp = next((s["display"] for s in SYMBOLS if s["broker"] == _csym), _csym)
                        self.pyramid.on_position_closed(_disp, "")
                # Refresh positions after closes
                time.sleep(1)
                positions_by_sym = get_positions_by_symbol(self.bridge)

            # ── Pyramid tranche checks (every cycle, not just analysis) ────────
            if self.pyramid and self.pyramid.active_pyramid_count() > 0:
                try:
                    _sym_lookup = {s["display"]: s for s in SYMBOLS}
                    pyramid_actions = self.pyramid.check_pyramids(self.bridge, _sym_lookup, positions_by_sym)
                    if pyramid_actions:
                        for pa in pyramid_actions:
                            log.info(
                                f"   🔺 PYRAMID {pa['symbol']} tranche {pa['tranche']}/10 "
                                f"@ {pa['price']:.2f} (+{pa['pips']:.1f}p) "
                                f"total={pa['total_lot']:.2f}"
                            )
                        time.sleep(0.5)
                        positions_by_sym = get_positions_by_symbol(self.bridge)
                except Exception as e:
                    log.warning(f"   [PYRAMID] Check error: {e}")

            # ── Analysis & new signals ───────────────────────────────────────
            run_analysis = now >= next_analysis
            if run_analysis:
                log.info(f"ANALYSIS | Starting market scan | session {session}")
                next_analysis = now + CONFIG["analysis_interval_s"]

                total_open = sum(len(v) for v in positions_by_sym.values())
                for sym_cfg in SYMBOLS:
                    broker_sym = sym_cfg["broker"]
                    disp = sym_cfg["display"]

                    current_pos = positions_by_sym.get(broker_sym, [])
                    can_open_new = (
                        len(current_pos) < CONFIG["max_trades_per_sym"]
                        and total_open < CONFIG["max_total_positions"]
                    )

                    if not can_open_new and not self.scaler:
                        log.info(
                            f"ACTION | {disp} | skip entry | max trades reached ({len(current_pos)})"
                        )
                        if disp in symbols_status:
                            symbols_status[disp]["positions"] = len(current_pos)
                        continue

                    if not can_open_new:
                        log.info(
                            f"ACTION | {disp} | entry full | checking scale only"
                        )

                    log.info(f"ANALYSIS | {disp} | fetching candles + live indicators")
                    tick = self.bridge.get_tick(broker_sym)
                    tf_data = self._fetch_candles(sym_cfg)
                    if not tf_data:
                        log.warning(f"ACTION | {disp} | skip analysis | no candle data")
                        continue
                    primary = tf_data.get("M15", list(tf_data.values())[0])
                    signal_data = self._run_analysis(sym_cfg, tf_data, tick)
                    direction = signal_data.get("direction", "HOLD")
                    confidence = float(signal_data.get("confidence", 0.0))
                    reason = signal_data.get("reason", "")
                    _ind = signal_data.get("indicators", {})
                    _fs  = signal_data.get("factor_scores", {})
                    _fib = signal_data.get("fibonacci_data", {})
                    _score = signal_data.get("score", 0)
                    # Stash indicators so SmartExitManager + next-cycle memory enrichment can use them
                    sym_cfg["_last_adx"] = _ind.get("adx", 0.0)
                    sym_cfg["_last_indicators"] = {
                        "direction": direction,
                        "adx":       _ind.get("adx", 0.0),
                        "rsi":       _ind.get("rsi", 50.0),
                    }

                    log.info(
                        f"SIGNAL | {disp} | {direction} {confidence:.0%} | score {_score:+.1f} | {_compact_text(reason)}"
                    )
                    log.info(
                        f"DETAIL | {disp} | trend D1={signal_data.get('d1_trend', '?')} H4={signal_data.get('h4_trend', '?')} "
                        f"H1={signal_data.get('h1_trend', '?')} M15={_ind.get('ema_trend', '?')}"
                    )
                    log.info(
                        f"DETAIL | {disp} | ADX {_ind.get('adx', 0):.1f} {_adx_regime(_ind.get('adx', 0))} | "
                        f"RSI {_ind.get('rsi', 50):.1f} | MACD {_ind.get('macd_signal', 'N/A')} | "
                        f"BB {_ind.get('bb_position', 'N/A')} | ATR {_ind.get('atr', 0):.5f}"
                    )
                    log.info(
                        f"DETAIL | {disp} | Price {_ind.get('price', 0):.5f} | Change {_ind.get('price_change', 0):+.3f}% | "
                        f"Stoch {_ind.get('stoch_k', 50):.0f}/{_ind.get('stoch_d', 50):.0f} {_ind.get('stoch_cross', '')} | "
                        f"Score {_score:+.1f} | Factors {_format_factor_summary(_fs)}"
                    )
                    if _fib and _fib.get('zone_label'):
                        log.info(f"DETAIL | {disp} | Fib {_compact_text(_fib['zone_label'], 96)}")

                    # Write 4-stage decision trace
                    try:
                        from core.decision_logger import write_trace
                        write_trace(cycle, disp, session, signal_data)
                    except Exception as _te:
                        log.debug(f"Trace write error: {_te}")

                    # Cache sparkline data
                    try:
                        candles_cache[disp] = {
                            "closes": primary["c"].tail(100).tolist(),
                            "updated": ts,
                        }
                        CANDLES_FILE.write_text(json.dumps(_sanitize(candles_cache)))
                    except Exception:
                        pass

                    # Update symbol status
                    symbols_status[disp] = {
                        "signal": direction,
                        "confidence": round(confidence, 4),
                        "reason": reason,
                        "session": session,
                        "ask": round(tick.ask, 5) if tick else None,
                        "bid": round(tick.bid, 5) if tick else None,
                        "positions": len(current_pos),
                        "indicators": signal_data.get("indicators", {}),
                        "h1_trend": signal_data.get("h1_trend", ""),
                        "h4_trend": signal_data.get("h4_trend", ""),
                        "broker_symbol": broker_sym,
                    }

                    # Cache signal for cross-symbol correlation
                    self._symbol_signals[disp] = {
                        "direction": direction,
                        "confidence": confidence,
                    }

                    # ── Fade Detection: block counter-trend entries on exhausted moves ──
                    # Target: ADX 40-55 + RSI mildly oversold/overbought (20-30 / 70-80)
                    #   → mature trend that is approaching exhaustion, likely to stall/reverse.
                    #
                    # Do NOT block:
                    #   RSI < 20 (SELL) or RSI > 80 (BUY) — this is CAPITULATION / PANIC, not
                    #   exhaustion. Extreme RSI + extreme ADX = continuation, not reversal.
                    #   Score ≤ -25 or ≥ +25 with conf ≥ 82% — full-stack alignment, respect it.
                    indicators = signal_data.get("indicators", {})
                    adx_val  = indicators.get("adx", 0)
                    rsi_val  = indicators.get("rsi", 50)
                    bb_pos   = indicators.get("bb_position", "MID")
                    _sig_score_raw = signal_data.get("score", 0)
                    fade_blocked = False

                    if can_open_new and direction in ("BUY", "SELL") and adx_val >= 20:
                        # Capitulation / panic exception: extreme RSI means continuation, not reversal
                        # RSI < 25 on SELL = deep panic selling (continuation likely)
                        # RSI > 75 on BUY  = extreme euphoria (continuation likely)
                        _is_capitulation_sell = direction == "SELL" and rsi_val < 25
                        _is_capitulation_buy  = direction == "BUY"  and rsi_val > 75
                        # Full-stack alignment exception: overwhelming evidence across all factors
                        _is_overwhelm = abs(_sig_score_raw) >= 20 and confidence >= 0.78

                        if not (_is_capitulation_sell or _is_capitulation_buy or _is_overwhelm):
                            if direction == "SELL" and adx_val > 40 and rsi_val < 30 and bb_pos in ("BELOW_MID", "BELOW_LOW"):
                                log.info(f"ACTION | {disp} | fade block | SELL vs exhausted downtrend (ADX={adx_val:.1f} RSI={rsi_val:.1f} BB={bb_pos})")
                                fade_blocked = True
                            elif direction == "BUY" and adx_val > 40 and rsi_val > 70 and bb_pos in ("ABOVE_MID", "ABOVE_HIGH"):
                                log.info(f"ACTION | {disp} | fade block | BUY vs exhausted uptrend (ADX={adx_val:.1f} RSI={rsi_val:.1f} BB={bb_pos})")
                                fade_blocked = True

                    # Loss cooldown: 10 min rest after a loss (was 3 min — too short, caused churn)
                    _LOSS_COOLDOWN_SECS = 600  # 10 minutes — prevents revenge trading
                    _cooldown_remaining = _LOSS_COOLDOWN_SECS - (time.time() - self._loss_cooldown.get(broker_sym, 0))
                    if can_open_new and _cooldown_remaining > 0:
                        log.info(f"ACTION | {disp} | cooldown after loss | wait {int(_cooldown_remaining)}s")
                        can_open_new = False

                    # Post-trade cooldown: split same-direction vs any-direction
                    # Same direction = chasing/revenge → 5min block
                    # Opposite direction = legit reversal → 90s block only
                    _last_t   = self._last_trade_time.get(broker_sym, 0)
                    _last_dir = self._last_trade_dir.get(broker_sym, None)
                    _elapsed  = time.time() - _last_t
                    if can_open_new and _last_t > 0:
                        if direction == _last_dir and _elapsed < self._TRADE_COOLDOWN_SAME_DIR:
                            _wait = int(self._TRADE_COOLDOWN_SAME_DIR - _elapsed)
                            log.info(f"ACTION | {disp} | same-dir cooldown ({direction}) | wait {_wait}s")
                            can_open_new = False
                        elif _elapsed < self._TRADE_COOLDOWN_ANY_DIR:
                            _wait = int(self._TRADE_COOLDOWN_ANY_DIR - _elapsed)
                            log.info(f"ACTION | {disp} | post-trade cooldown | wait {_wait}s")
                            can_open_new = False

                    # ── ADX: advisory not exclusionary ───────────────────────
                    # ADX tells us trend STRENGTH — not whether to trade.
                    # We only block on truly dead markets (ADX < 5 = no movement).
                    # Score threshold + confidence gate is the real filter.
                    _adx_val   = signal_data.get("indicators", {}).get("adx", 0)
                    _bb_pos    = signal_data.get("indicators", {}).get("bb_position", "MID")
                    _rsi_v     = signal_data.get("indicators", {}).get("rsi", 50)
                    _sig_score = abs(signal_data.get("score", 0))
                    # ADX comes directly from local analysis now

                    # Only block if ADX is truly dead (< 5) AND signal is weak (< 8)
                    _adx_ok = not (_adx_val < 5 and _sig_score < 8)
                    if not _adx_ok:
                        log.info(f"ACTION | {disp} | dead market | ADX {_adx_val:.1f} and score {_sig_score:.0f}")

                    # ── Circuit breaker: 4 consecutive losses → 1-cycle pause ─
                    _sym_consec = self._consec_losses.get(disp, 0)
                    _cb_until   = self._circuit_break_until.get(disp, 0)

                    if _cb_until > 0 and cycle >= _cb_until:
                        self._consec_losses[disp] = 0
                        self._circuit_break_until[disp] = 0
                        _save_streaks(self._consec_losses)
                        _sym_consec = 0
                        _cb_until   = 0
                        log.info(f"ACTION | {disp} | circuit breaker reset | trading resumed")

                    if can_open_new and _sym_consec >= 4:
                        if _cb_until == 0:
                            self._circuit_break_until[disp] = cycle + 1
                            _cb_until = cycle + 1
                        log.info(f"ACTION | {disp} | circuit breaker | {_sym_consec} losses, pausing 1 cycle")
                        can_open_new = False

                    # ── Phase 2.1: Adaptive Confidence Gating ──────────────────────
                    _sig_reason = signal_data.get("reason", "")
                    _regime_label = signal_data.get("factor_scores", {}).get("adx_regime", "")
                    _is_ranging   = _regime_label.startswith("RANGING")
                    _sess_cfg     = SESSION_CONFIG.get(session, {"lot_mult": 1.0, "min_conf": 0.48})

                    # 1. Base session/signal confidence
                    if "[Score override" in _sig_reason:
                        _base_conf = 0.45 if _is_ranging else 0.48
                    elif "[Indicator fallback]" in _sig_reason:
                        _base_conf = 0.45
                    else:
                        _base_conf = 0.45 if _is_ranging else _sess_cfg["min_conf"]

                    # 2. Full-regime modifier (replaces bare ADX ±5% logic)
                    #    STRONG_TREND  → lower gate (trend confirmation = higher edge)
                    #    SQUEEZE       → lower gate (breakout incoming)
                    #    RANGING_CHOP  → raise gate (low-quality signals)
                    #    RANGING_VOLATILE → slight raise (some edge but noisy)
                    _regime_mod = 0.0
                    if "STRONG_TREND" in _regime_label:
                        _regime_mod = -0.07   # ADX > 25, trending hard → be bold
                    elif "SQUEEZE" in _regime_label:
                        _regime_mod = -0.05   # BB squeeze → breakout imminent
                    elif "WEAK_TREND" in _regime_label:
                        _regime_mod = -0.02   # Developing trend → slight benefit
                    elif "RANGING_CHOP" in _regime_label:
                        _regime_mod = +0.05   # Chop → require stronger signal
                    elif "RANGING_VOLATILE" in _regime_label:
                        _regime_mod = +0.02   # Wide range → slight penalty
                    # backward compat: bare ADX mod if no regime label
                    elif _adx_val > 25:
                        _regime_mod = -0.05
                    elif _adx_val < 15:
                        _regime_mod = +0.05

                    # 3. Session historical win-rate modifier
                    #    If this symbol historically underperforms in this session,
                    #    raise the bar. If it outperforms, lower it slightly.
                    _sess_wr_mod = 0.0
                    if self.memory:
                        try:
                            _sess_wr = self.memory.get_session_win_rate(disp, session)
                            if _sess_wr:
                                _wr = _sess_wr["win_rate"]
                                if _wr < 0.40:
                                    _sess_wr_mod = +0.08   # < 40% WR → raise gate hard
                                    log.info(f"RISK | {disp} | session WR {_wr:.0%} in {session} → gate +8%")
                                elif _wr < 0.50:
                                    _sess_wr_mod = +0.04   # < 50% WR → raise gate
                                elif _wr > 0.70:
                                    _sess_wr_mod = -0.05   # > 70% WR → lower gate
                                elif _wr > 0.60:
                                    _sess_wr_mod = -0.02   # > 60% WR → slight benefit
                        except Exception:
                            pass

                    # 4. Loss-streak modifier removed from gate — circuit breaker handles it
                    _streak_mod = 0.0

                    # Calculate final adaptive gate (per-symbol minimum)
                    # 2026-04-30: silver requires 0.72 floor vs gold's 0.65
                    _per_sym_min = sym_cfg.get("min_confidence", 0.65)
                    _conf_gate = round(_base_conf + _regime_mod + _sess_wr_mod + _streak_mod, 3)
                    _conf_gate = max(_per_sym_min, min(0.85, _conf_gate))
                    log.info(
                        f"RISK | {disp} | gate {_conf_gate:.0%} | base {_base_conf:.0%} | "
                        f"per_sym_floor {_per_sym_min:.0%} | regime({_regime_label}) {_regime_mod:+.0%} | sess_wr {_sess_wr_mod:+.0%}"
                    )

                    # Per-symbol score threshold check (silver needs ±18 vs gold ±15)
                    _sym_score_min = sym_cfg.get("score_threshold", 15)
                    _sig_score_now = abs(signal_data.get("score", 0))
                    if _sig_score_now < _sym_score_min:
                        log.info(
                            f"ACTION | {disp} | skip entry | score {_sig_score_now:.1f} below "
                            f"per-symbol threshold {_sym_score_min}"
                        )
                        continue

                    # Per-symbol ADX minimum (gold 22, silver 28)
                    _sym_adx_min = sym_cfg.get("adx_min", 22)
                    _sig_adx = signal_data.get("indicators", {}).get("adx", 0)
                    if _sig_adx < _sym_adx_min:
                        log.info(
                            f"ACTION | {disp} | skip entry | ADX {_sig_adx:.1f} below "
                            f"per-symbol min {_sym_adx_min}"
                        )
                        continue

                    # ── Regime direction block ─────────────────────────────────────
                    # 2026-04-30: Tightened threshold 0.88→0.78. Anti-fade rule:
                    # never short a strong uptrend / long a strong downtrend
                    # unless confidence is genuinely high (78%+ vs old 88%).
                    # 88% was effectively unreachable, making rule cosmetic.
                    _regime_label_now = signal_data.get("factor_scores", {}).get("adx_regime", "")
                    _regime_blocked = False
                    _REGIME_OVERRIDE_CONF = 0.78
                    if direction == "SELL" and "STRONG_TREND_UP" in _regime_label_now:
                        if confidence < _REGIME_OVERRIDE_CONF:
                            _regime_blocked = True
                            log.info(f"ACTION | {disp} | regime_block | SELL blocked in STRONG_TREND_UP (conf {confidence:.0%} < {_REGIME_OVERRIDE_CONF:.0%})")
                    elif direction == "BUY" and "STRONG_TREND_DOWN" in _regime_label_now:
                        if confidence < _REGIME_OVERRIDE_CONF:
                            _regime_blocked = True
                            log.info(f"ACTION | {disp} | regime_block | BUY blocked in STRONG_TREND_DOWN (conf {confidence:.0%} < {_REGIME_OVERRIDE_CONF:.0%})")

                    if (
                        can_open_new
                        and direction in ("BUY", "SELL")
                        and confidence >= _conf_gate
                        and _adx_ok
                        and not fade_blocked
                        and not _regime_blocked
                    ):
                        # ── Run strategy filters ─────────────────────────────
                        lot_reduction = 1.0
                        if self.filter_chain:
                            # Build ATR history from M15 candle true-range
                            _atr_history = []
                            try:
                                _df = primary
                                if _df is not None and len(_df) > 20:
                                    _tr = (
                                        _df["h"] - _df["l"]
                                    ).abs().rolling(14).mean().dropna()
                                    _atr_history = _tr.tail(50).tolist()
                            except Exception:
                                pass
                            # Compute spread + ATR (pips) for SpreadFilter
                            _atr_raw = signal_data.get("indicators", {}).get("atr", 0) or 0
                            _atr_pips = (_atr_raw / sym_cfg["pip"]) if sym_cfg["pip"] > 0 else 0
                            try:
                                _tick_now = self.bridge.get_tick(broker_sym)
                                _spread_pips = (_tick_now.ask - _tick_now.bid) / sym_cfg["pip"]
                            except Exception:
                                _spread_pips = 0
                            filter_ctx = {
                                "session": session,
                                "confidence": confidence,
                                "indicators": signal_data.get("indicators", {}),
                                "hour_utc": datetime.now(timezone.utc).hour,
                                "atr_history": _atr_history,
                                "atr_pips": _atr_pips,
                                "spread_pips": _spread_pips,
                                "other_symbol_signal": self._get_other_signal(disp),
                                "xau_xag_correlation": 0,
                            }
                            allowed, veto_reasons = self.filter_chain.evaluate(
                                disp, direction, filter_ctx
                            )
                            lot_reduction = filter_ctx.get("_lot_reduction", 1.0)

                            if not allowed:
                                log.info(
                                    f"ACTION | {disp} | filtered | {'; '.join(veto_reasons)}"
                                )
                                _log_signal(
                                    broker_sym,
                                    direction,
                                    confidence,
                                    "; ".join(veto_reasons),
                                    "FILTERED",
                                )
                                if self.memory:
                                    self.memory.record_filtered(
                                        disp,
                                        direction,
                                        confidence,
                                        veto_reasons,
                                        signal_data.get("factor_scores"),
                                    )
                                continue

                        # ── Run skill evaluation ─────────────────────────────
                        skills_used = []
                        if self.skill_mgr:
                            skill_ctx = {
                                "session": session,
                                "adx": signal_data.get("indicators", {}).get("adx", 0),
                                "hour_utc": datetime.now(timezone.utc).hour,
                            }
                            skill_result = self.skill_mgr.evaluate_all(
                                disp, direction, skill_ctx
                            )
                            if not skill_result["allowed"]:
                                log.info(
                                    f"ACTION | {disp} | skill blocked | "
                                    f"{'; '.join(skill_result['reasons'])}"
                                )
                                _log_signal(
                                    broker_sym,
                                    direction,
                                    confidence,
                                    "; ".join(skill_result["reasons"]),
                                    "SKILL_BLOCKED",
                                )
                                continue
                            confidence = min(
                                confidence + skill_result["confidence_boost"], 0.95
                            )
                            skills_used = skill_result["skills_used"]

                        # ── Build order with ATR-based SL/TP ─────────────
                        atr = signal_data.get("indicators", {}).get("atr", 0)
                        # Inject balance for Kelly sizing (1% risk per trade)
                        try:
                            _acct_info = self.bridge.get_account_info()
                            sym_cfg["_account_balance"] = float(getattr(_acct_info, "balance", 0))
                        except Exception:
                            sym_cfg["_account_balance"] = 0
                        order = build_order_params(
                            sym_cfg,
                            tick,
                            direction,
                            confidence=confidence,
                            atr=atr,
                            lot_reduction=lot_reduction,
                            regime_data=signal_data.get("factor_scores"),
                        )

                        # ── PYRAMID SYSTEM: Force 0.01 lot for tranche 1 ──
                        # Pyramid manager controls all lot sizing across tranches
                        order["lot"] = 0.01

                        # Cache indicators for pyramid tranche checks
                        if self.pyramid:
                            self.pyramid.update_cached_indicators(
                                disp,
                                {
                                    "indicators": _ind,
                                    "score": _score,
                                    "confidence": confidence,
                                    "signal_direction": direction,
                                    "factor_scores": _fs,
                                },
                            )

                        if self.dry_run:
                            log.info(
                                f"ACTION | {disp} | dry run {direction} | lot {order['lot']} | sl {order['sl']} | tp {order['tp']} | conf {confidence:.0%}"
                            )
                            _log_signal(
                                broker_sym, direction, confidence, reason, "DRY_TRADE"
                            )
                        else:
                            # Skip if pyramid already active for this symbol
                            if self.pyramid and self.pyramid.has_active_pyramid(disp):
                                log.info(f"ACTION | {disp} | skip entry | pyramid already active")
                                continue

                            result = self.bridge.place_order(order)
                            if result and hasattr(result, "order"):
                                log.info(
                                    f"ACTION | {disp} | opened tranche 1/10 #{result.order} | {direction} @{order['price']:.2f} | "
                                    f"sl {order['sl']} | tp {order['tp']} | lot 0.01 | conf {confidence:.0%}"
                                )
                                _log_signal(
                                    broker_sym,
                                    direction,
                                    confidence,
                                    reason,
                                    "PYRAMID_START",
                                    ticket=result.order,
                                )

                                # ── Start pyramid session ────────────────────
                                if self.pyramid:
                                    self.pyramid.start_pyramid(
                                        symbol=disp,
                                        direction=direction,
                                        entry_price=order["price"],
                                        ticket=str(result.order),
                                        pip_size=sym_cfg["pip"],
                                        sl=order["sl"],
                                        tp=order["tp"],
                                        signal_context={
                                            "confidence": confidence,
                                            "factors": signal_data.get("factor_scores"),
                                            "conditions": {
                                                "session": session,
                                                "atr": atr,
                                                "adx": signal_data.get("indicators", {}).get("adx", 0),
                                                "rsi": signal_data.get("indicators", {}).get("rsi", 0),
                                                "h4_trend": signal_data.get("h4_trend", ""),
                                            },
                                            "skills_used": skills_used,
                                        },
                                    )

                                # ── Record in trade memory ───────────────────
                                if self.memory:
                                    self.memory.record_entry(
                                        ticket=str(result.order),
                                        symbol=disp,
                                        direction=direction,
                                        entry_price=order["price"],
                                        confidence=confidence,
                                        factors=signal_data.get("factor_scores"),
                                        conditions={
                                            "session": session,
                                            "atr": atr,
                                            "adx": signal_data.get(
                                                "indicators", {}
                                            ).get("adx", 0),
                                            "rsi": signal_data.get(
                                                "indicators", {}
                                            ).get("rsi", 0),
                                            "h4_trend": signal_data.get("h4_trend", ""),
                                        },
                                        skills_used=skills_used,
                                    )

                                self.state["total_trades"] += 1
                                _save_state(self.state)
                                # Record trade time + direction for split cooldown
                                self._last_trade_time[broker_sym] = time.time()
                                self._last_trade_dir[broker_sym] = direction
                                time.sleep(0.5)
                                positions_by_sym = get_positions_by_symbol(self.bridge)
                            else:
                                log.warning(f"ACTION | {disp} | order failed")
                    else:
                        if not can_open_new:
                            pass  # already logged above; don't spam DB for max-trades cases
                        elif direction == "HOLD":
                            log.info(f"ACTION | {disp} | HOLD | no trade setup")
                            _log_signal(
                                broker_sym, direction, confidence, reason, "HOLD"
                            )
                        elif fade_blocked:
                            log.info(f"ACTION | {disp} | skip entry | fade blocked (ADX={_adx_val:.1f} RSI={_rsi_v:.1f})")
                            _log_signal(broker_sym, direction, confidence, "fade_blocked", "LOW_CONF")
                        elif not _adx_ok:
                            log.info(f"ACTION | {disp} | skip entry | ADX too low ({_adx_val:.1f})")
                            _log_signal(broker_sym, direction, confidence, f"adx_low_{_adx_val:.1f}", "LOW_CONF")
                        else:
                            log.info(
                                f"ACTION | {disp} | skip entry | confidence {confidence:.0%} below gate {_conf_gate:.0%}"
                            )
                            _log_signal(
                                broker_sym, direction, confidence, reason, "LOW_CONF"
                            )

                    # ── Pyramid status logging (replaced old scaler) ──────────
                    if self.pyramid and self.pyramid.has_active_pyramid(disp):
                        _psess = self.pyramid.get_session(disp)
                        if _psess:
                            log.info(
                                f"PYRAMID | {disp} | {_psess.tranche_count}/10 tranches | total lot {_psess.total_lot:.2f} | avg entry {_psess.avg_entry_price:.2f}"
                            )

            # ── Write dashboard status ────────────────────────────────────────
            # Build open-positions list for dashboard
            open_positions_list = []
            for sym_cfg in SYMBOLS:
                for p in positions_by_sym.get(sym_cfg["broker"], []):
                    op = getattr(p, "price_open", 0)
                    pr = getattr(p, "profit", 0)
                    d = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
                    open_positions_list.append(
                        {
                            "ticket": getattr(p, "ticket", "?"),
                            "symbol": sym_cfg["display"],
                            "direction": d,
                            "volume": getattr(p, "volume", 0),
                            "open_price": op,
                            "profit": round(pr, 2),
                            "sl": getattr(p, "sl", 0),
                            "tp": getattr(p, "tp", 0),
                        }
                    )

            next_analysis_in = max(0, int(next_analysis - time.time()))
            primary_sym = SYMBOLS[0]["display"]
            primary_data = symbols_status.get(primary_sym, {})

            _write_status(
                {
                    "state": "running",
                    "cycle": cycle,
                    "ts": ts,
                    "dry_run": self.dry_run,
                    "session": session,
                    "connection": "OK",
                    "account": acct_info,
                    "open_positions": open_positions_list,
                    "stats": {
                        "total_trades": self.state["total_trades"],
                        "wins": self.state["wins"],
                        "losses": self.state["losses"],
                        "win_rate": (
                            round(
                                self.state["wins"] / self.state["total_trades"] * 100, 1
                            )
                            if self.state["total_trades"] > 0
                            else 0
                        ),
                        "scale_stats": (
                            self.scaler.get_scale_stats() if self.scaler else {}
                        ),
                        "capital": (self.capital.get_summary() if self.capital else {}),
                        "smart_exit": (
                            self.smart_exit.get_exit_stats() if self.smart_exit else {}
                        ),
                    },
                    "next_analysis_in": next_analysis_in,
                    # Flat fields for backward compat
                    "symbol": primary_sym,
                    "signal": primary_data.get("signal", "HOLD"),
                    "confidence": primary_data.get("confidence", 0),
                    "reason": primary_data.get("reason", ""),
                    "indicators": primary_data.get("indicators", {}),
                    "h1_trend": primary_data.get("h1_trend", ""),
                    "h4_trend": primary_data.get("h4_trend", ""),
                    "ask": primary_data.get("ask"),
                    "bid": primary_data.get("bid"),
                    "symbols": symbols_status,
                }
            )

            _save_state(self.state)

            # ── Rapid skill learning (every 5 cycles ≈ every 100s) ───────
            if self.memory and self.skill_mgr and cycle % 200 == 0:
                try:
                    outcomes = self.memory.get_recent_outcomes(hours=24)
                    if len(outcomes) >= 5:  # need meaningful data to improve
                        for skill_info in self.skill_mgr.list_skills():
                            self.skill_mgr.improve_skill(
                                skill_info["name"], outcomes[:5]
                            )
                        self.skill_mgr.invalidate_cache()
                        log.debug(
                            f"[SKILLS] Rapid learning: {len(outcomes)} recent trades"
                        )
                except Exception as e:
                    log.debug(f"Rapid skill learning error: {e}")

            # ── Deep self-improvement review (daily / every 100 cycles) ──
            if self.improver and cycle % 100 == 0:
                try:
                    if self.improver.should_run_review():
                        self.improver.daily_review()
                        if self.skill_mgr:
                            self.skill_mgr.invalidate_cache()
                except Exception as e:
                    log.warning(f"Self-improvement error: {e}")

            secs = CONFIG["monitor_interval_s"]
            log.info(
                f"NEXT | sleep {secs}s | analysis in {next_analysis_in}s | next cycle #{cycle + 1}"
            )
            time.sleep(secs)

        # ── Shutdown ──────────────────────────────────────────────────────────
        log.info("\n🛑 Trader stopped. Disconnecting...")
        try:
            self.bridge.disconnect()
        except Exception:
            pass
        _save_state(self.state)
        log.info(
            f"📊 Session stats: {self.state['total_trades']} trades | "
            f"W:{self.state['wins']} L:{self.state['losses']}"
        )


# ── CLI ──────────────────────────────────────────────────────────────────────


def cmd_close_all():
    """Close all open positions and exit."""
    log.info("Closing all open positions...")
    bridge = make_bridge()
    if not connect_with_retry(bridge):
        log.error("Cannot connect")
        return
    positions = bridge.get_open_positions()
    if not positions:
        log.info("No open positions.")
        return
    for p in positions:
        d = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
        pr = getattr(p, "profit", 0)
        ok = bridge.close_position(p.ticket)
        log.info(
            f"  #{p.ticket} {p.symbol} {d} profit={pr:+.2f} → {'closed' if ok else 'FAILED'}"
        )
    acct = bridge.get_account_info()
    if acct:
        log.info(f"Final balance: ${acct.balance:.2f}")
    bridge.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous Trader — XAUUSD + XAGUSD")
    parser.add_argument(
        "--dry", action="store_true", help="Paper-trade (no real orders)"
    )
    parser.add_argument(
        "--close", action="store_true", help="Close all positions and exit"
    )
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)  # always run from project dir

    if args.close:
        cmd_close_all()
    else:
        # ── Single-instance lock: prevent duplicate trader processes ──────────
        LOCK_FILE = Path("/tmp/trading_bot_main.lock")
        import fcntl

        def _is_pid_alive(pid_str):
            try:
                pid = int(pid_str.strip())
                os.kill(pid, 0)
                return True
            except (ValueError, ProcessLookupError, PermissionError):
                return False

        _lock_fh = open(LOCK_FILE, "w")
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fh.write(str(os.getpid()))
            _lock_fh.flush()
        except BlockingIOError:
            # Check if the locking process is actually still alive
            try:
                existing_pid = Path("/tmp/trading_bot_pid.txt").read_text().strip()
            except Exception:
                existing_pid = "?"
            if _is_pid_alive(existing_pid):
                print(f"❌ Trader already running (PID {existing_pid}). Exiting.")
                raise SystemExit(1)
            else:
                # Stale lock — take over
                LOCK_FILE.unlink(missing_ok=True)
                _lock_fh = open(LOCK_FILE, "w")
                fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _lock_fh.write(str(os.getpid()))
                _lock_fh.flush()

        # Write PID to separate readable file
        Path("/tmp/trading_bot_pid.txt").write_text(str(os.getpid()))

        trader = ContinuousTrader(dry_run=args.dry)
        trader.run()
