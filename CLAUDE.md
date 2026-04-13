# MetatradeXM — Developer Context

> **Note**: This file provides immediate context for AI assistants analyzing this repository.

## System Overview

MetatradeXM is a quantitative, autonomous Forex trading bot optimized for precious metals (XAUUSD, XAGUSD). It uses a hybrid deterministic/AI architecture:
1. **Deterministic Scoring**: Collects technical indicators (RSI, MACD, ADX, ATR, etc.) across timeframes (H1, H4, M15) and calculates a 9-factor score.
2. **AI Confirmation**: Feeds the raw data and score to a local Ollama model (default: `minimax-m2.7:cloud`), asking for a final BUY/SELL/HOLD decision.
3. **Execution Execution**: Uses the MetaApi Cloud SDK (cross-platform) to place trades and modify stops on MT5.
4. **Smart Management**: The `SmartExitManager` and `PositionScaler` dynamically manage open positions (breakeven stops, time decay closing, momentum reversal exits, pyramiding).
5. **Self-Improvement**: `self_improver.py` analyzes past trades stored in `trade_memory.db` and adjusts `scoring_weights.json` to adapt to changing market regimes.

## Key Modules

- `start_trading_cycle.sh`: Main entry script. Launches trader, dashboard, and watchdog.
- `continuous_trader.py`: The 24/7 asynchronous trading engine.
- `metaapi_bridge.py`: Wraps `metaapi-cloud-sdk` to provide simple get_tick/place_order APIs. Replaces native Windows `MetaTrader5`.
- `analyzer.py`: The 9-factor scoring logic and technical indicators builder.
- `ollama_client.py`: Handles prompts and JSON parsing from the local AI model.
- `smart_exit.py`: Adaptive exits (trailing stops, partial closes, momentum reversal).
- `position_scaler.py`: Adds volume to winning trades based on AI confirmation.
- `dashboard.py`: Lightweight Flask web UI running on port 8889.

## Common Developer Workflows

### Modifying the Strategy
- **Base Scoring**: Modify `scoring_weights.json` thresholds (`buy_threshold`, `ranging_penalty`, etc.) or individual factor weights.
- **Indicators**: Add new pandas_ta indicators into `analyzer.py`.
- **Exits**: Tune Pips buffers or decay times in `smart_exit.py` `EXIT_CFG`.

### Connecting to a New Broker
Update the `.env` file with the relevant MetaApi token and MT5 account ID. The exact symbol naming convention (`broker_symbol`) is defined dynamically in `continuous_trader.py`'s `SYMBOLS` map.

### Live vs Paper Trading
The `continuous_trader.py` accepts a `--dry` flag. When enabled, signals are generated and logged, but no orders are sent to the broker. This flag is configurable via `start_trading_cycle.sh --dry`.

## Recent Architecture Changes
- **Profitability Fixes (April 2026)**: Raised scoring thresholds from ±6 to ±15 to filter noise, implemented `SmartExitManager` to prevent massive drawdown, reduced max correlated positions from 3 to 1.
- **Cross-Platform Migration**: Ported from native `MetaTrader5` (Windows only) to `metaapi-cloud-sdk`, allowing the bot to run natively on macOS/Linux servers.
- **AI Backend Migration**: Shifted from Anthropic Claude API to local Ollama API to reduce OPEX costs and latency.
