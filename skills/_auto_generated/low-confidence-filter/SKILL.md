---
name: low-confidence-filter
description: Filters out low-confidence (55-70%) signals that historically win only 25% of the time
type: filter
version: 1.0.0
auto_generated: True
generated_from: Low-confidence (55-70%) trades: WR=25% (n=8)
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: [ALL]
  sessions: [ALL]
  min_adx: 20
---

# Low-Confidence Trade Filter

## Purpose
Avoid trades with confidence scores between 55-70%, as historical data shows these signals win only 25% of the time (n=8).

## Entry Filter Rules

### Primary Condition
- **REJECT** any signal where:
  - `confidence >= 0.55` AND `confidence <= 0.70`

### Secondary Confirmation
- Only proceed with signals where:
  - `confidence > 0.70` (high confidence)
  - OR `confidence < 0.55` (explicit low confidence with favorable R:R)

## Confidence Calibration Matrix

| Confidence Range | Action | Historical WR |
|------------------|--------|---------------|
| 55-70% | ❌ REJECT | 25% |
| 71-85% | ✅ ACCEPT | Use with trend |
| >85% | ✅ ACCEPT | Use always |
| <55% | ⚠️ REVIEW | Requires 2:1 R:R |

## Exit Conditions
If trade is taken despite filter (manual override):
- Set hard stop at 1.5x ATR
- Take profit at 1:1 R:R (given low WR expectation)

## Notes
- Sample size is small (n=8), re-evaluate after n>30
- Consider adjusting filter to 60-75% range as data accumulates