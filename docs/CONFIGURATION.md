# Configuration Guide

Parameters and configurations are distributed across three primary domains.

## 1. Environment Configurations (`.env`)

These are the primary secrets handling API connection paths.
- `METAAPI_TOKEN`: Your Cloud JWT.
- `METAAPI_ACCOUNT_ID`: The exact MT5 account ID connected in the cloud proxy.
- *(Optional)* `DASH_USER` & `DASH_PASS`: Adds basic auth HTTP protection to the dashboard.

## 2. Core Trading Configurations (`continuous_trader.py`)

The global `CONFIG` dictionary in `continuous_trader.py` controls the pace of execution.

- `monitor_interval_s`: Wait time in seconds between basic position checks (Default: `20`)
- `analysis_interval_s`: Wait time between full AI generation cycles (Default: `60`). Do not drop this too low unless your Ollama instance handles concurrent high traffic well.
- `profit_close_pct`: Account-wide total profit % equivalent before auto-closing all positions. Default `1.5`% (Realistic for continuous daily harvesting).
- `loss_close_pct`: Account-wide total draw-down % before emergency close all. Default `0.5`%.
- `min_confidence`: The floating point baseline for when AI yields a directional signal. Under this baseline, no order is placed. Default `0.60`.
- `max_trades_per_sym`: Limits risk allocation. Default `1`. Pyramiding additional trades should be governed entirely by `position_scaler.py`, not generic re-entries. 

## 3. Quantitative Scoring (`scoring_weights.json`)

Determines what the Analyzer considers "good" vs "bad" technical conditions. This JSON file acts as a stateful manifest and is routinely updated by the Self Improver engine.

- `f1_h4_trend` ... `f9_h1_macd`: Multipliers for underlying conditions.
- `buy_threshold` & `sell_threshold`: The final summative bounds (-27 to +27). An entry is generated if the score crosses these thresholds. Default is `±15`.
- `ranging_penalty`: Flat penalty applied to score logic when ADX dips tracking sideways. Default `0.6`.
