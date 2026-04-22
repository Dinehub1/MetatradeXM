---
name: avoid-unknown-session
description: Filters out trades during UNKNOWN sessions that show 20% win rate and -3261.6 pips average loss. Use as negative filter to avoid low-probability conditions.
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
  symbols: []
  sessions: [UNKNOWN]
  min_adx: null
---

# Strategy: Avoid Unknown Session Filter

## Purpose
This filter identifies and blocks trades during unidentified/UNKNOWN market sessions that demonstrate historically poor performance with only 20% win rate and average losses of -3261.6 pips.

## Entry Conditions
- **Session Detection**: System must be able to identify the current market session (London, NY, Asian, etc.)
- **Block Trigger**: If session is classified as "UNKNOWN", trade should be blocked
- **Confidence Threshold**: Minimum 0.8 confidence in session identification to avoid false unknowns

## Exit Conditions
- N/A - This is a filter, not a trading strategy

## Filter Rules
1. Check current session classification
2. If session = "UNKNOWN", reject trade signal
3. Only allow trades with clearly identified sessions
4. Consider adding session confirmation indicator to reduce unknown classifications

## Rationale
- Sample Size: 5 trades (limited but consistent pattern)
- Win Rate: 20% (very low, below 50% threshold)
- Average Loss: -3261.6 pips (severe losses per trade)
- Confidence: 0.8 (moderately high confidence despite small sample)

## Usage
Apply this filter BEFORE other strategy conditions to prevent capital allocation to unfavorable session conditions.
