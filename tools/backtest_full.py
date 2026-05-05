"""
Full backtest — replays the live bot's deterministic signal stack on real candles.

  - Multi-TF context: M15 (entry) + resampled H1 + H4 from same series
  - Same scoring weights, thresholds, and confluence math as analyzer._multi_tf_signal
  - Per-symbol pip / SL / TP from continuous_trader.SYMBOLS
  - Exit model:
      • Hard SL / TP (live config)
      • Breakeven stop after +15 pips
      • Time-decay close after `max_trade_age_hours` (4h)
      • Optional trailing stop after +30 pips (10 pip trail)
  - No AI / no Fibonacci F12 (the live system also gates on the indicator score
    BEFORE AI overrides — this measures the ground-truth edge of the indicators).
  - Spread modelled as 2 pips per round-trip.

Outputs P&L in pips and USD (using contract size & pip value), win rate, R:R,
max drawdown, factor contribution table, and an equity curve.

Usage:
    venv/bin/python -m tools.backtest_full --csv data/history_XAUUSD_15m.csv --symbol XAUUSD
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analyzer import MarketAnalyzer

# ── Live bot symbol config (mirror of continuous_trader.SYMBOLS) ─────────────
SYMBOL_CFG = {
    "XAUUSD": {"pip": 0.10, "contract_size": 100,  "sl_pips": 30, "tp_pips": 50, "lot": 0.01},
    "XAGUSD": {"pip": 0.01, "contract_size": 5000, "sl_pips": 15, "tp_pips": 25, "lot": 0.01},
}

# Live confidence gate (continuous_trader.CONFIG.min_confidence)
MIN_CONFIDENCE = 0.55
SPREAD_PIPS    = 2.0

EXIT_CFG = {
    "breakeven_trigger_pips": 15,
    "breakeven_buffer_pips":  1.0,
    "trailing_start_pips":    30,
    "trailing_distance_pips": 10,
    "max_trade_age_hours":    4,
}


def resample(m15: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = m15.set_index("time")
    out = pd.DataFrame({
        "o": df["o"].resample(rule).first(),
        "h": df["h"].resample(rule).max(),
        "l": df["l"].resample(rule).min(),
        "c": df["c"].resample(rule).last(),
        "vol": df["vol"].resample(rule).sum(),
    }).dropna().reset_index()
    return out


def slice_up_to(df: pd.DataFrame, t: pd.Timestamp, max_bars: int,
                bar_seconds: int = 0) -> pd.DataFrame:
    """Return only FULLY-CLOSED bars (open time + bar duration ≤ t).

    For H1 bars, a bar opened at 15:00 closes at 16:00. Only include
    that bar in indicator calculations when t ≥ 16:00 — otherwise we
    use future close data within the current hour (look-ahead bias).
    """
    if bar_seconds:
        cutoff = t - pd.Timedelta(seconds=bar_seconds)
        sub = df[df["time"] <= cutoff]
    else:
        sub = df[df["time"] <= t]
    if len(sub) > max_bars:
        sub = sub.iloc[-max_bars:]
    return sub


def run_backtest(csv: str, symbol: str, start_idx: int = 300,
                 use_multi_tf: bool = True, verbose: bool = True) -> dict:
    cfg = SYMBOL_CFG[symbol]
    pip = cfg["pip"]
    sl_pips, tp_pips = cfg["sl_pips"], cfg["tp_pips"]
    pip_value = pip * cfg["contract_size"] * cfg["lot"]   # USD per pip on 0.01 lot

    candles = pd.read_csv(csv, parse_dates=["time"])
    candles["time"] = pd.to_datetime(candles["time"], utc=True)
    candles = candles.sort_values("time").reset_index(drop=True)

    # Pre-resample H1 / H4 once — much faster than recomputing on every bar
    h1 = resample(candles, "1h")  if use_multi_tf else None
    h4 = resample(candles, "4h")  if use_multi_tf else None

    analyzer = MarketAnalyzer(use_ai=False)

    trades   = []
    in_trade = None
    factor_running = {f"f{i}": 0.0 for i in range(1, 12)}  # cumulative pips per factor sign
    factor_count   = {f"f{i}": 0   for i in range(1, 12)}
    bar_minutes = 15
    max_age_bars = int(EXIT_CFG["max_trade_age_hours"] * 60 / bar_minutes)

    if verbose:
        print(f"\n  ── Backtest: {symbol} ──")
        print(f"  Candles:   {len(candles)} M15 bars  ({candles['time'].iloc[0]} → {candles['time'].iloc[-1]})")
        print(f"  Multi-TF:  {use_multi_tf}  | H1 bars={len(h1) if h1 is not None else 0}  H4 bars={len(h4) if h4 is not None else 0}")
        print(f"  SL/TP:     {sl_pips}/{tp_pips} pips  ({tp_pips/sl_pips:.2f} R:R target)")
        print(f"  Pip value: ${pip_value:.4f}/pip on 0.01 lot")
        print(f"  Spread:    {SPREAD_PIPS} pips per round trip\n")

    progress_step = max(len(candles) // 20, 100)

    for i in range(start_idx, len(candles)):
        bar = candles.iloc[i]
        t   = bar["time"]

        # ── Entry logic ──────────────────────────────────────────────────
        if in_trade is None:
            # Use bars 0..i-1 + use bar i CLOSE as the latest data.
            # The bot in production calls _compute_indicators with the latest
            # CLOSED bar — so on bar i (which has just closed) it's bars 0..i.
            m15_slice = candles.iloc[max(0, i - 250): i + 1]
            if use_multi_tf:
                # H1 bar closes 1h after open, H4 closes 4h after open
                h1_slice = slice_up_to(h1, t, 250, bar_seconds=3600)
                h4_slice = slice_up_to(h4, t, 250, bar_seconds=14400)
                if len(h1_slice) < 50 or len(h4_slice) < 30:
                    continue
            else:
                h1_slice = h4_slice = m15_slice

            m15_ind = analyzer._compute_indicators(m15_slice)
            h1_ind  = analyzer._compute_indicators(h1_slice)
            h4_ind  = analyzer._compute_indicators(h4_slice)
            signal  = analyzer._multi_tf_signal(m15_ind, h1_ind, h4_ind)

            if signal["direction"] in ("BUY", "SELL") and signal["confidence"] >= MIN_CONFIDENCE:
                entry = float(bar["c"])
                in_trade = {
                    "direction": signal["direction"],
                    "entry":     entry,
                    "open_bar":  i,
                    "open_time": t,
                    "sl":        entry - sl_pips * pip if signal["direction"] == "BUY" else entry + sl_pips * pip,
                    "tp":        entry + tp_pips * pip if signal["direction"] == "BUY" else entry - tp_pips * pip,
                    "be_done":   False,
                    "trail_done":False,
                    "peak_pips": 0.0,
                    "score":     signal["score"],
                    "confidence":signal["confidence"],
                    "factor_scores": signal["factor_scores"],
                }
        # ── Active trade management ──────────────────────────────────────
        else:
            high, low = float(bar["h"]), float(bar["l"])
            d = in_trade["direction"]
            entry = in_trade["entry"]

            move_pips = ((high if d == "BUY" else low) - entry) / pip * (1 if d == "BUY" else -1)
            in_trade["peak_pips"] = max(in_trade["peak_pips"], move_pips)

            # Adjust SL: breakeven, then trailing
            if not in_trade["be_done"] and in_trade["peak_pips"] >= EXIT_CFG["breakeven_trigger_pips"]:
                buf = EXIT_CFG["breakeven_buffer_pips"] * pip
                in_trade["sl"] = entry + buf if d == "BUY" else entry - buf
                in_trade["be_done"] = True

            if in_trade["peak_pips"] >= EXIT_CFG["trailing_start_pips"]:
                trail = EXIT_CFG["trailing_distance_pips"] * pip
                if d == "BUY":
                    new_sl = high - trail
                    if new_sl > in_trade["sl"]:
                        in_trade["sl"] = new_sl
                else:
                    new_sl = low + trail
                    if new_sl < in_trade["sl"]:
                        in_trade["sl"] = new_sl

            hit = None
            exit_price = None

            def _classify_stop():
                # Distinguish between true loss-stops and breakeven/trailing stops
                if in_trade["be_done"] or in_trade["peak_pips"] >= EXIT_CFG["trailing_start_pips"]:
                    return "TRAIL" if in_trade["peak_pips"] >= EXIT_CFG["trailing_start_pips"] else "BE"
                return "SL"

            if d == "BUY":
                if low <= in_trade["sl"]:
                    hit, exit_price = _classify_stop(), in_trade["sl"]
                elif high >= in_trade["tp"]:
                    hit, exit_price = "TP", in_trade["tp"]
            else:
                if high >= in_trade["sl"]:
                    hit, exit_price = _classify_stop(), in_trade["sl"]
                elif low <= in_trade["tp"]:
                    hit, exit_price = "TP", in_trade["tp"]

            # Time-decay close
            if not hit and (i - in_trade["open_bar"]) >= max_age_bars:
                hit, exit_price = "TIME", float(bar["c"])

            if hit:
                pnl_pips = (exit_price - entry) / pip * (1 if d == "BUY" else -1) - SPREAD_PIPS
                pnl_usd  = pnl_pips * pip_value
                trades.append({
                    "open_time":  in_trade["open_time"],
                    "close_time": t,
                    "direction":  d,
                    "entry":      entry,
                    "exit":       exit_price,
                    "result":     hit,
                    "pips":       round(pnl_pips, 2),
                    "usd":        round(pnl_usd, 2),
                    "score":      in_trade["score"],
                    "confidence": in_trade["confidence"],
                    "duration_bars": i - in_trade["open_bar"],
                })
                # Attribute P&L to factors that had a non-zero score
                for fk, fv in in_trade["factor_scores"].items():
                    base = fk.split("_", 1)[0]      # f1, f2, ..., f11
                    if isinstance(fv, (int, float)) and fv != 0 and base in factor_running:
                        factor_running[base] += pnl_pips
                        factor_count[base]   += 1
                in_trade = None

        if verbose and i % progress_step == 0 and i > start_idx:
            done_pct = (i - start_idx) / (len(candles) - start_idx) * 100
            print(f"    {done_pct:5.1f}%  ({i}/{len(candles)})  trades_so_far={len(trades)}")

    # ── Stats ────────────────────────────────────────────────────────────
    df = pd.DataFrame(trades)
    if df.empty:
        print("\n  ⚠️  No trades triggered. Strategy gates are too tight for this period/data.")
        return {"trades": 0, "candles": len(candles)}

    wins   = df[df["pips"] > 0]
    losses = df[df["pips"] <= 0]
    total_pips = df["pips"].sum()
    total_usd  = df["usd"].sum()
    win_rate   = len(wins) / len(df) * 100 if len(df) else 0
    avg_win    = wins["pips"].mean() if len(wins) else 0
    avg_loss   = losses["pips"].mean() if len(losses) else 0
    rr         = abs(avg_win / avg_loss) if avg_loss else 0
    expectancy = total_pips / len(df)

    # Equity curve & drawdown
    df = df.sort_values("close_time").reset_index(drop=True)
    df["cum_pips"] = df["pips"].cumsum()
    df["cum_usd"]  = df["usd"].cumsum()
    peak = df["cum_pips"].cummax()
    dd   = (df["cum_pips"] - peak)
    max_dd_pips = dd.min()

    by_dir   = df.groupby("direction")["pips"].agg(["count", "sum", "mean"])
    by_exit  = df.groupby("result")["pips"].agg(["count", "sum", "mean"])

    summary = {
        "symbol":         symbol,
        "candles":        len(candles),
        "trades":         len(df),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(win_rate, 1),
        "total_pips":     round(float(total_pips), 1),
        "total_usd":      round(float(total_usd), 2),
        "avg_win_pips":   round(float(avg_win), 1),
        "avg_loss_pips":  round(float(avg_loss), 1),
        "rr":             round(float(rr), 2),
        "expectancy_pips": round(float(expectancy), 2),
        "max_dd_pips":    round(float(max_dd_pips), 1),
        "by_direction":   by_dir.to_dict(),
        "by_exit":        by_exit.to_dict(),
        "factor_contribution_pips": {k: round(v, 1) for k, v in factor_running.items() if factor_count[k] > 0},
        "factor_trade_count":       factor_count,
    }

    if verbose:
        print(f"\n  ━━━━ {symbol} RESULTS ━━━━")
        print(f"  Trades:        {len(df)}  ({len(wins)} W / {len(losses)} L)")
        print(f"  Win rate:      {win_rate:.1f}%")
        print(f"  Total P&L:     {total_pips:+.1f} pips  =  ${total_usd:+,.2f}")
        print(f"  Avg win:       {avg_win:+.1f} pips")
        print(f"  Avg loss:      {avg_loss:+.1f} pips")
        print(f"  R:R achieved:  1:{rr:.2f}    (target 1:{tp_pips/sl_pips:.2f})")
        print(f"  Expectancy:    {expectancy:+.2f} pips/trade")
        print(f"  Max DD:        {max_dd_pips:.1f} pips")
        print(f"\n  Exits breakdown:")
        for r, row in by_exit.iterrows():
            print(f"    {r:5}  count={int(row['count']):3}  sum={row['sum']:+8.1f}  avg={row['mean']:+6.1f}")
        print(f"\n  Direction breakdown:")
        for r, row in by_dir.iterrows():
            print(f"    {r:5}  count={int(row['count']):3}  sum={row['sum']:+8.1f}  avg={row['mean']:+6.1f}")

    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--symbol", required=True, choices=list(SYMBOL_CFG.keys()))
    p.add_argument("--no-multi-tf", action="store_true")
    p.add_argument("--save", default=None, help="Save summary JSON")
    args = p.parse_args()

    res = run_backtest(args.csv, args.symbol, use_multi_tf=not args.no_multi_tf)
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        with open(args.save, "w") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"\n  ✅ Summary saved → {args.save}")


if __name__ == "__main__":
    main()
