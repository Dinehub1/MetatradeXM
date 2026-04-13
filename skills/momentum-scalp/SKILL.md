---
name: momentum-scalp
description: High-ADX momentum entries when trend is strong and accelerating
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
  sessions: [LONDON, LONDON_NY_OVERLAP, NEW_YORK]
  min_adx: 30
  boost_amount: 0.08
---

# Momentum Scalp Strategy

## Concept
When ADX > 30 and +DI/-DI separation is wide, the trend is strong.
Enter in the direction of +DI > -DI (BUY) or -DI > +DI (SELL).

## Rules
1. Only active when ADX >= 30 (strong trend confirmed)
2. Only during liquid sessions (London, Overlap, New York)
3. Boosts confidence by 8% when conditions met
4. Works best with tight ATR-based stops (2x ATR)
