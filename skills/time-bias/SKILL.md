---
name: 'time-bias'
description: 'Gold weakens during Asian session (22-07 UTC), rebounds during London/NY overlap (13-16 UTC)'
type: 'filter'
version: '1.0.0'
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  block_buy_sessions: [ASIAN]
  preferred_hours: [13, 14, 15]
  boost_amount: 0.05
sell_confirmation_rsi: 45
preferred_hours: []
block_buy_sessions: []
---
# Time-of-Day Bias Strategy

## Research Basis
Gold (XAUUSD) shows a well-documented intraday pattern:
- Weakness during Asian hours (22:00-07:00 UTC) — lower volume, drift lower
- Strength during London/NY overlap (13:00-16:00 UTC) — highest liquidity, tends to rally
- Silver follows a similar but less pronounced pattern

## Rules
1. **Block BUY during ASIAN session** — avoid going long when gold historically weakens
2. **Boost confidence during London/NY overlap** — +5% confidence for aligned signals
3. Allow SELL during any session (mean-reversion shorts during Asian can work)

## Self-Improvement Triggers
- If Asian BUY blocks are costing profitable trades (>60% would have been wins), reduce blocking
- If overlap boost leads to losses, reduce boost_amount