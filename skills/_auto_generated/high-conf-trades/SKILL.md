---
name: high-conf-trades
description: trades with high confidence
type: filter
version: 1.0.0
auto_generated: True
generated_from: High-confidence (>=70%) trades: WR=31% (n=13)
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: [all]
  sessions: [all]
  hours: [9, 10, 11, 12, 13, 14, 15]
  min_adx: 20
---

# Strategy

Entry conditions: when confidence is >= 70%
Exit conditions: when trade reaches stop loss or take profit
This strategy filters trades based on high confidence levels, aiming to increase win rates.