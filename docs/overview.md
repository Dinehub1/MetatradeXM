# Trading Bot - Overview

## What This Bot Does

This is a **production-ready automated trading bot** that:
- Receives **real-time trading signals** from TradingView via webhook
- Validates signals against **risk management rules**
- Executes trades on supported **brokers/exchanges** automatically
- Monitors positions and closes them based on profit/loss targets
- Logs all trades and provides **audit trails**

The bot is designed to work **24/5** (or 24/7 depending on your broker) with minimal human intervention.

---

## High-Level Architecture

```
TradingView Alert
        ↓
   Webhook Server (Flask)
        ↓
   Signal Parser & Validator
        ↓
   Risk Manager (Position & Loss Checks)
        ↓
   Order Manager (Order Placement)
        ↓
   Broker API (MetaTrader5, Binance, etc.)
        ↓
   Trade Execution
        ↓
   Logging & Monitoring
```

### Component Overview

| Component | Purpose |
|-----------|---------|
| **WebSocket Listener** | Receives HTTP POST webhook alerts from TradingView |
| **Signal Parser** | Validates and parses incoming alerts |
| **Risk Manager** | Enforces position limits, daily loss limits, and position sizing |
| **Order Manager** | Handles order lifecycle (creation, placement, cancellation) |
| **Broker Connector** | Abstracts communication with different brokers |
| **Logger** | Records all actions for auditing and debugging |

---

## TradingView Webhook Format

The bot expects alerts in the following JSON format:

```json
{
  "id": "unique-signal-id-123",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": 1.0950,
  "stop_loss": 1.0900,
  "take_profit": 1.1050,
  "timestamp": "2026-04-18T10:30:00Z"
}
```

### Field Descriptions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | No | Unique signal identifier; auto-generated if missing |
| `symbol` | string | **Yes** | Trading pair (must be in `ALLOWED_SYMBOLS`) |
| `action` | string | **Yes** | `BUY`, `SELL`, or `CLOSE` |
| `quantity` | float | No | Position size (defaults to `DEFAULT_LOT_SIZE`) |
| `entry_price` | float | No | Order price (0 = market order) |
| `stop_loss` | float | No | Stop loss level |
| `take_profit` | float | No | Take profit level |
| `timestamp` | string | No | ISO 8601 timestamp |

---

## Supported Exchanges & Brokers

| Broker | Status | Notes |
|--------|--------|-------|
| **MetaTrader5** | ✓ Implemented | Forex, CFDs, Stocks |
| **Binance** | ⚠️ Partial | Cryptocurrency |
| **Interactive Brokers** | ⚠️ Planned | Stocks, Options, Forex |
| **Alpaca** | ⚠️ Planned | US Stocks, Options |

To add support for a new broker:
1. Extend `BrokerConnector` in `src/broker_connector.py`
2. Implement broker-specific API calls
3. Add broker type to config

---

## Key Security Features

- **HMAC Signature Verification**: Webhooks are signed with `WEBHOOK_SECRET`
- **Position Validation**: All trades validated against risk rules
- **API Key Security**: Keys stored in `.env` (never in code)
- **Audit Logging**: All trades logged with timestamps
- **Demo Mode**: Default safe mode for testing

---

## Signal Handling Examples

### Example 1: Long Entry
```json
{
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": 0,
  "stop_loss": 1.0900,
  "take_profit": 1.1050
}
```
→ Opens a 0.1 lot long position at market price with SL/TP

### Example 2: Exit Signal
```json
{
  "symbol": "EURUSD",
  "action": "CLOSE"
}
```
→ Closes any open EURUSD position

### Example 3: Limit Order
```json
{
  "symbol": "GBPUSD",
  "action": "SELL",
  "quantity": 0.2,
  "entry_price": 1.2750,
  "stop_loss": 1.2850,
  "take_profit": 1.2550
}
```
→ Places a sell limit order at 1.2750

---

## Configuration & Deployment

See **setup-guide.md** for step-by-step instructions.

---

## Monitoring & Status

Check bot health:
```bash
curl http://localhost:5000/health
```

Example response:
```json
{
  "status": "healthy",
  "timestamp": "2026-04-18T10:35:22.123456"
}
```

Get current risk summary:
```bash
# Via API endpoint (requires admin endpoint)
# Check logs/bot.log for detailed info
tail -f logs/bot.log
```

---

## Common Workflows

### Scenario 1: Long Trade Entry & Exit
1. TradingView chart shows buy signal
2. Alert sent: `{symbol: "EURUSD", action: "BUY", ...}`
3. Bot receives webhook
4. Risk manager validates (position limit OK, daily loss OK)
5. Order placed at broker
6. Later, exit signal sent: `{symbol: "EURUSD", action: "CLOSE"}`
7. Bot closes position, calculates P&L
8. Result logged

### Scenario 2: Risk Limit Hit
1. Bot has already lost $500 today (daily limit)
2. New signal arrives
3. Risk manager blocks trade
4. Alert logged: "Daily loss limit reached"
5. Bot will not trade until daily reset (next day at midnight)

---

## Next Steps

- **Setup**: Follow [setup-guide.md](setup-guide.md)
- **Architecture Details**: See [architecture.md](architecture.md)
- **Webhook Configuration**: See [tradingview-webhook-spec.md](tradingview-webhook-spec.md)
- **Trade Flow**: See [how-it-works.md](how-it-works.md)
