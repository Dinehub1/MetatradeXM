---
name: correlation-filter
description: Block trades when XAU and XAG signals diverge during high correlation periods
type: filter
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
  min_correlation: 0.85
---

# Correlation Filter Strategy

## Concept
Gold and Silver are highly correlated (~0.85-0.95 typically).
When both metals show the same signal, conviction is higher.
When they diverge during high-correlation periods, something unusual is happening — HOLD.

## Rules
1. Track rolling 20-period correlation between XAUUSD and XAGUSD closes
2. When correlation > 0.85 and signals DIVERGE (one BUY, one SELL): block both
3. When correlation > 0.85 and signals ALIGN: boost confidence by 10%
4. When correlation < 0.70: treat each independently (decorrelated)
