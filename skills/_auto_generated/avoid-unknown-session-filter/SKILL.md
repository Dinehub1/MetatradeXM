---
name: avoid-unknown-session-filter
description: Blocks trades during UNKNOWN session due to poor performance (20% WR, -3261.6pips avg loss). Only enter trades in known high-probability sessions.
type: filter
version: 1.0.0
auto_generated: True
generated_from: Losing in UNKNOWN session (WR: 20%, avg: -3261.6pips)
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: [EURUSD, GBPUSD, USDJPY, XAUUSD]
  sessions: [LONDON, NEW_YORK, TOKYO, SYDNEY]
  min_adx: 25
---

# Strategy

## Pattern Detected
- **Session:** UNKNOWN
- **Win Rate:** 20%
- **Average Pips:** -3261.6pips
- **Confidence:** 80%
- **Sample Size:** 5 trades

## Filter Rules

### Entry Conditions (BLOCK if ALL met)
- Current session = UNKNOWN
- Any symbol
- Any time frame

### Entry Conditions (ALLOW if ANY met)
- Session IN: LONDON, NEW_YORK, TOKYO, SYDNEY
- ADX > 25 (strong trend confirmation)
- Win rate for session > 50%

### Exit Rules
- Close all positions if session changes to UNKNOWN
- Apply trailing stop at 2x ATR when in unknown session

## Logic
This filter prevents new trades during the UNKNOWN session since historical data shows extreme losses. Existing positions should be managed with caution if accidentally opened.

## Notes
- 80% confidence suggests pattern is likely persistent
- 5 sample size indicates need for more data before full confidence
- Consider logging all UNKNOWN session occurrences for future analysis