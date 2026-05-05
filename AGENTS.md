# MetatradeXM — Developer Context

> **Note**: This file provides immediate context for AI assistants analyzing this repository.

## System Overview

MetatradeXM is a quantitative, autonomous Forex trading bot optimized for precious metals (XAUUSD, XAGUSD). It uses a hybrid deterministic/AI architecture:
1. **Deterministic Scoring**: Collects technical indicators (RSI, MACD, ADX, ATR, etc.) across timeframes (H1, H4, M15) and calculates a 9-factor score.
2. **AI Confirmation**: Feeds the raw data and score to the NVIDIA API, asking for a final BUY/SELL/HOLD decision.
3. **Execution**: Uses the Windows MT5 webhook/WebSocket bridge (`5001` HTTP, `5002` WS) to place trades, stream data, and modify stops on MT5.
4. **Smart Management**: The `SmartExitManager` and `PositionScaler` dynamically manage open positions (breakeven stops, time decay closing, momentum reversal exits, pyramiding).
5. **Self-Improvement**: `self_improver.py` analyzes past trades stored in Supabase and adjusts `scoring_weights.json` to adapt to changing market regimes.

## Key Modules

- `start_trading_cycle.sh`: Main entry script. Launches trader, dashboard, and watchdog.
- `continuous_trader.py`: The 24/7 asynchronous trading engine.
- `webhook_bridge.py`: HTTP execution/history/candles bridge to the Windows MT5 webhook service.
- `ws_bridge.py`: WebSocket real-time stream bridge that falls back to the HTTP webhook when needed.
- `analyzer.py`: The 9-factor scoring logic and NVIDIA API prompt builder.
- `ai_client.py`: Handles NVIDIA API calls and JSON parsing.
- `smart_exit.py`: Adaptive exits (trailing stops, partial closes, momentum reversal).
- `position_scaler.py`: Adds volume to winning trades based on AI confirmation.
- `dashboard.py`: Lightweight Flask web UI running on port 8889.

## Common Developer Workflows

### Modifying the Strategy
- **Base Scoring**: Modify `scoring_weights.json` thresholds (`buy_threshold`, `ranging_penalty`, etc.) or individual factor weights.
- **Indicators**: Add new pandas_ta indicators into `analyzer.py`.
- **Exits**: Tune Pips buffers or decay times in `smart_exit.py` `EXIT_CFG`.

### Connecting to a New Broker
Update the `.env` file with `WIN_WEBHOOK_URL`, `WIN_WS_URL`, and `WEBHOOK_AUTH_TOKEN`. The exact symbol naming convention (`broker_symbol`) is defined dynamically in `continuous_trader.py`'s `SYMBOLS` map.

### Live vs Paper Trading
The `continuous_trader.py` accepts a `--dry` flag. When enabled, signals are generated and logged, but no orders are sent to the broker. This flag is configurable via `start_trading_cycle.sh --dry`.

## Recent Architecture Changes
- **Profitability Fixes (April 2026)**: Raised scoring thresholds from ±6 to ±15 to filter noise, implemented `SmartExitManager` to prevent massive drawdown, reduced max correlated positions from 3 to 1.
- **Windows Bridge Migration**: Uses a Windows MT5 webhook/WebSocket bridge for broker connectivity while the bot runs on macOS/Linux.
- **AI Backend Migration**: Uses NVIDIA API as the trading decision AI backend.

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
