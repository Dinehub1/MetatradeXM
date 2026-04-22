# MetatradeXM — AI-Powered MT5 Trading System

An autonomous, multi-symbol automated trading system (XAUUSD + XAGUSD) running on [MetaApi](https://metaapi.cloud/) (works on Mac/Linux/Windows) and powered by local Ollama AI.

## Features

- **Cross-Platform**: Uses MetaApi to connect to MT5 instead of the native Windows-only MT5 terminal. Runs natively on macOS, Linux, or Windows.
- **Smart Analytics**: Real-time 9-factor scoring logic analyzing H4 and H1 timeframe confluence (Trend, RSI, MACD, ADX, Stochastic, Bollinger Bands).
- **AI Decision Engine**: Local Ollama (minimax-m2.7:cloud model by default) processes market data and confirms trades with quantitative reasoning.
- **Self-Improving Memory**: Logs trades to SQLite, detects patterns, and adjusts factor weights dynamically if a strategy loses edge.
- **Smart Exit Manager**: Adaptive trade management including momentum-reversal exits, breakeven stops, time decay, and trailing stops.
- **Position Scaling**: Adds to winning positions (pyramiding) when AI confirms continued trend strength.
- **Live Dashboard**: Web UI (Flask) for real-time monitoring of P&L, system health, and AI reasoning.

## Architecture

![Architecture](docs/images/architecture_placeholder.png)

Read the full architecture overview in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick Start

### 1. Requirements

- Python 3.9+
- [Ollama](https://ollama.com/) running locally (`minimax-m2.7:cloud` model)
- A MetaApi Cloud account + MT5 Demo account (XM Global or similar)

### 2. Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env from template
cp .env.example .env
# Edit .env and add your METAAPI_TOKEN and METAAPI_ACCOUNT_ID
```

For detailed setup, see [docs/SETUP.md](docs/SETUP.md).

### 3. Run the System

The easiest way to run the entire system is using the startup script:

```bash
# Start in paper trading mode
bash start_trading_cycle.sh --dry

# Start live trading
bash start_trading_cycle.sh

# Stop everything
bash start_trading_cycle.sh --stop
```

Check the dashboard at `http://localhost:8889` (or the IP of your server).

## Documentation

Full documentation is available in the `/docs` directory:
- [System Architecture](docs/ARCHITECTURE.md)
- [Setup & Installation](docs/SETUP.md)
- [Configuration Guide](docs/CONFIGURATION.md)
- [Operations & Monitoring](docs/OPERATIONS.md)
- [Profitability & Tuning](docs/PROFITABILITY.md)
- [Self-Improvement Engine](docs/SELF_IMPROVEMENT.md)

## Legacy Entry Point

The original Windows-only CLI `bot.py` is preserved for reference, but `continuous_trader.py` is the official 24/7 autonomous engine spanning Mac/Linux/Windows.
