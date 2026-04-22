---
name: 'time-bias'
description: 'Boost confidence during the London/NY overlap (13-16 UTC) — peak Gold liquidity'
type: 'booster'
version: '1.1.0'
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  preferred_hours: [13, 14, 15]
  boost_amount: 0.05
---
# Time-of-Day Bias Strategy

## Research Basis
Gold (XAUUSD) shows peak liquidity and directional conviction during the London/NY overlap
(13:00–16:00 UTC). This skill boosts signal confidence by +5% during those hours.

The ASIAN BUY block was removed (v1.1.0): Shanghai Gold Exchange is active during Asian
hours and generates legitimate long setups. The TimeOfDayFilter in strategy_filters.py
already blocks the true dead zone (23:30–00:30 UTC crossover).

## Rules
1. **Boost +5% confidence** during hours 13, 14, 15 UTC for any directional signal
2. No blocking — session-level vetoes are handled by TimeOfDayFilter

## Self-Improvement Triggers
- If overlap boost leads to losses, reduce boost_amount
- If non-overlap hours start outperforming, expand preferred_hours
