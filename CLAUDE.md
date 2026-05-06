# MetatradeXM — Developer Context

> **Note**: This file provides immediate context for AI assistants analyzing this repository.

## System Overview

MetatradeXM is a quantitative, autonomous Forex trading bot optimized for precious metals (XAUUSD, XAGUSD). It uses a hybrid deterministic/AI architecture:
1. **Deterministic Scoring**: Collects technical indicators (RSI, MACD, ADX, ATR, etc.) across timeframes (H1, H4, M15) and calculates a 12-factor score.
2. **AI Confirmation**: Feeds the raw data and score to NVIDIA API (cloud inference), asking for a final BUY/SELL/HOLD decision.
3. **Execution**: Uses the MetaApi Cloud SDK (cross-platform) to place trades and modify stops on MT5.
4. **Smart Management**: The `SmartExitManager` and `PositionScaler` dynamically manage open positions (R-based exit thresholds, time decay closing, momentum reversal exits).
5. **Self-Improvement**: `self_improver.py` analyzes past trades stored in Supabase and adjusts `scoring_weights.json` to adapt to changing market regimes (minimum 20 trades/day to avoid overfitting).

## Key Modules

- `start_trading_cycle.sh`: Main entry script. Launches trader, dashboard, and watchdog.
- `continuous_trader.py`: The 24/7 asynchronous trading engine. Manages H4/D1 swing entry signals and position lifecycle.
- `analyzer.py`: The 12-factor scoring logic and technical indicators builder. Computes signals across M15/H1/H4/D1 timeframes.
- `ai_client.py`: Handles NVIDIA API prompts and JSON parsing for trade confirmation. Falls back to Ollama (local) if NVIDIA keys unavailable.
- `smart_exit.py`: R-based adaptive exits with dynamic profit-lock and trailing-stop logic. Scales thresholds to actual position SL distance.
- `position_scaler.py`: Adds volume to winning trades based on AI confirmation (disabled by default).
- `dashboard.py`: Lightweight Flask web UI running on port 8889. Real-time P&L, trades, and metrics.

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
- **H4/D1 Swing Shift (May 2026)**: Reweighted H4+D1 factors (2.0× each) to focus on swing entries. Implemented R-based smart exits with dynamic thresholds. Raised `sl_atr_mult` to 1.5 to avoid intra-candle noise stops. Self-improver now requires 20+ trades/day (up from 3) before adjustments.
- **Exit Logic Consolidation (May 2026)**: Replaced 7 overlapping exit rules with 4 clean ones: catastrophic backstop, time-based close, profit-lock (R-based), trailing stop (ADX-adaptive). Thresholds now scale to position SL distance, adapting automatically to both M15 and H4 entries.
- **Cross-Platform Migration (2026)**: Ported from native `MetaTrader5` (Windows only) to `metaapi-cloud-sdk`, allowing the bot to run natively on macOS/Linux servers.
- **AI Confirmation**: Uses NVIDIA API (T1/T2 keys) for trade confirmation. Falls back to local Ollama (T3) if cloud keys unavailable. Confidence calibration: 70%+ = profitable trades; <70% = filtered out.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
