#!/usr/bin/env python3
"""
trace_viewer.py — View the 4-stage trading decision log.

COMMANDS
  python3 trace_viewer.py                       last 20 decisions
  python3 trace_viewer.py --last 50             last 50 decisions
  python3 trace_viewer.py --symbol XAUUSD       Gold only
  python3 trace_viewer.py --symbol XAGUSD       Silver only
  python3 trace_viewer.py --action BUY          only BUY signals
  python3 trace_viewer.py --action SELL         only SELL signals
  python3 trace_viewer.py --action HOLD         only HOLD signals
  python3 trace_viewer.py --session LONDON      London session only
  python3 trace_viewer.py --today               today's records only
  python3 trace_viewer.py --stats               win/signal summary stats
  python3 trace_viewer.py --watch               live tail (refreshes every 10s)
  python3 trace_viewer.py --json                raw JSON lines
"""

import json
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from core.paths import STATE_DIR


TRACE_FILE = STATE_DIR / "analysis_trace.jsonl"

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def col(text, code): return f"{code}{text}{RESET}"


def load_records(args) -> list:
    if not TRACE_FILE.exists():
        return []
    lines = TRACE_FILE.read_text().splitlines()
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            pass

    # Filters
    if args.symbol:
        records = [r for r in records if r.get("symbol") == args.symbol.upper()]
    if args.action:
        records = [r for r in records if r.get("decision", {}).get("direction") == args.action.upper()]
    if args.session:
        records = [r for r in records if r.get("session", "").upper() == args.session.upper()]
    if args.today:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        records = [r for r in records if r.get("ts", "").startswith(today)]

    return records[-args.last:]


def fmt_direction(d):
    if d == "BUY":   return col(f"{'BUY':4s}", GREEN)
    if d == "SELL":  return col(f"{'SELL':4s}", RED)
    return col(f"{'HOLD':4s}", YELLOW)


def fmt_confidence(c):
    pct = int(c * 100)
    if pct >= 75: return col(f"{pct:3d}%", GREEN)
    if pct >= 60: return col(f"{pct:3d}%", YELLOW)
    return col(f"{pct:3d}%", DIM)


def fmt_score(s):
    if s is None: return "  ?"
    s = float(s)
    if s >= 18:  return col(f"{s:+6.1f}", GREEN)
    if s <= -18: return col(f"{s:+6.1f}", RED)
    return col(f"{s:+6.1f}", YELLOW)


def print_record(r):
    ts   = r.get("ts", "")[-8:]          # HH:MM:SS
    sym  = r.get("symbol", "?")
    sess = r.get("session", "")[:6]
    cyc  = r.get("cycle", "?")
    dec  = r.get("decision", {})
    calc = r.get("calculations", {})
    raw  = r.get("raw_data", {})
    ai_c = r.get("ai_context", "")

    direction  = dec.get("direction", "HOLD")
    confidence = float(dec.get("confidence", 0))
    reason     = dec.get("reason", "")[:90]
    score      = calc.get("score", 0)
    regime     = calc.get("regime", "")[:8]

    m15  = calc.get("m15", {})
    adx  = m15.get("adx", "?")
    rsi  = m15.get("rsi", "?")
    bb   = (m15.get("bb", "") or "").replace("_", " ").replace("ABOVE ", "↑").replace("BELOW ", "↓")
    macd = (m15.get("macd", "") or "").replace("_CROSS", "✕").replace("BULLISH", "BULL").replace("BEARISH", "BEAR")
    sc   = (m15.get("stoch_cross") or "NONE").replace("BULLISH", "↑").replace("BEARISH", "↓")
    wr   = m15.get("williams_r", "?")

    fs   = calc.get("factor_scores", {})
    h4   = calc.get("h4_trend", "")[:8]
    h1   = calc.get("h1_trend", "")[:8]
    d1   = calc.get("d1_trend", "")[:8]
    fib  = calc.get("fib_zone", "")[:55] or "—"

    # TV raw M15
    tv_m15 = raw.get("M15", {})
    tv_tag = ""
    if tv_m15.get("adx"):
        tv_tag = f"[TV ADX={tv_m15['adx']} RSI={tv_m15.get('rsi','?')} EMA={tv_m15.get('ema_trend','?')}]"

    # Header line
    print(f"{col(ts, DIM)}  {col(sym, BOLD)}  {col(sess, CYAN)}  "
          f"{fmt_direction(direction)} {fmt_confidence(confidence)}  "
          f"Score{fmt_score(score)}  {col(regime, DIM)}  Cyc#{cyc}")

    # Stage 1: Raw TV data
    if tv_tag:
        print(f"  {col('📡 RAW', CYAN)}  {tv_tag}")

    # Stage 2: Calculations
    fline = "  ".join(f"{k.replace('f','F').replace('_',' ')[:6]}={v:+.0f}"
                      for k, v in sorted(fs.items())
                      if isinstance(v, (int, float)) and v != 0)
    print(f"  {col('🔧 CALC', YELLOW)}  ADX={adx} RSI={rsi} BB={bb} MACD={macd} Stoch={sc} W%R={wr}")
    print(f"         H4={h4} H1={h1} D1={d1}")
    if fline:
        print(f"         {fline[:110]}")
    if fib and fib != "—":
        print(f"         Fib: {fib}")

    # Stage 3: AI context
    if ai_c:
        print(f"  {col('🧠 AI  ', BOLD)}  {ai_c[:110]}")

    # Stage 4: Decision
    print(f"  {col('📝 CALL', GREEN if direction=='BUY' else RED if direction=='SELL' else YELLOW)}  "
          f"{fmt_direction(direction)} {fmt_confidence(confidence)}  {reason}")
    print()


def print_stats(records):
    if not records:
        print("No records found.")
        return

    from collections import Counter
    directions = Counter(r.get("decision", {}).get("direction") for r in records)
    sessions   = Counter(r.get("session") for r in records)
    symbols    = Counter(r.get("symbol") for r in records)

    conf_vals = [float(r["decision"]["confidence"]) for r in records if "decision" in r]
    avg_conf  = sum(conf_vals) / len(conf_vals) if conf_vals else 0

    scores = [float(r["calculations"]["score"]) for r in records
              if r.get("calculations", {}).get("score") is not None]
    avg_score = sum(scores) / len(scores) if scores else 0

    print(col(f"\n{'═'*55}", BOLD))
    print(col(f"  ANALYSIS TRACE STATS  ({len(records)} records)", BOLD))
    print(col(f"{'═'*55}", BOLD))
    print(f"  Symbols   : {dict(symbols)}")
    print(f"  Sessions  : {dict(sessions)}")
    print(f"  Signals   : {col('BUY='+str(directions.get('BUY',0)), GREEN)}  "
          f"{col('SELL='+str(directions.get('SELL',0)), RED)}  "
          f"{col('HOLD='+str(directions.get('HOLD',0)), YELLOW)}")
    print(f"  Avg conf  : {avg_conf:.1%}")
    print(f"  Avg score : {avg_score:+.1f}")
    if records:
        print(f"  Time span : {records[0].get('ts','')} → {records[-1].get('ts','')}")
    print()


def watch_mode(args):
    print(col("👁  LIVE WATCH — refreshing every 10s  (Ctrl+C to stop)\n", CYAN))
    seen = set()
    while True:
        try:
            records = load_records(args)
            for r in records:
                key = (r.get("ts"), r.get("symbol"))
                if key not in seen:
                    seen.add(key)
                    print_record(r)
            time.sleep(10)
        except KeyboardInterrupt:
            break


def main():
    parser = argparse.ArgumentParser(description="Trading decision trace viewer")
    parser.add_argument("--last",    type=int, default=20,  help="Show last N records (default 20)")
    parser.add_argument("--symbol",  type=str, default="",  help="Filter by symbol (XAUUSD / XAGUSD)")
    parser.add_argument("--action",  type=str, default="",  help="Filter by direction (BUY/SELL/HOLD)")
    parser.add_argument("--session", type=str, default="",  help="Filter by session (LONDON/NEW_YORK/ASIAN)")
    parser.add_argument("--today",   action="store_true",   help="Only today's records")
    parser.add_argument("--stats",   action="store_true",   help="Show summary stats")
    parser.add_argument("--watch",   action="store_true",   help="Live tail mode")
    parser.add_argument("--json",    action="store_true",   help="Raw JSON output")
    args = parser.parse_args()

    if not TRACE_FILE.exists():
        print(col("⚠  analysis_trace.jsonl not found yet.", YELLOW))
        print("   The bot writes to it on every analysis cycle (~60s interval).")
        print(f"   Expected location: {TRACE_FILE}")
        return

    if args.watch:
        watch_mode(args)
        return

    records = load_records(args)

    if not records:
        print("No matching records found.")
        return

    if args.json:
        for r in records:
            print(json.dumps(r))
        return

    if args.stats:
        print_stats(records)
        return

    print(col(f"\n{'═'*70}", BOLD))
    print(col(f"  TRADING DECISION TRACE   {len(records)} records", BOLD))
    print(col(f"{'═'*70}\n", BOLD))

    for r in records:
        print_record(r)

    print_stats(records)


if __name__ == "__main__":
    main()
