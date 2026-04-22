# Profitability & Tuning Guide

MetatradeXM's profitability depends heavily on the parameters in `scoring_weights.json` and `continuous_trader.py`. If the bot begins consistently losing money, reference this guide to fix it.

## 🚨 The Noise Problem
The #1 reason algorithmic bots lose money is entering low-quality trades ("noise"). 

### The Threshold Rules
The Bot scores trades from -27 (Strongest Sell) to +27 (Strongest Buy).
- `buy_threshold` and `sell_threshold` dictate minimum entry requirements.
- **DANGER ZONE (±6):** A threshold of ±6 means the bot only needs minor alignment across 2 timeframes to enter. This historically caused a **15% Win Rate**.
- **PROFIT ZONE (±15):** A threshold of ±15 requires strong confluence across H4, H1, and momentum indicators. 

### The Ranging Penalty
Markets spend 70% of their time ranging (ADX < 20). Trend-following strategies fail in ranging markets.
- `ranging_penalty`: This multiplier triggers when ADX indicates no trend. 
- Historically, `0.8` let too many bad trades through. The default should remain `0.6` or lower, effectively forcing a "HOLD" when markets are flat.

## Strategy Tuning Parameters (`continuous_trader.py`)

| Parameter | Recommended | Explanation |
|-----------|-------------|-------------|
| `min_confidence` | `0.60` | Ollama AI outputs a confidence score 0.0 - 1.0. A score of 0.45 is literally a coin flip. Demand 0.60+ for entry. |
| `max_trades_per_sym`| `1` | Scaling (adding to winners) is handled safely by `position_scaler.py`. Do NOT let the main engine open 3 primary positions simultaneously on one symbol. It creates massive risk concentration. |
| `profit_close_pct` | `1.5` | Close all positions if total account profit spikes 1.5%. Greed kills automated systems. |
| `loss_close_pct` | `0.5` | Absolute hard floor. If an algo gets it wrong, get out aggressively. |

## The Smart Exit Manager

Trading isn't just about entries; it's about exits. `smart_exit.py` protects profitability:
1. **Time Decay:** Profitable setups shouldn't take 8 hours to play out. If a trade is barely in profit after 4 hours, it closes automatically (`stale_close`).
2. **Momentum Reversal:** If you buy and indicators flip to BEARISH 2 hours later, you should NOT hold until SL is hit. The AI reversal check intercepts and closes early.
3. **Breakeven Stops:** Once 5 pips into profit, SL moves to entry + 1 pip. The trade becomes "risk free."

If profitability drops, first check the Smart Exit Manager logs. Ensure it isn't disabled and the `ai_timeout` isn't blocking reversal checks.
