# MT5 AI Trading Bot — Claude-Powered

## Prerequisites

1. **MetaTrader 5** installed and running on Windows (MT5 Python lib only works on Windows)
2. **Python 3.10+**
3. **Anthropic API key** in your environment: `ANTHROPIC_API_KEY`

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key (Windows)
set ANTHROPIC_API_KEY=sk-ant-...

# 3. Open MetaTrader 5, log in to your account (demo recommended first)
#    Enable: Tools → Options → Expert Advisors → Allow algorithmic trading
```

## Usage (Claude Code terminal)

```bash
# Analyze current market (no trades placed)
python bot.py analyze

# Analyze a different symbol/timeframe
python bot.py analyze --symbol GBPUSD --timeframe H1

# Check account status and open positions
python bot.py status

# Run backtester on last 500 candles
python bot.py backtest

# Start live bot (DRY RUN — paper trades only, safe)
python bot.py run

# Start live bot on specific pair, disable AI (indicators only)
python bot.py run --symbol XAUUSD --no-ai

# Enable REAL trading (only when you're ready!)
python bot.py run --live
```

## Configuration

Edit the `CONFIG` dict at the top of `bot.py`:

| Key | Default | Description |
|-----|---------|-------------|
| `symbol` | `EURUSD` | Trading pair |
| `timeframe` | `M15` | Candle timeframe |
| `lot_size` | `0.01` | Fallback lot (micro) |
| `max_risk_pct` | `1.0` | Max % of balance per trade |
| `sl_pips` | `30` | Stop loss distance |
| `tp_pips` | `60` | Take profit distance (2:1 RR) |
| `max_open_trades` | `3` | Max concurrent positions |
| `loop_interval_s` | `60` | Seconds between cycles |
| `use_claude_ai` | `True` | Enable Claude reasoning |
| `dry_run` | `True` | Paper trade (no real orders) |

## Architecture

```
Claude Code terminal
        │
        ▼
   bot.py  (main loop + CLI)
   ├── mt5_bridge.py   ← MetaTrader 5 API (prices, orders, account)
   ├── analyzer.py     ← RSI, EMA, MACD, BB + Claude AI reasoning
   ├── risk_manager.py ← Position sizing, SL/TP calculation
   ├── logger.py       ← SQLite trade journal
   └── backtester.py   ← Indicator backtest on historical data
```

## Indicators Used

- **RSI (14)** — overbought/oversold
- **EMA 20/50/200** — trend direction stack
- **MACD (12/26/9)** — momentum crossovers
- **Bollinger Bands (20,2)** — price extremes
- **ATR (14)** — volatility (used in dynamic SL in future)

## Claude AI Role

When `use_claude_ai: True`, Claude receives:
- All indicator values
- Last 10 candles OHLCV
- Indicator-based preliminary signal

Claude returns a structured JSON:
```json
{
  "direction": "BUY",
  "confidence": 0.72,
  "reason": "RSI oversold with bullish EMA stack and MACD crossover confirms momentum shift."
}
```

This overrides the pure indicator signal, adding contextual reasoning.

## Warning

**Always test on a demo account first. Never risk money you cannot afford to lose.
This bot is for educational purposes. Past performance does not guarantee future results.**
# MetatradeXM
# MetatradeXM
