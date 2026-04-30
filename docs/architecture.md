# System Architecture

MetatradeXM is a hybrid deterministic/AI automated trading system.

## High-Level Data Flow

```mermaid
graph TD
    MT5([MT5 Terminal]) <--> MetaApi[MetaApi Cloud Bridge]
    MetaApi <--> Trader(continuous_trader.py)
    
    Trader --> Analyzer(analyzer.py)
    Analyzer --> Ollama([Local Ollama AI])
    Ollama --> Trader
    
    Trader --> Exits(smart_exit.py)
    Trader --> Scaler(position_scaler.py)
    
    Trader --> Memory(memory.py SQLite)
    Memory --> Improver(self_improver.py)
    Improver --> Weights[scoring_weights.json]
    Weights --> Analyzer
    
    Trader --> Status[(bot_status.json)]
    Status --> Dashboard(dashboard.py Flask UI)
```

## Core Components

### 1. `continuous_trader.py` (The Engine)
The core asynchronous loop running 24/7. It polls MetaApi every ~60 seconds to check open positions, grabs the latest market ticks/candles, routes them to the analyzer, and executes actual trades based on the analyzer's output. 

### 2. `metaapi_bridge.py` (The Connector)
An abstraction wrapper around the `metaapi-cloud-sdk`. This converts generic bot commands (`place_order`, `get_tick`) into the specialized web-proxy calls required by MetaApi. This replaced `MetaTrader5` windows native SDK.

### 3. `analyzer.py` & `ollama_client.py` (The Brains)
- **`analyzer.py`**: Fetches M15, H1, and H4 candles. Computes RSI, MACD, ADX, Bollinger Bands, Stochastic, and ATR using `pandas_ta`. It scores these into 9 distinct factors.
- **`ollama_client.py`**: Packages the raw indicator data into a prompt and asks local Ollama if the trade makes quantitative sense. Returns a final confidence score.

### 4. `smart_exit.py` & `position_scaler.py` (The Managers)
- **`smart_exit.py`**: Intercepts open positions and intelligently decides if they should be closed early due to time decay or momentum reversal, or if SL should trail the price.
- **`position_scaler.py`**: If a trade is successfully in profit and the current analysis says the trend is continuing, it enters a secondary, smaller position (pyramiding).

### 5. `self_improver.py` & `memory.py` (The Adaptors)
- **`memory.py`**: Logs every closed trade (with JSON contexts of *why* it was opened) into local SQLite.
- **`self_improver.py`**: Runs daily to observe which factors (e.g. `f1_h4_trend`) correlated heavily with Wins, and adjusting `scoring_weights.json` accordingly.

### 6. `dashboard.py` (The Interface)
A lightweight Flask server serving a single responsive HTML page displaying the contents of `bot_status.json` and reading recent lines from `trading.log`.
