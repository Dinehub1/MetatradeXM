---
name: 'position-scaling'
description: 'Add to winning positions when AI confirms trend continuation and account balance supports it'
type: 'scaler'
version: '1.0.0'
performance:
  total_scales: 0
  successful_scales: 0
  failed_scales: 0
  total_pips_added: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  sessions: [LONDON, LONDON_NY_OVERLAP, NEW_YORK]
  min_profit_pips: 15
  min_confidence: 0.65
  max_scales_per_trade: 2
  min_free_margin_pct: 30
  max_total_risk_pct: 3.0
  scale_lot_fraction: 0.5
min_confidence: 0.5
min_profit_pips: 8
---
# Position Scaling Skill

## Concept
When a trade is winning and AI confirms the trend is continuing, add a smaller
position in the same direction to compound gains. This is called "pyramiding".

## Entry Rules (ALL must be true)
1. Existing position is profitable by at least `min_profit_pips` (15 pips)
2. AI analysis gives `min_confidence` (65%+) in the SAME direction
3. Free margin is above `min_free_margin_pct` (30% of balance)
4. Total open risk (all positions) is below `max_total_risk_pct` (3% of balance)
5. Max `max_scales_per_trade` (2) additional lots already added
6. Only in liquid sessions (London, Overlap, New York)

## Position Sizing
- Scale lot = original lot × `scale_lot_fraction` (0.5x = half original size)
- Each scale is smaller than the last (pyramid shape, not inverted)
- SL for scaled position = original entry price (lock in minimum breakeven)

## Exit
- Scaled positions close with the parent trade
- If original trade hits SL, scaled positions close too (same SL level)

## Risk Management
- Never scale in Asian session (thin liquidity, wider spreads)
- Never scale if account equity < 98% of balance (already losing elsewhere)
- Never scale if adding would push total margin > 70% of balance