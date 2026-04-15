---
name: 'fade-detection'
description: 'Block counter-trend trades when momentum is exhausted and reversal is likely'
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
  min_adx: 35
  block_when: null
  sell: null
  rsi_max: 40
  bb_position: 'ABOVE_HIGH'
  buy: null
  rsi_min: 60
min_adx: 40
---
# Fade Detection Skill

## Concept
When an asset is deeply oversold (RSI < 40) or overbought (RSI > 60) with strong ADX (>35),
the trend is EXTENDED and likely to reverse. Continuing to trade in the direction of a
mature trend when indicators show exhaustion leads to catching reversals.

This skill blocks entries that go against the grain of an exhausted move.

## Rules
1. If ADX > 35 (strong trend) AND RSI < 40 (oversold) AND not BUYING → block SELL
2. If ADX > 35 (strong trend) AND RSI > 60 (overbought) AND not SELLING → block BUY
3. If ADX > 35 AND BB_position at extreme AND momentum diverging → strong block
4. This overrides other skills — do not boost into an exhausted trend

## Why This Works
- ADX > 35 means the trend is MATURE, not fresh
- RSI < 40 with ADX > 35 means the trend has run far on momentum
- Mean reversion after extended trends is high probability
- The bot was dying by SELLING XAGUSD at 75.0 when RSI was 39 and ADX was 45

## Integration
Apply BEFORE other skills in evaluate_all() to ensure exhausted trends are blocked early.