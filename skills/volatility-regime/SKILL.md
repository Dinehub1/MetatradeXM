---
name: 'volatility-regime'
description: 'Adjust trading behavior based on current ATR vs historical ATR percentile'
type: 'filter'
version: '1.0.0'
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  min_atr_percentile: 10
  max_atr_percentile: 95
min_atr_percentile: 40
max_atr_percentile: 70
---
# Volatility Regime Filter

## Concept
Markets alternate between low-volatility compression and high-volatility expansion.
Trading in dead markets (ATR < 10th percentile) generates false signals.
Trading in chaotic markets (ATR > 95th percentile) risks oversized losses.

## Rules
1. Block ALL trades when ATR is below 10th percentile of 100-bar history
2. Reduce lot size by 50% when ATR is above 95th percentile
3. Optimal trading window: ATR between 25th-75th percentile
4. BB squeeze + rising ATR = breakout imminent — allow trades but tighten SL