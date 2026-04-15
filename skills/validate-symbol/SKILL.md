---
name: validate-symbol
description: Validates that trading symbol is not UNKNOWN or empty to prevent execution errors
type: filter
version: 1.0.0
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
conditions:
  symbols: [XAUUSD, XAGUSD]
---

# Symbol Validation Filter

## Purpose
Prevents trade execution with invalid symbols (UNKNOWN, empty, or malformed) that cause catastrophic losses due to symbol resolution failures.

## Entry Conditions
- **Symbol Validation**: Only allow trades with known, valid symbols (XAUUSD, XAGUSD)
- **Block Trigger**: Reject any trade where symbol is "UNKNOWN", empty, or not in the allowed list

## Filter Rules
1. Check if symbol is in the allowed symbols list [XAUUSD, XAGUSD]
2. If symbol is NOT in allowed list, reject trade signal
3. Only allow trades with properly resolved symbols

## Rationale
- UNKNOWN symbol trades have caused >99% of account drawdown in recent sessions
- These trades show entry_price=0.0, confidence=0.0, and massive losses
- Symbol validation prevents execution when broker symbol mapping fails
- This is a critical safety filter that must be applied before other strategy conditions

## Usage
Apply this filter FIRST in the skill pipeline to catch invalid symbols before they reach the execution layer.