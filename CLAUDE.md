# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MT5 AI Trading Bot — a multi-symbol, multi-timeframe automated trading system for Forex and Commodities. Uses Ollama (localhost:11434) for AI reasoning layered over technical indicators. Runs on Python 3.10+ and supports both Windows-only MetaTrader 5 API and the cross-platform MetaApi.cloud bridge.

## Commands

```bash
# Live trading loop (default — dry run paper trades)
python bot.py run

# One-shot market analysis (no trades)
python bot.py analyze

# Account status and open positions
python bot.py status

# Trade history from SQLite journal
python bot.py history --limit 20 --filter TRADE

# Backtest on last 500 candles (indicator-only)
python bot.py backtest

# Tail bot.log in real time
python bot.py logs --follow

# CLI overrides
python bot.py run --symbol XAUUSD --timeframe H1 --no-ai   # no AI, specific pair
python bot.py run --live                                 # real orders (dangerous)
```

## Architecture

```
bot.py                  ← CLI entry point, main trading loop, market hours gate
├── analyzer.py         ← Indicators (RSI/EMA/MACD/BB/ATR/ADX/Stochastic/Williams%R)
│                        + Ollama AI reasoning (multi-timeframe confluence)
├── risk_manager.py     ← Position sizing, SL/TP, session sizing, trailing SL
├── logger.py           ← SQLite trade journal (trades.db) + rotating bot.log
├── backtester.py       ← Indicator-only walk-forward backtest
├── mt5_bridge.py       ← Windows MT5 API wrapper
├── metaapi_bridge.py   ← Cross-platform MetaApi.cloud (async, threaded event loop)
└── dashboard.py        ← Separate Flask/tkinter dashboard (reads bot_status.json)
```

**Bridge auto-selection**: bot.py checks `METAAPI_TOKEN` + `METAAPI_ACCOUNT_ID`. If set, uses `MetaApiBridge` (Linux/Mac compatible); otherwise falls back to `MT5Bridge` (requires Windows MT5).

**AI endpoint**: Ollama at `http://localhost:11434/api/chat` using model `minimax-m2.7:cloud`. System prompt is embedded in `analyzer.py` `SYSTEM_PROMPT` constant. AI response must be raw JSON — markdown fences are stripped.

## Indicators

All computed in `analyzer.py::_compute_indicators()` using only `pandas` and `numpy` (no TA-lib):
- RSI (14), EMA (20/50/200), MACD (12/26/9), Bollinger Bands (20,2), ATR (14)
- ADX +DI/-DI, Stochastic K/D, Williams %R
- Multi-timeframe confluence scoring: H4 bias (weight 3), H1 context (weight 2), M15 entry (weight 1–2)

## Trading Guards

`bot.py::is_forex_market_open()` gates the loop. Sessions: LONDON (07–13 UTC), LONDON_NY_OVERLAP (13–16), NEW_YORK (16–22), ASIAN (22–07). Market closed Fri 22:00 through Sun 22:00 UTC.

`RiskManager::check_daily_drawdown()` halts trading if equity drops >`max_daily_drawdown_pct` from the UTC-day open balance.

`RiskManager::manage_open_positions()` moves SL to breakeven (+2 pip buffer) when profit reaches `sl_pips`.

## Key Files

- `CONFIG` dict in `bot.py` — primary configuration (symbol, timeframe, SL/TP pips, lot size, risk %, AI toggle, dry run toggle, session size multipliers)
- `trades.db` — SQLite journal of all signals and orders (schema: signals table)
- `bot_status.json` — written each cycle for dashboard consumption
- `candles_cache.json` — sparkline data for dashboard
- `.env` — `ANTHROPIC_API_KEY`, `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID`

## Dependencies

`requirements.txt`: openai>=1.0.0, pandas>=2.0.0, numpy>=1.24.0
MetaApi option: `pip install metaapi-cloud-sdk`
