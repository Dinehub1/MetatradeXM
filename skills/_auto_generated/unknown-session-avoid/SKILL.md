---
name: unknown-session-avoid
description: Filters out trades during UNKNOWN session patterns with historically poor performance (17% WR, -2721.7pips avg loss, 83% confidence)
type: filter
version: 1.0.0
auto_generated: True
generated_from: Losing in UNKNOWN session (WR: 17%, avg: -2721.7pips)
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: []
  sessions: [UNKNOWN]
  min_adx: 0
---

# Strategy

## Purpose
This filter prevents trade entries during UNKNOWN session conditions that have demonstrated consistently poor performance with only 17% win rate and average losses of -2721.7 pips per trade.

## Filter Conditions
1. **Session Detection**: Block all trade entries when current session is classified as UNKNOWN
2. **Minimum Confidence**: Apply filter when pattern confidence ≥ 80% (current: 83.33%)
3. **Sample Size**: Pattern based on 6 historical samples with high confidence

## Implementation Rules
- **Entry Block**: REJECT all new long and short entries when session = UNKNOWN
- **Exit Permission**: Existing open positions may still be closed normally
- **Filter Priority**: This is a HIGH-PRIORITY filter that cannot be bypassed by lower-priority boosters
- **Override**: Manual disable required to trade in UNKNOWN sessions

## Backtest Evidence
| Metric | Value |
|--------|-------|
| Win Rate | 16.67% (1 of 6) |
| Average Result | -2,721.7 pips |
| Confidence | 83.33% |
| Sample Size | 6 trades |

## Recommendation
Avoid all trading activity during UNKNOWN session conditions until sufficient positive data is collected or session classification accuracy improves. Consider investigating why session is being classified as UNKNOWN and improving session detection logic.