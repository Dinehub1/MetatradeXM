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
import logging.handlers
import argparse
import signal
from datetime import datetime, timezone
from pathlib import Path

# ── Environment loading ──────────────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent
STATUS_FILE  = ROOT / "bot_status.json"
CANDLES_FILE = ROOT / "candles_cache.json"
STATE_FILE   = ROOT / "trader_state.json"   # persists cycle count + trade IDs
LOG_FILE     = ROOT / "trading.log"

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    # NOTE: systemd already captures stdout → trading.log via StandardOutput=append:
    # Using ONLY RotatingFileHandler here would cause duplicates.
    # Use one StreamHandler (stdout) and let systemd write it once to file.
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("trader")

# ── Silence noisy third-party loggers ────────────────────────────────────────
for _noisy in ("socketio", "engineio", "metaapi_cloud_sdk", "asyncio",
               "urllib3", "requests", "websockets", "aiohttp",
               "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    {"broker": "GOLD.i#",   "display": "XAUUSD", "pip": 0.10,
     "sl_pips": 150, "tp_pips": 300, "lot": 0.01},
    {"broker": "SILVER.i#", "display": "XAGUSD", "pip": 0.01,
     "sl_pips": 50,  "tp_pips": 100, "lot": 0.01},
]

CONFIG = {
    "monitor_interval_s": 20,       # check positions every 20s (faster response)
    "analysis_interval_s": 60,      # full AI analysis every 60s (was 120)
    "profit_close_pct":   3.0,      # close trade at +3% account profit (let winners run)
    "loss_close_pct":     0.8,      # close trade at -0.8% account loss (cut faster)
    "min_confidence":     0.45,     # minimum AI confidence to trade (calibrated)
    "max_trades_per_sym": 3,        # allow up to 3 positions per symbol (scaling)
    "dry_run":            False,    # live trading
    "use_ai":             True,     # use Ollama AI analysis
    "max_reconnect_attempts": 5,
    "reconnect_backoff_s":    10,
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _sanitize(obj):
    """Make data JSON-safe (numpy types, NaN, Inf → Python builtins / None)."""
    import math
    try:
        import numpy as np
        if isinstance(obj, np.bool_):   return bool(obj)
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj); return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(obj, np.ndarray): return [_sanitize(x) for x in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):  return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [_sanitize(v) for v in obj]
    return obj

def _write_status(data: dict):
    try:
        STATUS_FILE.write_text(json.dumps(_sanitize(data), indent=2))
    except Exception as e:
        log.warning(f"Status write error: {e}")

def _log_signal(symbol: str, direction: str, confidence: float,
                reason: str, action: str, ticket: int = 0):
    """Write signal to trades.db for dashboard display."""
    import sqlite3
    DB_FILE = ROOT / "trades.db"
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
                (datetime.now(timezone.utc).isoformat(), symbol, direction,
                 round(confidence, 4), reason[:200], action, ticket)
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

def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

def is_forex_market_open() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    wd, h = now.weekday(), now.hour
    if wd == 5:              return False, "MARKET_CLOSED"   # Saturday
    if wd == 4 and h >= 22: return False, "MARKET_CLOSED"   # Fri night
    if wd == 6 and h < 22:  return False, "MARKET_CLOSED"   # Sunday
    if 7  <= h < 13: return True, "LONDON"
    if 13 <= h < 16: return True, "LONDON_NY_OVERLAP"
    if 16 <= h < 22: return True, "NEW_YORK"
    return True, "ASIAN"

def fmt_profit(p: float) -> str:
    arrow = "▲" if p >= 0 else "▼"
    color = "\033[92m" if p >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}{arrow} ${p:+.2f}{reset}"

# ── Bridge factory with auto-reconnect ──────────────────────────────────────

def make_bridge():
    token      = os.environ.get("METAAPI_TOKEN", "")
    account_id = os.environ.get("METAAPI_ACCOUNT_ID", "")
    if token and account_id:
        from metaapi_bridge import MetaApiBridge
        return MetaApiBridge(token, account_id)
    else:
        from mt5_bridge import MT5Bridge
        return MT5Bridge()

def connect_with_retry(bridge, max_attempts: int = 5) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            log.info(f"🔌 Connecting to MetaTrader (attempt {attempt}/{max_attempts})...")
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
    for p in (all_pos or []):
        sym = getattr(p, "symbol", "")
        by_sym.setdefault(sym, []).append(p)
    return by_sym

# ── Per-ticket peak profit tracking (persistent trailing SL) ────────────────
_PEAK_FILE = ROOT / "peak_profits.json"

def _load_peaks() -> dict:
    try:
        if _PEAK_FILE.exists():
            return json.loads(_PEAK_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_peaks(d: dict):
    try:
        _PEAK_FILE.write_text(json.dumps(d))
    except Exception:
        pass

_peak_profit: dict = _load_peaks()   # ticket → peak profit seen

def check_and_close_positions(bridge, account_balance: float, positions_by_sym: dict,
                               state: dict, dry_run: bool) -> list:
    """
    Close positions that hit profit or loss targets, OR if trailing SL is triggered.
    Trailing SL: once profit reaches 1% of balance, lock in 50% of peak profit.
    Returns list of closed broker symbols.
    """
    closed = []
    profit_target  = account_balance * (CONFIG["profit_close_pct"] / 100)
    loss_limit     = account_balance * (CONFIG["loss_close_pct"] / 100)
    trail_trigger  = account_balance * 0.5 / 100   # start trailing at +0.5%
    trail_lock_pct = 0.50                           # lock 50% of peak profit

    for sym_cfg in SYMBOLS:
        broker_sym = sym_cfg["broker"]
        positions  = positions_by_sym.get(broker_sym, [])
        for pos in positions:
            profit = getattr(pos, "profit", 0)
            ticket = str(getattr(pos, "ticket", "?"))
            direction = "BUY" if getattr(pos, "type", 1) == 0 else "SELL"

            # Track peak profit for trailing SL (persisted to disk)
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
                log.info(f"🎯 Closing #{ticket} {sym_cfg['display']} {direction} "
                         f"{fmt_profit(profit)} — {reason}")
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

                        # Cleanup peak profit tracking
                        _peak_profit.pop(ticket, None)
                        _save_peaks(_peak_profit)

                        # Record outcome in trade memory
                        try:
                            from memory import TradeMemory
                            mem = TradeMemory()
                            pip_size = sym_cfg["pip"]
                            open_price = getattr(pos, "price_open", 0)
                            pips = profit / (pip_size * 10 * getattr(pos, "volume", 0.01))
                            mem.record_outcome(str(ticket), 0, round(pips, 1), outcome)
                        except Exception as e:
                            log.warning(f"Memory record error: {e}")
                else:
                    log.info(f"   [DRY RUN] Would close #{ticket}")
                    closed.append(broker_sym)

    return closed

def build_order_params(sym_cfg: dict, tick, direction: str,
                       confidence: float = 0.55, atr: float = 0,
                       lot_reduction: float = 1.0) -> dict:
    pip = sym_cfg["pip"]
    price = tick.ask if direction == "BUY" else tick.bid
    digits = 2 if pip >= 0.01 else 5

    # ── ATR-based dynamic SL/TP ──────────────────────────────────────────
    if atr > 0:
        sl_pips = max(int(atr * 2.5 / pip), sym_cfg["sl_pips"] // 3)
        tp_pips = max(int(atr * 3.5 / pip), sym_cfg["tp_pips"] // 3)
    else:
        sl_pips = sym_cfg["sl_pips"]
        tp_pips = sym_cfg["tp_pips"]

    if direction == "BUY":
        sl = round(price - sl_pips * pip, digits)
        tp = round(price + tp_pips * pip, digits)
    else:
        sl = round(price + sl_pips * pip, digits)
        tp = round(price - tp_pips * pip, digits)

    # ── Confidence-scaled position sizing ─────────────────────────────────
    if confidence >= 0.80:
        conf_mult = 1.0
    elif confidence >= 0.65:
        conf_mult = 0.7
    elif confidence >= 0.55:
        conf_mult = 0.5
    else:
        conf_mult = 0.3

    lot = round(max(sym_cfg["lot"] * conf_mult * lot_reduction, 0.01), 2)

    return {
        "symbol":    sym_cfg["broker"],
        "direction": direction,
        "lot":       lot,
        "price":     price,
        "sl":        sl,
        "tp":        tp,
        "sl_pips":   sl_pips,
        "tp_pips":   tp_pips,
        "comment":   f"CT-{direction}-c{confidence:.0%}",
    }

# ── Main trading cycle ───────────────────────────────────────────────────────

class ContinuousTrader:
    def __init__(self, dry_run: bool = False):
        self.dry_run   = dry_run or CONFIG["dry_run"]
        self.state     = _load_state()
        self.bridge    = make_bridge()
        self.connected = False
        self._stop     = False
        self._last_analysis = 0   # epoch seconds

        # ── Self-improving systems ──────────────────────────────────────
        try:
            from memory import TradeMemory
            self.memory = TradeMemory()
            log.info("  [MEMORY] Trade memory system initialized")
        except Exception as e:
            log.warning(f"  [MEMORY] Failed to init: {e}")
            self.memory = None

        try:
            from skill_manager import SkillManager
            self.skill_mgr = SkillManager()
            skills = self.skill_mgr.list_skills()
            log.info(f"  [SKILLS] Loaded {len(skills)} trading skills")
        except Exception as e:
            log.warning(f"  [SKILLS] Failed to init: {e}")
            self.skill_mgr = None

        try:
            from strategy_filters import FilterChain
            self.filter_chain = FilterChain()
            log.info(f"  [FILTERS] Filter chain initialized")
        except Exception as e:
            log.warning(f"  [FILTERS] Failed to init: {e}")
            self.filter_chain = None

        try:
            from self_improver import PerformanceAnalyzer
            self.improver = PerformanceAnalyzer(self.memory, self.skill_mgr)
            log.info(f"  [IMPROVE] Self-improvement engine initialized")
        except Exception as e:
            log.warning(f"  [IMPROVE] Failed to init: {e}")
            self.improver = None

        try:
            from position_scaler import PositionScaler
            self.scaler = PositionScaler()
            log.info(f"  [SCALER] Position scaling system initialized")
        except Exception as e:
            log.warning(f"  [SCALER] Failed to init: {e}")
            self.scaler = None

        # Cache for cross-symbol correlation
        self._symbol_signals = {}

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT,  self._handle_stop)
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
        self.connected = connect_with_retry(self.bridge,
                                            CONFIG["max_reconnect_attempts"])
        return self.connected

    def _get_account(self):
        try:
            return self.bridge.get_account_info()
        except Exception as e:
            log.warning(f"Account info error: {e}")
            return None

    def _run_analysis(self, sym_cfg: dict, tf_data: dict, tick) -> dict:
        """Run multi-timeframe analysis with Ollama AI + memory context."""
        try:
            from analyzer import MarketAnalyzer
            analyzer = MarketAnalyzer(use_claude=CONFIG["use_ai"])

            # Prefetch memory context for AI reasoning
            memory_context = ""
            if self.memory:
                try:
                    memory_context = self.memory.prefetch_context(sym_cfg["display"])
                except Exception as e:
                    log.warning(f"Memory prefetch error: {e}")

            signal_data = analyzer.analyze(tf_data, tick, sym_cfg["display"],
                                           memory_context=memory_context)
            return signal_data
        except Exception as e:
            log.warning(f"Analysis error for {sym_cfg['display']}: {e}")
            return {"direction": "HOLD", "confidence": 0.0,
                    "reason": f"Analysis failed: {e}"}

    def _fetch_candles(self, sym_cfg: dict) -> dict:
        broker_sym = sym_cfg["broker"]
        tf_data = {}
        for tf in ["M15", "H1", "H4"]:
            count = 500 if tf == "M15" else 200
            try:
                df = self.bridge.get_candles(broker_sym, tf, count)
                if df is not None and len(df) > 30:
                    tf_data[tf] = df
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
        log.info(f"  Profit target: +{CONFIG['profit_close_pct']}% | Loss limit: -{CONFIG['loss_close_pct']}%")
        log.info("=" * 60)

        if not self._ensure_connected():
            log.error("Cannot start — MetaTrader connection failed")
            return

        # Print initial account info
        acct = self._get_account()
        if acct:
            log.info(f"  Account: #{acct.login} ({acct.server})")
            log.info(f"  Balance: ${acct.balance:.2f} | Equity: ${acct.equity:.2f}")

        self.state["cycle"] = self.state.get("cycle", 0)
        cycle = self.state["cycle"]

        symbols_status = {}
        candles_cache  = {}
        next_analysis  = 0   # run immediately on first cycle

        while not self._stop:
            now = time.time()
            cycle += 1
            self.state["cycle"] = cycle
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"\n{'─'*60}")
            log.info(f"🔄 Cycle #{cycle}  [{ts}]")

            # ── Connection health check ──────────────────────────────────────
            if not self._ensure_connected():
                log.error("❌ MetaTrader disconnected — retrying next cycle")
                _write_status({
                    "state": "reconnecting", "cycle": cycle, "ts": ts,
                    "dry_run": self.dry_run, "symbols": symbols_status,
                    "connection": "DISCONNECTED",
                })
                time.sleep(CONFIG["monitor_interval_s"])
                self.bridge = make_bridge()   # fresh bridge
                continue

            # ── Market hours gate ────────────────────────────────────────────
            market_open, session = is_forex_market_open()
            if not market_open:
                log.info(f"💤 Market CLOSED ({session}) — sleeping 5 min")
                _write_status({
                    "state": "sleeping", "cycle": cycle, "ts": ts,
                    "dry_run": self.dry_run, "session": session,
                    "reason": "Market closed — weekend",
                    "symbols": symbols_status, "connection": "OK",
                })
                time.sleep(300)
                continue

            # ── Account info ─────────────────────────────────────────────────
            acct = self._get_account()
            balance  = acct.balance  if acct else 1000.0
            equity   = acct.equity   if acct else 1000.0
            acct_info = {
                "balance": round(balance, 2),
                "equity":  round(equity, 2),
                "margin":  round(getattr(acct, "margin", 0), 2),
                "free":    round(getattr(acct, "margin_free", balance), 2),
                "currency": getattr(acct, "currency", "USD"),
            }
            pnl = equity - balance
            log.info(f"💰 Balance: ${balance:.2f} | Equity: ${equity:.2f} | P&L: {fmt_profit(pnl)}")

            # ── Fetch ALL open positions (one RPC call) ──────────────────────
            positions_by_sym = get_positions_by_symbol(self.bridge)
            total_open = sum(len(v) for v in positions_by_sym.values())
            log.info(f"📊 Open positions: {total_open}")

            for sym_cfg in SYMBOLS:
                broker_sym = sym_cfg["broker"]
                disp       = sym_cfg["display"]
                pos_list   = positions_by_sym.get(broker_sym, [])
                for p in pos_list:
                    d = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
                    pr = getattr(p, "profit", 0)
                    op = getattr(p, "price_open", 0)
                    log.info(f"   {disp} {d} @{op:.2f} {fmt_profit(pr)}")

            # ── Check close conditions ───────────────────────────────────────
            closed_syms = check_and_close_positions(
                self.bridge, balance, positions_by_sym, self.state, self.dry_run)
            if closed_syms:
                _save_state(self.state)
                log.info(f"   W:{self.state['wins']} L:{self.state['losses']} "
                         f"({self.state['total_trades']} total trades)")
                # Refresh positions after closes
                time.sleep(1)
                positions_by_sym = get_positions_by_symbol(self.bridge)

            # ── Analysis & new signals ───────────────────────────────────────
            run_analysis = (now >= next_analysis)
            if run_analysis:
                log.info(f"📊 Running market analysis  ({session})")
                next_analysis = now + CONFIG["analysis_interval_s"]

                for sym_cfg in SYMBOLS:
                    broker_sym = sym_cfg["broker"]
                    disp       = sym_cfg["display"]

                    current_pos = positions_by_sym.get(broker_sym, [])
                    can_open_new = len(current_pos) < CONFIG["max_trades_per_sym"]

                    if not can_open_new and not self.scaler:
                        log.info(f"   ⏸  {disp}: max trades reached ({len(current_pos)})")
                        if disp in symbols_status:
                            symbols_status[disp]["positions"] = len(current_pos)
                        continue

                    if not can_open_new:
                        log.info(f"   ⏸  {disp}: max trades reached — checking scale only")

                    log.info(f"   🔍 Analyzing {disp}...")
                    tf_data = self._fetch_candles(sym_cfg)
                    if not tf_data:
                        log.warning(f"   ⚠️  {disp}: no candle data")
                        continue

                    primary = tf_data.get("M15", list(tf_data.values())[0])
                    tick    = self.bridge.get_tick(broker_sym)

                    signal_data = self._run_analysis(sym_cfg, tf_data, tick)
                    direction   = signal_data.get("direction", "HOLD")
                    confidence  = signal_data.get("confidence", 0.0)
                    reason      = signal_data.get("reason", "")

                    log.info(f"   {disp}: {direction:4s} conf={confidence:.0%} | {reason[:55]}")

                    # Cache sparkline data
                    try:
                        candles_cache[disp] = {
                            "closes":  primary["c"].tail(100).tolist(),
                            "updated": ts,
                        }
                        CANDLES_FILE.write_text(json.dumps(_sanitize(candles_cache)))
                    except Exception:
                        pass

                    # Update symbol status
                    symbols_status[disp] = {
                        "signal":      direction,
                        "confidence":  round(confidence, 4),
                        "reason":      reason,
                        "session":     session,
                        "ask":         round(tick.ask, 5) if tick else None,
                        "bid":         round(tick.bid, 5) if tick else None,
                        "positions":   len(current_pos),
                        "indicators":  signal_data.get("indicators", {}),
                        "h1_trend":    signal_data.get("h1_trend", ""),
                        "h4_trend":    signal_data.get("h4_trend", ""),
                        "broker_symbol": broker_sym,
                    }

                    # Cache signal for cross-symbol correlation
                    self._symbol_signals[disp] = {
                        "direction": direction,
                        "confidence": confidence,
                    }

                    # Place trade if signal qualifies AND no existing position
                    if (can_open_new and
                            direction in ("BUY", "SELL") and
                            confidence >= CONFIG["min_confidence"]):

                        # ── Run strategy filters ─────────────────────────────
                        lot_reduction = 1.0
                        if self.filter_chain:
                            filter_ctx = {
                                "session": session,
                                "indicators": signal_data.get("indicators", {}),
                                "hour_utc": datetime.now(timezone.utc).hour,
                                "atr_history": [],  # TODO: populate from candle data
                                "other_symbol_signal": self._get_other_signal(disp),
                                "xau_xag_correlation": 0,  # TODO: compute from data
                            }
                            allowed, veto_reasons = self.filter_chain.evaluate(
                                disp, direction, filter_ctx)
                            lot_reduction = filter_ctx.get("_lot_reduction", 1.0)

                            if not allowed:
                                log.info(f"   🚫 {disp}: FILTERED — {'; '.join(veto_reasons)}")
                                _log_signal(broker_sym, direction, confidence,
                                            '; '.join(veto_reasons), "FILTERED")
                                if self.memory:
                                    self.memory.record_filtered(
                                        disp, direction, confidence, veto_reasons,
                                        signal_data.get("factor_scores"))
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
                                disp, direction, skill_ctx)
                            if not skill_result["allowed"]:
                                log.info(f"   🚫 {disp}: SKILL BLOCKED — "
                                         f"{'; '.join(skill_result['reasons'])}")
                                _log_signal(broker_sym, direction, confidence,
                                            '; '.join(skill_result["reasons"]), "SKILL_BLOCKED")
                                continue
                            confidence = min(confidence + skill_result["confidence_boost"], 0.95)
                            skills_used = skill_result["skills_used"]

                        # ── Build order with ATR-based SL/TP ─────────────────
                        atr = signal_data.get("indicators", {}).get("atr", 0)
                        order = build_order_params(
                            sym_cfg, tick, direction,
                            confidence=confidence, atr=atr,
                            lot_reduction=lot_reduction)

                        if self.dry_run:
                            log.info(f"   📝 DRY RUN {disp} {direction} "
                                     f"lot={order['lot']} sl={order['sl']} "
                                     f"tp={order['tp']} conf={confidence:.0%}")
                            _log_signal(broker_sym, direction, confidence, reason, "DRY_TRADE")
                        else:
                            result = self.bridge.place_order(order)
                            if result and hasattr(result, "order"):
                                log.info(f"   💰 ORDER #{result.order} — "
                                         f"{disp} {direction} @{order['price']:.2f} "
                                         f"SL={order['sl']} TP={order['tp']} "
                                         f"lot={order['lot']} conf={confidence:.0%}")
                                _log_signal(broker_sym, direction, confidence,
                                            reason, "TRADE", ticket=result.order)

                                # ── Record in trade memory ───────────────────
                                if self.memory:
                                    self.memory.record_entry(
                                        ticket=str(result.order),
                                        symbol=disp,
                                        direction=direction,
                                        entry_price=order['price'],
                                        confidence=confidence,
                                        factors=signal_data.get("factor_scores"),
                                        conditions={
                                            "session": session,
                                            "atr": atr,
                                            "adx": signal_data.get("indicators", {}).get("adx", 0),
                                            "rsi": signal_data.get("indicators", {}).get("rsi", 0),
                                            "h4_trend": signal_data.get("h4_trend", ""),
                                        },
                                        skills_used=skills_used,
                                    )

                                self.state["total_trades"] += 1
                                _save_state(self.state)
                                time.sleep(0.5)
                                positions_by_sym = get_positions_by_symbol(self.bridge)
                            else:
                                log.warning(f"   ❌ {disp} order failed")
                    else:
                        if not can_open_new:
                            pass  # already logged above; don't spam DB for max-trades cases
                        elif direction == "HOLD":
                            log.info(f"   ⏭  {disp}: HOLD — no trade")
                            _log_signal(broker_sym, direction, confidence, reason, "HOLD")
                        else:
                            log.info(f"   ⏭  {disp}: confidence too low ({confidence:.0%})")
                            _log_signal(broker_sym, direction, confidence, reason, "LOW_CONF")

                    # ── Position scaling (pyramiding) ────────────────────────
                    if self.scaler and current_pos:
                        try:
                            scales = self.scaler.evaluate(
                                positions=current_pos,
                                account=acct,
                                signal_data=signal_data,
                                sym_cfg=sym_cfg,
                                session=session,
                                bridge=self.bridge,
                            )
                            if scales:
                                for sc in scales:
                                    log.info(f"   📈 SCALED {disp} #{sc['scale_ticket']} "
                                             f"{sc['direction']} +{sc['lot']}lot — {sc['reason']}")
                                    _log_signal(broker_sym, sc["direction"], confidence,
                                                sc["reason"], "SCALED", ticket=sc["scale_ticket"])
                                    symbols_status.setdefault(disp, {})["last_scale"] = {
                                        "ticket": sc["scale_ticket"],
                                        "lot": sc["lot"],
                                        "ts": ts,
                                    }
                                time.sleep(0.5)
                                positions_by_sym = get_positions_by_symbol(self.bridge)
                        except Exception as e:
                            log.warning(f"   [SCALER] Error for {disp}: {e}")

            # ── Write dashboard status ────────────────────────────────────────
            # Build open-positions list for dashboard
            open_positions_list = []
            for sym_cfg in SYMBOLS:
                for p in positions_by_sym.get(sym_cfg["broker"], []):
                    op = getattr(p, "price_open", 0)
                    pr = getattr(p, "profit", 0)
                    d  = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
                    open_positions_list.append({
                        "ticket":      getattr(p, "ticket", "?"),
                        "symbol":      sym_cfg["display"],
                        "direction":   d,
                        "volume":      getattr(p, "volume", 0),
                        "open_price":  op,
                        "profit":      round(pr, 2),
                        "sl":          getattr(p, "sl", 0),
                        "tp":          getattr(p, "tp", 0),
                    })

            next_analysis_in = max(0, int(next_analysis - time.time()))
            primary_sym  = SYMBOLS[0]["display"]
            primary_data = symbols_status.get(primary_sym, {})

            _write_status({
                "state":         "running",
                "cycle":         cycle,
                "ts":            ts,
                "dry_run":       self.dry_run,
                "session":       session,
                "connection":    "OK",
                "account":       acct_info,
                "open_positions": open_positions_list,
                "stats": {
                    "total_trades": self.state["total_trades"],
                    "wins":         self.state["wins"],
                    "losses":       self.state["losses"],
                    "win_rate": (
                        round(self.state["wins"] / self.state["total_trades"] * 100, 1)
                        if self.state["total_trades"] > 0 else 0),
                    "scale_stats": (
                        self.scaler.get_scale_stats() if self.scaler else {}),
                },
                "next_analysis_in": next_analysis_in,
                # Flat fields for backward compat
                "symbol":     primary_sym,
                "signal":     primary_data.get("signal", "HOLD"),
                "confidence": primary_data.get("confidence", 0),
                "reason":     primary_data.get("reason", ""),
                "indicators": primary_data.get("indicators", {}),
                "h1_trend":   primary_data.get("h1_trend", ""),
                "h4_trend":   primary_data.get("h4_trend", ""),
                "ask":        primary_data.get("ask"),
                "bid":        primary_data.get("bid"),
                "symbols":    symbols_status,
            })

            _save_state(self.state)

            # ── Self-improvement check (daily) ───────────────────────────
            if self.improver and cycle % 100 == 0:  # check every ~50 min
                try:
                    if self.improver.should_run_review():
                        self.improver.daily_review()
                        if self.skill_mgr:
                            self.skill_mgr.invalidate_cache()
                except Exception as e:
                    log.warning(f"Self-improvement error: {e}")

            secs = CONFIG["monitor_interval_s"]
            log.info(f"   💤 Next check in {secs}s  "
                     f"(analysis in {next_analysis_in}s)  "
                     f"Cycle #{cycle}")
            time.sleep(secs)

        # ── Shutdown ──────────────────────────────────────────────────────────
        log.info("\n🛑 Trader stopped. Disconnecting...")
        try:
            self.bridge.disconnect()
        except Exception:
            pass
        _save_state(self.state)
        log.info(f"📊 Session stats: {self.state['total_trades']} trades | "
                 f"W:{self.state['wins']} L:{self.state['losses']}")


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
        d  = "BUY" if getattr(p, "type", 1) == 0 else "SELL"
        pr = getattr(p, "profit", 0)
        ok = bridge.close_position(p.ticket)
        log.info(f"  #{p.ticket} {p.symbol} {d} profit={pr:+.2f} → {'closed' if ok else 'FAILED'}")
    acct = bridge.get_account_info()
    if acct:
        log.info(f"Final balance: ${acct.balance:.2f}")
    bridge.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous Trader — XAUUSD + XAGUSD")
    parser.add_argument("--dry",   action="store_true", help="Paper-trade (no real orders)")
    parser.add_argument("--close", action="store_true", help="Close all positions and exit")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)   # always run from project dir

    if args.close:
        cmd_close_all()
    else:
        trader = ContinuousTrader(dry_run=args.dry)
        trader.run()
