"""
Simple indicator backtest — walks through candles and applies the
same indicator logic as the live bot to measure hypothetical P&L.
No slippage/spread modelling (realistic spread is added as a constant).
"""

import pandas as pd
import numpy as np
from core.analyzer import MarketAnalyzer


def run_backtest(candles: pd.DataFrame, config: dict):
    print("\n── Backtest ─────────────────────────────────────────────")
    print(f"  Symbol:    {config['symbol']}")
    print(f"  Timeframe: {config['timeframe']}")
    print(f"  Candles:   {len(candles)}")
    print(f"  SL pips:   {config['sl_pips']}  |  TP pips: {config['tp_pips']}\n")

    analyzer    = MarketAnalyzer(use_ai=False)  # indicators only for speed
    pip         = 0.01 if "JPY" in config.get("symbol", "") else 0.0001
    spread_pips = 2                                  # typical EURUSD spread
    sl_pips     = config["sl_pips"]
    tp_pips     = config["tp_pips"]

    trades      = []
    in_trade    = None

    for i in range(100, len(candles)):
        slice_df = candles.iloc[:i].copy()
        ind_m15  = analyzer._compute_indicators(slice_df)
        ind_h1   = ind_m15   # no H1/H4 in backtest — reuse M15
        ind_h4   = ind_m15
        signal   = analyzer._multi_tf_signal(ind_m15, ind_h1, ind_h4)

        if in_trade is None:
            if signal["direction"] in ("BUY", "SELL") and signal["confidence"] >= 0.60:
                entry = candles.iloc[i]["c"]
                in_trade = {
                    "direction": signal["direction"],
                    "entry":     entry,
                    "sl":        entry - sl_pips * pip if signal["direction"] == "BUY"
                                 else entry + sl_pips * pip,
                    "tp":        entry + tp_pips * pip if signal["direction"] == "BUY"
                                 else entry - tp_pips * pip,
                    "bar_open":  i,
                }
        else:
            bar   = candles.iloc[i]
            close = bar["c"]
            hit   = None

            if in_trade["direction"] == "BUY":
                if bar["l"] <= in_trade["sl"]:
                    hit = "SL"
                elif bar["h"] >= in_trade["tp"]:
                    hit = "TP"
            else:
                if bar["h"] >= in_trade["sl"]:
                    hit = "SL"
                elif bar["l"] <= in_trade["tp"]:
                    hit = "TP"

            if hit:
                pnl_pips = (tp_pips - spread_pips) if hit == "TP" else -(sl_pips + spread_pips)
                trades.append({
                    "bar":    i,
                    "dir":    in_trade["direction"],
                    "result": hit,
                    "pips":   pnl_pips,
                })
                in_trade = None

    if not trades:
        print("  No trades triggered in this period.")
        return

    wins       = [t for t in trades if t["result"] == "TP"]
    losses     = [t for t in trades if t["result"] == "SL"]
    total_pips = sum(t["pips"] for t in trades)
    win_rate   = len(wins) / len(trades) * 100
    avg_win    = np.mean([t["pips"] for t in wins]) if wins else 0
    avg_loss   = np.mean([t["pips"] for t in losses]) if losses else 0
    rr         = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    print(f"  Total trades:  {len(trades)}")
    print(f"  Wins:          {len(wins)}  ({win_rate:.1f}%)")
    print(f"  Losses:        {len(losses)}")
    print(f"  Total P&L:     {total_pips:+.1f} pips")
    print(f"  Avg win:       {avg_win:+.1f} pips")
    print(f"  Avg loss:      {avg_loss:+.1f} pips")
    print(f"  R:R achieved:  1:{rr:.2f}")
    print("─" * 54 + "\n")

    # Simple equity curve
    running = 0
    print("  Equity curve (pips):")
    for t in trades:
        running += t["pips"]
        bar = "█" * int(abs(running) / 5)
        sign = "+" if running >= 0 else "-"
        print(f"    {sign}{abs(running):5.0f}  {bar}")
    print()
