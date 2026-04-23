---
name: ny-losing-trade
description: Avoids trading during NEW_YORK session due to low win rate
type: filter
version: 1.0.0
auto_generated: True
generated_from: Losing in NEW_YORK session (WR: 25%, avg: +2.5pips)
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: [all]
  sessions: [NEW_YORK]
  hours: [9, 10, 11, 12, 13, 14, 15, 16]
  min_adx: 20
---

# Strategy

This strategy avoids trading during the NEW_YORK session due to a detected losing pattern.
## Entry Conditions
No entry during NEW_YORK session.
## Exit Conditions
No exit conditions, as this strategy focuses on filtering out trades during the specified session.
## Filter Conditions
- Symbols: All
- Sessions: NEW_YORK
- Hours: 9am-4pm EST
- ADX Range: Above 20