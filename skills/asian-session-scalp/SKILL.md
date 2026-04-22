---
name: asian-session-scalp
description: Scalping strategy for low-volume Asian session with tight stops and quick exits
type: booster
version: 1.0.0
performance:
  total_trades: 0
  wins: 0
  losses: 0
  total_pips: 0.0
  win_rate: 0.0
  last_updated: null
conditions:
  symbols: [XAUUSD, XAGUSD]
  sessions: [ASIAN]
  max_adx: 25
  boost_amount: 0.03
---
# Asian Session Scalping Strategy

## Concept
During the Asian session (22:00-07:00 UTC), gold and silver typically exhibit:
- Lower volume and volatility
- Range-bound price action
- Mean-reverting tendencies
- False breakouts that quickly reverse

This strategy is designed for scalping in these conditions with:
- Very tight stop losses (0.5x ATR)
- Small profit targets (1x ATR)
- Quick entry/exit based on short-term momentum
- Only active during low ADX conditions (ranging market)

## Rules
1. **Only active during ASIAN session** (22:00-07:00 UTC)
2. **Only when ADX < 25** (ranging/low trend market)
3. **Enter on short-term momentum**:
   - BUY when price pulls back to support + bullish momentum on M5/M15
   - SELL when price rallies to resistance + bearish momentum on M5/M15
4. **Tight risk management**:
   - Stop Loss: 0.5x ATR from entry
   - Take Profit: 1.0x ATR from entry
   - Maximum trade duration: 20 minutes
5. **Requires confirmation**:
   - Price must be within Bollinger Bands (avoid extremes)
   - RSI must show momentum divergence (not overbought/oversold)
6. **Boosts confidence by 3%** when all conditions met

## Exit Rules
- **Primary**: Hit take profit target (1x ATR)
- **Secondary**: Hit stop loss (0.5x ATR)
- **Time-based**: Close after 20 minutes if neither target hit
- **Reversal signal**: Opposite momentum signal on M5

## Position Sizing
- Use minimal position size appropriate for account balance
- Consider 50% of normal size due to lower conviction in ranging markets

## Why This Works in Asian Session
- Low ADX indicates lack of strong trend - perfect for mean reversion
- Tight stops accommodate the lower volatility environment
- Small targets are realistic given the reduced price movement
- Quick exits prevent giving back profits in choppy conditions

## Integration
Apply AFTER trend-filtering skills but BEFORE execution to ensure we only scalp in appropriate conditions.
