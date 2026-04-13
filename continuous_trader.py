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
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)
log = logging.getLogger("trader")

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    {"broker": "GOLD.i#",   "display": "XAUUSD", "pip": 0.10,
     "sl_pips": 150, "tp_pips": 300, "lot": 0.01},
    {"broker": "SILVER.i#", "display": "XAGUSD", "pip": 0.01,
     "sl_pips": 50,  "tp_pips": 100, "lot": 0.01},
]

CONFIG = {
    "monitor_interval_s": 30,       # check positions every 30s
    "analysis_interval_s": 120,     # full AI analysis every 2 min
    "profit_close_pct":   2.0,      # close trade at +2% account profit
    "loss_close_pct":     1.0,      # close trade at -1% account loss
    "min_confidence":     0.55,     # minimum AI confidence to trade
    "max_trades_per_sym": 1,        # max 1 position per symbol
    "dry_run":            False,    # set True for paper trading
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

def check_and_close_positions(bridge, account_balance: float, positions_by_sym: dict,
                               state: dict, dry_run: bool) -> list:
    """
    Close positions that hit profit or loss targets.
    Returns list of closed broker symbols.
    """
    closed = []
    profit_target = account_balance * (CONFIG["profit_close_pct"] / 100)
    loss_limit    = account_balance * (CONFIG["loss_close_pct"] / 100)

    for sym_cfg in SYMBOLS:
        broker_sym = sym_cfg["broker"]
        positions  = positions_by_sym.get(broker_sym, [])
        for pos in positions:
            profit = getattr(pos, "profit", 0)
            ticket = getattr(pos, "ticket", "?")
            direction = "BUY" if getattr(pos, "type", 1) == 0 else "SELL"

            should_close = False
            reason = ""
            if profit >= profit_target:
                should_close = True
                reason = f"profit target +{CONFIG['profit_close_pct']}%"
            elif profit <= -loss_limit:
                should_close = True
                reason = f"loss limit -{CONFIG['loss_close_pct']}%"

            if should_close:
                log.info(f"🎯 Closing #{ticket} {sym_cfg['display']} {direction} "
                         f"{fmt_profit(profit)} — {reason}")
                if not dry_run:
                    ok = bridge.close_position(ticket)
                    if ok:
                        state["total_trades"] += 1
                        if profit > 0:
                            state["wins"] += 1
                        else:
                            state["losses"] += 1
                        closed.append(broker_sym)
                else:
                    log.info(f"   [DRY RUN] Would close #{ticket}")
                    closed.append(broker_sym)

    return closed

def build_order_params(sym_cfg: dict, tick, direction: str) -> dict:
    pip = sym_cfg["pip"]
    price = tick.ask if direction == "BUY" else tick.bid
    digits = 2 if pip >= 0.01 else 5

    if direction == "BUY":
        sl = round(price - sym_cfg["sl_pips"] * pip, digits)
        tp = round(price + sym_cfg["tp_pips"] * pip, digits)
    else:
        sl = round(price + sym_cfg["sl_pips"] * pip, digits)
        tp = round(price - sym_cfg["tp_pips"] * pip, digits)

    return {
        "symbol":    sym_cfg["broker"],
        "direction": direction,
        "lot":       sym_cfg["lot"],
        "price":     price,
        "sl":        sl,
        "tp":        tp,
        "comment":   f"CT-{direction}",
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

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT,  self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, sig, frame):
        log.info("\n🛑 Shutdown signal received — closing gracefully...")
        self._stop = True

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
        """Run multi-timeframe analysis with Ollama AI."""
        try:
            from analyzer import MarketAnalyzer
            analyzer = MarketAnalyzer(use_claude=CONFIG["use_ai"])
            signal_data = analyzer.analyze(tf_data, tick, sym_cfg["display"])
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

                    # Check max trades per symbol BEFORE analyzing
                    current_pos = positions_by_sym.get(broker_sym, [])
                    if len(current_pos) >= CONFIG["max_trades_per_sym"]:
                        log.info(f"   ⏸  {disp}: max trades reached ({len(current_pos)})")
                        # Still update status display
                        if disp in symbols_status:
                            symbols_status[disp]["positions"] = len(current_pos)
                        continue

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

                    # Place trade if signal qualifies
                    if (direction in ("BUY", "SELL") and
                            confidence >= CONFIG["min_confidence"]):
                        order = build_order_params(sym_cfg, tick, direction)
                        if self.dry_run:
                            log.info(f"   📝 DRY RUN {disp} {direction} "
                                     f"lot={order['lot']} sl={order['sl']} tp={order['tp']}")
                        else:
                            result = self.bridge.place_order(order)
                            if result and hasattr(result, "order"):
                                log.info(f"   💰 ORDER #{result.order} — "
                                         f"{disp} {direction} @{order['price']:.2f} "
                                         f"SL={order['sl']} TP={order['tp']}")
                                self.state["total_trades"] += 1
                                _save_state(self.state)
                                # Refresh positions
                                time.sleep(0.5)
                                positions_by_sym = get_positions_by_symbol(self.bridge)
                            else:
                                log.warning(f"   ❌ {disp} order failed")
                    else:
                        if direction == "HOLD":
                            log.info(f"   ⏭  {disp}: HOLD — no trade")
                        else:
                            log.info(f"   ⏭  {disp}: confidence too low ({confidence:.0%})")

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
