---
name: mean-reversion
description: Trade reversals at Bollinger Band extremes with RSI confirmation
type: booster
version: 1.0.0
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  max_adx: 25
  boost_amount: 0.06
---

# Mean Reversion Strategy

## Concept
In ranging markets (ADX < 25), price tends to bounce between Bollinger Bands.
When price hits lower BB + RSI oversold, expect bounce up.
When price hits upper BB + RSI overbought, expect pull back.

## Rules
1. Only in RANGING markets (ADX < 25)
2. Requires dual confirmation: BB extreme + RSI extreme
3. Boosts confidence by 6% for mean-reversion setups
4. Wider stops needed (3x ATR) as reversals can be slow
