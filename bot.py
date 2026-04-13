"""
MT5 AI Trading Bot — powered by Claude AI
Run from Claude Code terminal: python bot.py [command]

Commands:
  python bot.py run           — start live trading loop
  python bot.py analyze       — one-shot market analysis
  python bot.py status        — show account + open positions
  python bot.py history       — show recent trade log
  python bot.py backtest      — run simple indicator backtest
"""

import sys
import os
import time
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, timezone

# Load .env file if present
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

STATUS_FILE = Path(__file__).parent / "bot_status.json"

def _sanitize(obj):
    """Recursively make a value JSON-safe: NaN→None, numpy types→Python builtins."""
    import math
    try:
        import numpy as np
        _np_float  = np.floating
        _np_int    = np.integer
        _np_bool   = np.bool_
        _np_array  = np.ndarray
    except ImportError:
        _np_float = _np_int = _np_bool = _np_array = type(None)

    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, _np_array):
        return [_sanitize(x) for x in obj.tolist()]
    if isinstance(obj, _np_bool):
        return bool(obj)
    if isinstance(obj, _np_int):
        return int(obj)
    if isinstance(obj, _np_float):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    return obj

def _write_status(data: dict):
    try:
        STATUS_FILE.write_text(json.dumps(_sanitize(data), indent=2))
    except Exception as e:
        print(f"  ⚠️  Status write error: {e}")

from analyzer import MarketAnalyzer
from risk_manager import RiskManager
from logger import TradeLogger

# ── Auto-select bridge: MetaApi (real demo) or Mock (simulation) ──────────────
def _make_bridge():
    token      = os.environ.get("METAAPI_TOKEN", "")
    account_id = os.environ.get("METAAPI_ACCOUNT_ID", "")
    if token and account_id:
        print("  Using MetaApi bridge (real MT5 demo account)")
        from metaapi_bridge import MetaApiBridge
        return MetaApiBridge(token, account_id)
    else:
        print("  Using mock bridge (simulation mode — set METAAPI_TOKEN to use real MT5)")
        from mt5_bridge import MT5Bridge
        return MT5Bridge()

# ─────────────────────────── CONFIG ──────────────────────────────────────────
CANDLES_FILE = Path(__file__).parent / "candles_cache.json"

CONFIG = {
    # ── Symbols ──────────────────────────────────────────────────────────────
    # XMGlobal broker symbol names (confirmed via MetaApi API)
    # Gold = GOLD.i#  (~$4714/oz)   Silver = SILVER.i# (~$74/oz)
    "symbols": ["GOLD.i#", "SILVER.i#"],   # XAUUSD + XAGUSD only
    "symbol":  "GOLD.i#",    # primary symbol

    # ── Human-readable display names for dashboard ────────────────────────────
    "symbol_display": {
        "GOLD.i#":   "XAUUSD",   # Gold — broker uses GOLD.i#
        "SILVER.i#": "XAGUSD",   # Silver
    },

    # ── Per-symbol overrides (SL/TP in "pip units" for that instrument) ──────
    # GOLD.i# quoted to 2 decimals; pip = $0.10; 200 pips = $20/lot move
    # For 0.01 lot (1 oz): 200 pips × $0.10 × 0.01 lot = $0.20 SL (too small)
    # Use 2000 pips for meaningful ~$2 SL risk on 0.01 lot
    # ── Symbol-specific SL/TP & pip info ─────────────────────────────────────
    # GOLD.i#   price ~$4700, pip=$0.10, contract=100oz/lot
    #   → 100 pips SL = $0.10*100pips*0.01lot*100oz = $10 risk ✓
    # SILVER.i# price ~$74,   pip=$0.01, contract=5000oz/lot
    #   → 50 pips SL = $0.01*50pips*0.01lot*5000oz = $2.50 risk ✓
    "symbol_configs": {
        "GOLD.i#":   {"sl_pips": 100, "tp_pips": 200, "lot_size": 0.01},   # Gold ~$4700
        "SILVER.i#": {"sl_pips": 50,  "tp_pips": 100, "lot_size": 0.01},   # Silver ~$74
    },

    # ── Timeframes ────────────────────────────────────────────────────────────
    "timeframes":       ["M15","H1","H4"],
    "timeframe":        "M15",

    # ── Risk ─────────────────────────────────────────────────────────────────
    "lot_size":         0.01,
    "max_risk_pct":     1.0,
    "sl_pips":          30,
    "tp_pips":          60,
    "max_open_trades":  1,              # max positions per symbol

    # ── Loop ─────────────────────────────────────────────────────────────────
    "loop_interval_s":  60,
    "use_claude_ai":    True,
    "dry_run":          False,   # LIVE trading — real orders on demo account
    "min_confidence":   0.55,   # slightly relaxed to catch good setups
    "confidence_floor":  0.45,   # hard skip below this regardless of AI output

    # ── Guards ───────────────────────────────────────────────────────────────
    "skip_asian_session":        False,
    "max_daily_drawdown_pct":    2.0,
    "session_size_multipliers": {
        "LONDON_NY_OVERLAP": 1.0,
        "LONDON":            0.8,
        "NEW_YORK":          0.8,
        "ASIAN":             0.5,
        "MARKET_CLOSED":     0.0,
    },
}
# ─────────────────────────────────────────────────────────────────────────────


def is_forex_market_open() -> tuple:
    """
    Returns (is_open: bool, session: str).
    Forex is closed Friday 22:00 UTC through Sunday 22:00 UTC.
    """
    now = datetime.now(timezone.utc)
    weekday, hour = now.weekday(), now.hour   # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if weekday == 5:                          return False, "MARKET_CLOSED"  # Saturday
    if weekday == 4 and hour >= 22:           return False, "MARKET_CLOSED"  # Friday night
    if weekday == 6 and hour < 22:            return False, "MARKET_CLOSED"  # Sunday
    if 7  <= hour < 13: return True, "LONDON"
    if 13 <= hour < 16: return True, "LONDON_NY_OVERLAP"
    if 16 <= hour < 22: return True, "NEW_YORK"
    return True, "ASIAN"


def run_trading_loop(config: dict):
    """Main live trading loop — multi-symbol (Forex + Commodities)."""
    symbols = config.get("symbols", [config.get("symbol", "EURUSD")])

    print("\n" + "═" * 60)
    print("  MT5 AI TRADING BOT  —  Ollama-powered")
    print("═" * 60)
    print(f"  Symbols:    {' | '.join(symbols)}")
    print(f"  Timeframe:  {config['timeframe']}")
    print(f"  Dry run:    {config['dry_run']}")
    print(f"  AI:         {'ON' if config['use_claude_ai'] else 'OFF (indicators only)'}")
    print("═" * 60 + "\n")

    bridge   = _make_bridge()
    analyzer = MarketAnalyzer(use_claude=config["use_claude_ai"])
    risk_mgr = RiskManager(config)   # used for drawdown tracking (persistent state)
    logger   = TradeLogger()

    if not bridge.connect():
        print("❌  Could not connect. Check credentials or use mock mode.")
        sys.exit(1)

    print("✅  Connected\n")
    bridge.print_account_info()

    cycle = 0
    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n── Cycle {cycle}  [{ts}] ──────────────────────────────")

            _write_status({"state": "running", "cycle": cycle, "ts": ts,
                           "dry_run": config["dry_run"], "symbol": symbols[0],
                           "signal": "...", "reason": "Analyzing..."})

            # ── Market hours gate (global — same for all symbols) ────────────
            market_open, session_name = is_forex_market_open()
            if not market_open:
                print(f"  Market CLOSED. Sleeping 5 min.")
                _write_status({"state": "sleeping", "cycle": cycle, "ts": ts,
                               "symbol": symbols[0], "session": session_name,
                               "reason": "Market closed - weekend"})
                logger.log({"direction": "HOLD", "confidence": 0.0,
                            "reason": "Market closed - weekend"},
                           action="MARKET_CLOSED", symbol=symbols[0])
                time.sleep(300)
                continue

            if config.get("skip_asian_session") and session_name == "ASIAN":
                print(f"  Asian session — skipping. Sleeping 5 min.")
                logger.log({"direction": "HOLD", "confidence": 0.0,
                            "reason": "Asian session skipped"},
                           action="SKIP_SESSION", symbol=symbols[0])
                time.sleep(300)
                continue

            # ── Daily drawdown guard (once per cycle, shared across symbols) ─
            if risk_mgr.check_daily_drawdown(bridge):
                logger.log({"direction": "HOLD", "confidence": 0.0,
                            "reason": "Daily drawdown limit reached"},
                           action="DRAWDOWN_HALT", symbol=symbols[0])
                time.sleep(300)
                continue

            # ── Fetch account info for dashboard ────────────────────────────
            account_info = {}
            try:
                ai = bridge.get_account_info()
                if ai:
                    account_info = {
                        "balance":     getattr(ai, "balance", 0),
                        "equity":      getattr(ai, "equity", 0),
                        "margin":      getattr(ai, "margin", 0),
                        "margin_free": getattr(ai, "margin_free", 0),
                        "currency":    getattr(ai, "currency", "USD"),
                    }
            except Exception:
                pass

            # ── Fetch all open positions once per cycle (avoid repeated RPC calls)
            try:
                all_positions = bridge.get_open_positions()   # all symbols
            except Exception:
                all_positions = []

            # ── Per-symbol loop ──────────────────────────────────────────────
            symbols_status  = {}
            candles_cache   = {}

            for symbol in symbols:
                print(f"\n  [{symbol}]")

                # Per-symbol config (merge overrides)
                sym_overrides = config.get("symbol_configs", {}).get(symbol, {})
                sym_config    = {**config, **sym_overrides, "symbol": symbol}
                sym_risk      = RiskManager(sym_config)

                # Trailing SL / breakeven management (uses cached positions)
                try:
                    sym_positions = [p for p in all_positions
                                     if getattr(p, "symbol", "") == symbol]
                    modified = sym_risk.manage_open_positions(bridge, symbol)
                    if modified:
                        print(f"  SL → breakeven for {symbol}: {modified}")
                except Exception:
                    sym_positions = []

                # Fetch multi-timeframe candles
                tf_data = {}
                for tf in config.get("timeframes", [config["timeframe"]]):
                    count = 500 if tf == "M15" else 200
                    df = bridge.get_candles(symbol, tf, count)
                    if df is not None:
                        tf_data[tf] = df

                if not tf_data:
                    print(f"  ⚠️  No candles for {symbol}, skipping.")
                    continue

                primary = tf_data.get(config["timeframe"], list(tf_data.values())[0])

                tick = bridge.get_tick(symbol)

                # Analyze
                signal = analyzer.analyze(
                    tf_data if len(tf_data) > 1 else primary, tick, symbol)
                signal["session"] = session_name   # ensure session from gate
                print(f"  Signal: {signal['direction']:6s} | Conf: {signal['confidence']:.0%} | {signal['reason'][:60]}")

                # Accumulate per-symbol status for dashboard
                # Use display name for dashboard readability
                display_sym = config.get("symbol_display", {}).get(symbol, symbol)
                symbols_status[display_sym] = {
                    "signal":        signal["direction"],
                    "confidence":    round(signal["confidence"], 4),
                    "reason":        signal["reason"],
                    "session":       session_name,
                    "ask":           round(tick.ask, 5) if tick else None,
                    "bid":           round(tick.bid, 5) if tick else None,
                    "indicators":    signal.get("indicators", {}),
                    "h1_trend":      signal.get("h1_trend", ""),
                    "h4_trend":      signal.get("h4_trend", ""),
                    "broker_symbol": symbol,   # actual broker name for orders
                }
                # Also cache under display name for sparklines
                try:
                    closes = primary["c"].tail(100).tolist()
                    candles_cache[display_sym] = {"closes": closes, "updated": ts}
                except Exception:
                    pass

                # Update dashboard with partial results as each symbol completes
                _write_status({
                    "state":      "running",
                    "cycle":      cycle,
                    "ts":         ts,
                    "dry_run":    config["dry_run"],
                    "session":    session_name,
                    "account":    account_info,
                    "symbol":     display_sym,
                    "signal":     signal["direction"],
                    "confidence": round(signal["confidence"], 4),
                    "reason":     signal["reason"],
                    "indicators": signal.get("indicators", {}),
                    "h1_trend":   signal.get("h1_trend", ""),
                    "h4_trend":   signal.get("h4_trend", ""),
                    "ask":        round(tick.ask, 5) if tick else None,
                    "bid":        round(tick.bid, 5) if tick else None,
                    "symbols":    symbols_status,
                })

                # Risk check (per symbol) — use pre-fetched positions cache
                open_trades = [p for p in all_positions
                               if getattr(p, "symbol", "") == symbol]
                if len(open_trades) >= config["max_open_trades"]:
                    print(f"  ⏸  Max trades for {symbol} ({config['max_open_trades']}), skip.")
                    logger.log(signal, action="SKIP_MAX_TRADES", symbol=symbol)
                    continue

                # Execute / paper trade
                if (signal["direction"] in ("BUY", "SELL") and
                        signal["confidence"] >= config.get("min_confidence", 0.60) and
                        signal["confidence"] >= config.get("confidence_floor", 0.50)):
                    order_params = sym_risk.build_order(signal, tick, primary)
                    if config["dry_run"]:
                        print(f"  📝 DRY RUN — {symbol} {signal['direction']} lot={order_params['lot']}")
                        logger.log(signal, action="DRY_RUN", order=order_params, symbol=symbol)
                    else:
                        result = bridge.place_order(order_params)
                        if result:
                            print(f"  ✅  {symbol} order #{result.order}")
                            logger.log(signal, action="TRADE", order=order_params,
                                       ticket=result.order, symbol=symbol)
                        else:
                            print(f"  ❌  {symbol} order failed.")
                            logger.log(signal, action="ORDER_FAILED", symbol=symbol)
                else:
                    logger.log(signal, action="HOLD", symbol=symbol)

            # ── Save candles cache for dashboard sparklines ──────────────────
            try:
                CANDLES_FILE.write_text(json.dumps(candles_cache))
            except Exception:
                pass

            # ── Write consolidated status for dashboard ──────────────────────
            # Use display name for primary symbol
            primary_sym_raw = symbols[0]
            primary_sym     = config.get("symbol_display", {}).get(primary_sym_raw, primary_sym_raw)
            primary_data    = symbols_status.get(primary_sym, {})
            _write_status({
                "state":      "running",
                "cycle":      cycle,
                "ts":         ts,
                "dry_run":    config["dry_run"],
                "session":    session_name,
                "account":    account_info,
                # backward-compat flat fields (primary symbol)
                "symbol":     primary_sym,
                "signal":     primary_data.get("signal", "HOLD"),
                "confidence": primary_data.get("confidence", 0),
                "reason":     primary_data.get("reason", ""),
                "indicators": primary_data.get("indicators", {}),
                "h1_trend":   primary_data.get("h1_trend", ""),
                "h4_trend":   primary_data.get("h4_trend", ""),
                "ask":        primary_data.get("ask"),
                "bid":        primary_data.get("bid"),
                # full per-symbol map (new dashboard)
                "symbols":    symbols_status,
            })

            time.sleep(config["loop_interval_s"])

    except KeyboardInterrupt:
        print("\n\n  🛑  Bot stopped by user.")
    finally:
        bridge.disconnect()
        print("  Disconnected from MT5.\n")


def cmd_analyze(config: dict):
    """One-shot analysis without trading."""
    bridge   = MT5Bridge()
    analyzer = MarketAnalyzer(use_claude=config["use_claude_ai"])

    if not bridge.connect():
        print("❌  Could not connect to MT5.")
        sys.exit(1)

    candles = bridge.get_candles(config["symbol"], config["timeframe"], 200)
    tick    = bridge.get_tick(config["symbol"])
    signal  = analyzer.analyze(candles, tick, config["symbol"])

    print("\n── Market Analysis ──────────────────────────────────")
    print(f"  Symbol:     {config['symbol']}  ({config['timeframe']})")
    print(f"  Ask/Bid:    {tick.ask:.5f} / {tick.bid:.5f}")
    print(f"  Signal:     {signal['direction']}")
    print(f"  Confidence: {signal['confidence']:.0%}")
    print(f"  RSI:        {signal['indicators']['rsi']:.1f}")
    print(f"  EMA20 > 50: {signal['indicators']['ema_trend']}")
    print(f"  MACD cross: {signal['indicators']['macd_signal']}")
    print(f"\n  AI Reasoning:\n  {signal['reason']}")
    print("─" * 54 + "\n")

    bridge.disconnect()


def cmd_status(config: dict):
    """Print account status and open positions."""
    bridge = MT5Bridge()
    if not bridge.connect():
        print("❌  Could not connect to MT5.")
        sys.exit(1)
    bridge.print_account_info()
    bridge.print_open_positions()
    bridge.disconnect()


def cmd_history(limit: int = 20, filter_action: str = None, filter_symbol: str = None):
    """Print trade log from SQLite."""
    logger = TradeLogger()
    logger.print_history(limit=limit, filter_action=filter_action,
                         filter_symbol=filter_symbol)


def cmd_logs(follow: bool = False, lines: int = 50):
    """Tail bot.log — optionally follow in real time like tail -f."""
    log_path = Path(__file__).parent / "bot.log"
    if not log_path.exists():
        print("  No bot.log yet. Start the bot first.")
        return
    with open(log_path) as f:
        all_lines = f.readlines()
        print("".join(all_lines[-lines:]), end="")
        if follow:
            print("\n--- Following bot.log (Ctrl+C to stop) ---")
            try:
                while True:
                    new = f.readline()
                    if new:
                        print(new, end="", flush=True)
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                pass


def cmd_backtest(config: dict):
    """Simple indicator-based backtest on last 500 candles."""
    from backtester import run_backtest
    bridge = MT5Bridge()
    if not bridge.connect():
        print("❌  Could not connect to MT5.")
        sys.exit(1)
    candles = bridge.get_candles(config["symbol"], config["timeframe"], 500)
    bridge.disconnect()
    if candles is not None:
        run_backtest(candles, config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 AI Trading Bot")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "analyze", "status", "history", "backtest", "logs"],
                        help="Command to execute (default: run)")
    parser.add_argument("--symbol",    default=CONFIG["symbol"])
    parser.add_argument("--timeframe", default=CONFIG["timeframe"])
    parser.add_argument("--live",      action="store_true",
                        help="Enable live trading (disables dry_run)")
    parser.add_argument("--no-ai",     action="store_true",
                        help="Disable Claude AI, use indicators only")
    parser.add_argument("--filter",    default=None, metavar="ACTION",
                        help="Filter history by action (e.g. TRADE, DRY_RUN, HOLD)")
    parser.add_argument("--limit",     type=int, default=20,
                        help="Number of entries to show (default: 20)")
    parser.add_argument("--follow",    action="store_true",
                        help="Follow bot.log in real time (use with logs command)")
    args = parser.parse_args()

    CONFIG["symbol"]      = args.symbol
    CONFIG["timeframe"]   = args.timeframe
    if args.live:
        CONFIG["dry_run"] = False
    if args.no_ai:
        CONFIG["use_claude_ai"] = False

    cmds = {
        "run":       lambda: run_trading_loop(CONFIG),
        "analyze":   lambda: cmd_analyze(CONFIG),
        "status":    lambda: cmd_status(CONFIG),
        "history":   lambda: cmd_history(limit=args.limit, filter_action=args.filter),
        "backtest":  lambda: cmd_backtest(CONFIG),
        "logs":      lambda: cmd_logs(follow=args.follow, lines=args.limit),
    }
    cmds[args.command]()
