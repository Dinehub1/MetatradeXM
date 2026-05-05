# MetatradeXM — AI-Powered Autonomous Forex Trading Bot

An autonomous, multi-symbol automated trading system (XAUUSD + XAGUSD) that combines deterministic technical analysis with AI decision-making. Powered by **NVIDIA API** for trade confirmation and real-time market data from **MetaTrader 5 webhooks**.

## Features

- **Real-Time Data Pipeline**: Receives market ticks directly from MetaTrader 5 via webhook (not polling) for sub-millisecond latency
- **Hybrid Decision Engine**: 
  - **Deterministic**: 9-factor scoring logic analyzing H4, H1, and M15 timeframes (RSI, MACD, ADX, Stochastic, Bollinger Bands, ATR)
  - **AI Confirmation**: NVIDIA API validates signals with quantitative reasoning
- **Smart Analytics**: Real-time scoring with multi-timeframe confluence detection and regime-aware filtering
- **Self-Improving Memory**: Analyzes past trades in Supabase, detects profit/loss patterns, auto-adjusts strategy weights
- **Adaptive Exit Management**: Momentum-reversal exits, breakeven stops, time-decay closing, trailing stops, and smart position pyramiding
- **Risk & Capital Management**: Dynamic position sizing, correlation filtering, max loss limits, session-aware capital allocation
- **Live Dashboard**: Real-time web UI (Flask, port 8889) for P&L tracking, system health, and AI decision transparency
- **Windows MT5 Bridge**: Runs the bot from macOS/Linux while using the Windows MT5 webhook/WebSocket bridge for broker data and execution

## System Architecture

```
MetaTrader 5 Webhook
    ↓ (real-time ticks)
    ↓
┌─────────────────────────────────────┐
│  continuous_trader.py (24/7 engine)  │
├─────────────────────────────────────┤
│ Core Modules:                       │
│  • analyzer.py (9-factor scoring)   │
│  • ai_client.py (NVIDIA API)        │
│  • bridges/ (MT5 connectors)        │
│  • risk/ (exit, pyramid, scaling)   │
│  • learning/ (self-improvement)     │
└─────────────────────────────────────┘
    ↓ (orders)
    ↓
Windows MT5 Webhook/WebSocket Bridge → MetaTrader 5 Account
```

Read the full architecture overview in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project Structure

```
src/                     - Production code
├── continuous_trader.py - Main 24/7 trading engine
├── core/               - Analysis & AI modules
├── bridges/            - Data sources (webhook, MT5, WebSocket)
├── risk/              - Position & exit management
├── learning/          - Self-improvement engine
├── dashboard/         - Web UI (Flask)
└── bot_mcp/           - MCP server integration

config/                - Configuration
├── scoring_weights.json - Strategy parameters
└── requirements.txt   - Python dependencies

deployment/           - Scripts & operations
├── scripts/          - start_trading_cycle.sh, auto_recovery.sh
└── docs/             - Deployment & operations guides

data/                 - Historical files & results
├── histories/        - Price data
├── backtests/        - Backtest results
└── glm51_reports/    - AI analysis reports

docs/                 - Full documentation
tests/               - Test files
tools/               - Utility scripts
archive/             - Inactive/old files
```

## Quick Start

### 1. Requirements

- **Python 3.9+**
- **NVIDIA API Key** (for trade confirmation)
- **Supabase project** for trade memory, dashboard state, and analytics
- **Windows MT5 webhook/WebSocket bridge** running on ports `5001` and `5002`
- **MetaTrader 5 Webhook Bridge** running on your Windows VM/broker server

### 2. Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env configuration
cp .env.example .env

# Edit .env and add:
# - NVIDIA_API_KEY (required for trade confirmation)
# - WIN_WEBHOOK_URL (Windows webhook HTTP endpoint, usually port 5001)
# - WIN_WS_URL (Windows webhook WebSocket URL, usually port 5002)
```

**Data Sources:**
- **Live Ticks**: MetaTrader 5 webhook (real-time, event-driven)
- **AI Decisions**: NVIDIA API
- **Trade Execution**: Windows MT5 webhook HTTP bridge

For detailed setup, see [docs/SETUP.md](docs/SETUP.md).

### 3. Run the System

The easiest way to run the entire system is using the startup script:

```bash
# Start in paper trading mode (no real orders)
bash deployment/scripts/start_trading_cycle.sh --dry

# Start live trading
bash deployment/scripts/start_trading_cycle.sh

# Check status
bash deployment/scripts/start_trading_cycle.sh --status

# Stop everything
bash deployment/scripts/start_trading_cycle.sh --stop
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

## Running the Bot

### Main Entry Point

```bash
python3 src/continuous_trader.py          # Live trading
python3 src/continuous_trader.py --dry    # Paper trading (simulated orders)
python3 src/continuous_trader.py --close  # Close all positions & exit
```

### Automated Startup (Recommended)

```bash
cd deployment/scripts
bash start_trading_cycle.sh               # Starts bot + dashboard + watchdog
bash start_trading_cycle.sh --status      # Check system status
bash start_trading_cycle.sh --stop        # Stop everything
```

The startup script auto-manages:
- Continuous trader (24/7 engine)
- Dashboard web UI (port 8889)
- Auto-recovery watchdog (restarts if crashes)
- Logging to `logs/system.log`

## AI Integration

**AI Provider**: NVIDIA API
- Validates trade signals with quantitative reasoning
- Cost-effective inference with fast response times

If NVIDIA is unavailable, the system falls back to deterministic indicator logic instead of a second AI provider.

**API Keys Required** in `.env`:
```
NVIDIA_API_KEY=your-api-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
```

## Data Sources

**MetaTrader 5 Webhooks**
- Real-time tick data (event-driven, not polling)
- Executed orders and position updates
- Market opens/closes signaling

**Technical Analysis**
- 9-factor scoring across H4, H1, M15 timeframes
- RSI, MACD, ADX, Stochastic, Bollinger Bands, ATR
- Regime detection (trend/range, volatility)

**Self-Improvement**
- Supabase trade memory database
- Pattern detection on closed trades
- Auto-adjustment of strategy weights
