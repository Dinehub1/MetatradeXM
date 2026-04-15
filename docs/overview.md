# MetatradeXM Trading Bot - Overview

MetatradeXM is an autonomous Forex trading bot optimized for precious metals (XAUUSD, XAGUSD). It combines deterministic technical scoring with AI confirmation to generate trade signals, and uses smart exit and position scaling strategies to manage risk and maximize profits.

## Key Features

- **Hybrid Deterministic/AI Architecture**
  - Deterministic scoring based on 9 technical factors (RSI, MACD, ADX, ATR, etc.) across multiple timeframes.
  - AI confirmation via a local Ollama model (default: minimax-m2.7:cloud) for final BUY/SELL/HOLD decision.
- **Smart Exit Management**
  - Adaptive stop-loss and take-profit logic including breakeven moves, trailing stops, momentum-reversal detection, and time-based decay.
- **Position Scaling (Pyramiding)**
  - Adds to winning positions when AI confirms trend continuation, subject to profit and risk constraints.
- **Symbol Validation**
  - Prevents trades with invalid or UNKNOWN symbols to avoid catastrophic losses.
- **Risk Controls**
  - Maximum total positions, per‑symbol limits, margin‑based risk checks, and configurable loss cut thresholds.
- **Self‑Improvement**
  - Ongoing analysis of past trades to adjust scoring weights and improve performance over time.
- **Cross‑Platform**
  - Uses MetaApi Cloud SDK, allowing the bot to run on Linux/macOS (no Windows‑only MetaTrader5 dependency).
- **Dashboard**
  - Lightweight Flask UI on port 8889 showing status, balances, open positions, and recent signals.

## Repository Structure

```
trading-bot/
├── continuous_trader.py      # Main 24/7 trading engine
├── metaapi_bridge.py         # Wrapper for MetaApi Cloud SDK
├── analyzer.py               # Technical indicator calculation & scoring
├── smart_exit.py             # Smart exit manager (breakeven, trailing, reversal, time decay)
├── position_scaler.py        # Position scaling (pyramiding) logic
├── risk_manager.py           # Risk & margin checking
├── capital_manager.py        # Capital & lot sizing management
├── memory.py                 # Persistent trade memory (SQLite)
├── self_improver.py          # Post‑trade analysis and weight adjustment
├── mcp_server.py             # MCP (Model Context Protocol) server for external tool integration
├── dashboard.py              # Flask web UI
├── skills/                   # Skill files (filters, boosters, scalers, etc.) used by the bot
├── docs/                     # Documentation (this folder)
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (MetaApi token, account ID, etc.)
└── README.md                 # This file
```

## Getting Started

1. Clone the repository and install dependencies:
   ```bash
   git clone <repo-url>
   cd trading-bot
   pip install -r requirements.txt
   ```
2. Set up your MetaApi credentials in `.env`:
   ```dotenv
   METAAPI_TOKEN=your_token_here
   METAAPI_ACCOUNT_ID=your_account_id_here
   ```
3. (Optional) Adjust configuration in `continuous_trader.py` (`CONFIG` dict) or skill parameters as needed.
4. Start the bot:
   ```bash
   ./start_trading_cycle.sh          # Live trading
   # or
   ./start_trading_cycle.sh --dry    # Paper‑trade only
   ```
5. View the dashboard at `http://<host>:8889`.

## Configuration

Main tweakable parameters are located in:

- `continuous_trader.py` – `CONFIG` dictionary (monitor intervals, max positions, confidence thresholds, profit/loss close percentages, etc.).
- `scoring_weights.json` – Deterministic factor weights and entry thresholds (`buy_threshold`, `sell_threshold`).
- `smart_exit.py` – `EXIT_CFG` dict (loss cut pips, winner protection levels, trailing start/distance, time decay settings, etc.).
- `position_scaler.py` – `SCALE_CFG` dict (minimum profit to scale, confidence threshold, max scales per trade, lot fraction, etc.).
- Individual skill files in `skills/` – each skill has its own YAML frontmatter defining its behaviour.

For detailed explanations, see the specific documentation files in this `docs/` folder.

---
*Document last updated: $(date)*