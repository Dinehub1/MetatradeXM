# API Reference

A brief overview of primary module functions for developers modifying MetatradeXM.

## `continuous_trader.py`
- `main_loop()`: Orchestrates continuous asynchronous behavior.
- `execute_trade()`: Manages position limits, invokes AI validation, parses results, and utilizes bridge.

## `metaapi_bridge.py`
- `place_order(order_dict)`: Accepts an abstracted structure and executes limit/market orders asynchronously to MT5.
- `get_open_positions()`: Fetches cross-account aggregate positions.
- `close_position(ticket)`: Standard forceful liquidation.

## `analyzer.py`
- `analyze_market(symbol, bridge)`: The deterministic workhorse. Builds raw M15, H1, H4 datasets and computes technical features (`rsi`, `macd`, `stoch`, `bb`). Calculates factor points (-3 to +3).

## `ollama_client.py`
- `ask_ollama(prompt, model)`: Sends constructed text buffers to local host loopback instance on port 11434. JSON parses resulting text blocks.

## `smart_exit.py`
- `SmartExitManager.evaluate_exits()`: Primary function hooked in the trader loop. Parses internal states for trailing SL buffers, time decay configurations, and intercepts positions against flipped indicators via AI query prompts.
