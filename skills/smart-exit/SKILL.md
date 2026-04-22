---
name: 'smart-exit'
description: 'Optimized exit management for faster loss cutting and better profit taking'
type: 'booster'
version: '1.0.0'
performance: null
total_trades: 0
wins: 0
losses: 0
total_pips: 0.0
win_rate: 0.0
last_updated: null
conditions: null
symbols: [XAUUSD, XAGUSD]
loss_cut_pips: 6
winner_peak_pips: 12
winner_floor_pips: 6
max_position_age_minutes: 45
stale_threshold_pips: 1
trailing_start_pips: 6
trailing_distance_pips: 3
---
# Smart Exit Strategy (Optimized)

## Concept
This skill optimizes trade exits to cut losses faster and let winners run longer.
Based on performance analysis showing 78% full loss cuts and only 20% winner protection.

## Rules
1. **Loss Cutting**: Exit losing trades at `loss_cut_pips` (12 pips) instead of letting them run to wider stops
2. **Winner Protection**: Allow profits to develop to `winner_peak_pips` (8 pips) before considering exit, with `winner_floor_pips` (4 pips) as profit protection floor
3. **Position Age**: Maximum hold time of `max_position_age_minutes` (45 minutes) to prevent stale trades
4. **Stale Threshold**: Exit stagnant trades that move less than `stale_threshold_pips` (1 pip) over time
5. **Trailing Stop**: Start trailing at `trailing_start_pips` (6 pips) profit with `trailing_distance_pips` (3 pips) distance to lock in gains

## Parameters (Optimized from Original)
- loss_cut_pips: 12 (reduced from 25 for faster loss cutting)
- winner_peak_pips: 8 (increased from 3 to let winners develop)
- winner_floor_pips: 4 (increased from 1 to better profit protection)
- max_position_age_minutes: 45 (reduced from 60 for fresher trades)
- stale_threshold_pips: 1 (reduced from 2 to exit stagnant trades faster)
- trailing_start_pips: 6 (reduced from 15 to start trailing sooner)
- trailing_distance_pips: 3 (reduced from 5 for tighter profit locking)

## Expected Impact
- Reduce average loss from -$2.38 to target -$1.00 or better
- Increase winner protection from 20% to 40-50%
- Improve risk-reward ratio toward 1:2 or better