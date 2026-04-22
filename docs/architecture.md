# System Architecture

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SYSTEMS                         │
├─────────────────────────────────────────────────────────────────┤
│  TradingView              Broker API           Monitoring        │
│  (Signals)                (Execution)          (Alerts/Dashboard)│
│     │                         │                       │          │
│     └─────────────┬───────────┴───────────────────────┘          │
│                   │ HTTP(S)                                       │
└───────────────────┼─────────────────────────────────────────────┘
                    │
┌───────────────────┼─────────────────────────────────────────────┐
│                   │ TRADING BOT (Python)                        │
├───────────────────┼─────────────────────────────────────────────┤
│                   ▼                                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         WEBHOOK LISTENER (Flask)                        │  │
│  │  ├─ POST /webhook                                       │  │
│  │  ├─ Signature Verification (HMAC)                       │  │
│  │  ├─ Signal Parsing & Validation                         │  │
│  │  └─ GET /health                                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         ORDER MANAGER                                    │  │
│  │  ├─ Order Lifecycle Management                           │  │
│  │  ├─ Order Status Tracking                                │  │
│  │  ├─ Open Position Registry                               │  │
│  │  └─ Trade History                                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│           │              ▲                                      │
│           ├──────────────┤                                      │
│           ▼              │                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         RISK MANAGER                                     │  │
│  │  ├─ Position Limit Checks                                │  │
│  │  ├─ Daily Loss Tracking                                  │  │
│  │  ├─ Position Size Validation                             │  │
│  │  └─ P&L Calculation                                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │       BROKER CONNECTOR (Abstract Interface)              │  │
│  │  ├─ place_order()                                        │  │
│  │  ├─ close_position()                                     │  │
│  │  ├─ get_open_orders()                                    │  │
│  │  ├─ get_account_balance()                                │  │
│  │  └─ health_check()                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│           │                                                     │
│  ┌────────┴──────────────────────────────────────────────────┐ │
│  │       BROKER IMPLEMENTATIONS                             │ │
│  │  ├─ MetaTrader5 API                                      │ │
│  │  ├─ Binance API                                          │ │
│  │  └─ [Future: Interactive Brokers, etc.]                  │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         LOGGING & PERSISTENCE                            │  │
│  │  ├─ Logger (rotating file + console)                     │  │
│  │  ├─ Trade Database (SQLite)                              │  │
│  │  └─ Config Management                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Request Path: Signal → Execution

```
1. External: TradingView sends webhook
   POST /webhook
   {
     "symbol": "EURUSD",
     "action": "BUY",
     ...
   }

2. WebhookListener._verify_signature()
   └─ Check HMAC-SHA256 signature

3. WebhookListener._parse_signal()
   └─ Validate fields, format data

4. OrderManager.process_signal()
   ├─ Validate symbol allowed
   └─ Route to _open_position() or _close_position()

5. RiskManager.validate_trade()
   ├─ Check daily loss
   ├─ Check position count
   ├─ Check position size
   └─ Return: {allowed, reason}

6. Order object created
   └─ Status = PENDING

7. BrokerConnector.place_order()
   ├─ Call broker API
   ├─ Receive order ID
   └─ Status = PLACED

8. RiskManager.record_position()
   └─ Track open position

9. HTTP Response 200
   {
     "status": "success",
     "trade_id": "ord_123...",
     "symbol": "EURUSD"
   }

10. Logger writes to file
    └─ logs/bot.log
```

---

## Configuration & Environment

### Config Hierarchy

```
1. Environment Variables (.env file)
   └─ Loaded by Config class

2. Defaults in config.py
   └─ Fallback values

3. Database
   └─ Persistence layer
   
4. Runtime Parameters
   └─ In-memory state
```

### Sensitive Data Handling

```
API Keys (in .env):
  - API_KEY
  - API_SECRET
  - WEBHOOK_SECRET

Never:
  ✗ Commit .env to git
  ✗ Log API keys
  ✗ Pass keys in URLs
  ✗ Expose in error messages

Always:
  ✓ Use environment variables
  ✓ Rotate keys periodically
  ✓ Use DEMO_MODE=true in development
```

---

## Technology Stack

### Core Framework
- **Flask** 2.3.2 - Web server for webhook receiving
- **Python** 3.8+ - Language

### Broker Integrations
- **MetaTrader5** - Forex/CFD trading
- **python-binance** - Cryptocurrency trading
- **requests** - HTTP client for APIs

### Data Management
- **SQLAlchemy** 2.0 - ORM for trade database
- **SQLite3** - Local database (can upgrade to PostgreSQL)
- **pandas** - Data analysis (optional, for reporting)

### Quality & Testing
- **pytest** - Unit testing
- **black** - Code formatting
- **flake8** - Linting
- **mypy** - Type checking

### Production Deployment
- **gunicorn** - WSGI application server (for production)
- **python-dotenv** - Environment configuration

### Architecture Rationale

| Choice | Why |
|--------|-----|
| Flask | Lightweight, low-latency webhook handling |
| SQLite | Simple, file-based, no external DB needed initially |
| Python | Rich trading libraries, rapid development |
| Async/Threading | Flask handles concurrent webhook requests |
| Config from .env | Standard 12-factor app approach |

---

## Module Organization

```
trading-bot/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point, bot initialization
│   ├── config.py               # Configuration management
│   ├── logger.py               # Logging setup
│   ├── websocket_listener.py   # Flask webhook server
│   ├── order_manager.py        # Order lifecycle
│   ├── risk_manager.py         # Risk controls
│   └── broker_connector.py     # Broker API abstraction
│
├── config/
│   └── (configuration files)
│
├── logs/
│   ├── bot.log                 # Main log file
│   └── trades.db               # Trade database
│
├── docs/
│   ├── overview.md
│   ├── how-it-works.md
│   ├── architecture.md
│   ├── setup-guide.md
│   └── tradingview-webhook-spec.md
│
├── tests/
│   ├── test_websocket_listener.py
│   ├── test_order_manager.py
│   ├── test_risk_manager.py
│   └── test_integration.py
│
├── utils/
│   └── (helper scripts)
│
├── .env.example               # Example config
├── requirements.txt           # Python dependencies
└── README.md
```

---

## Error Handling Architecture

### Error Levels

```
Level 1: Validation Errors (400)
├─ Bad request format
├─ Missing required fields
├─ Invalid action type
└─ Recovery: Reject, don't retry

Level 2: Business Logic Errors (403)
├─ Daily loss limit hit
├─ Position limit exceeded
├─ Symbol not allowed
└─ Recovery: Reject, log alert

Level 3: System Errors (500)
├─ Broker connection failed
├─ Database error
├─ Unexpected exception
└─ Recovery: Retry with backoff, alert ops team

Level 4: Critical Errors
├─ Config validation failed
├─ Broker permanently offline
└─ Recovery: Graceful shutdown
```

### Exception Handling Strategy

```python
# Level 1-2: Catch and return HTTP error
try:
    signal = parse_signal(payload)
    if not signal:
        return {"error": "Invalid signal"}, 400
except ValueError:
    return {"error": "Malformed JSON"}, 400

# Level 3: Catch and retry
try:
    result = broker.place_order(...)
except ConnectionError:
    for attempt in range(3):
        result = broker.place_order(...)
        if result["success"]:
            return result
        sleep(2 ** attempt)
    return {"error": "Broker unreachable"}, 500

# Level 4: Catch and alert
except Exception as e:
    logger.critical(f"Unrecoverable error: {e}")
    alert_operations_team()
    raise SystemExit(1)
```

---

## Security Architecture

### Webhook Validation

```
1. Network Level
   └─ HTTPS only (enforce in production)

2. Signature Level
   ├─ X-Webhook-Signature header
   ├─ HMAC-SHA256 using WEBHOOK_SECRET
   └─ Constant-time comparison to prevent timing attacks

3. Content Level
   ├─ JSON schema validation
   ├─ Type checking (symbol, action, prices)
   ├─ Symbol whitelist (ALLOWED_SYMBOLS)
   └─ Price sanity checks

4. Application Level
   ├─ Position validation by risk manager
   ├─ Order routing to authorized accounts
   └─ Audit logging of all actions
```

### API Key Security

```
Storage:
  ✓ .env file (local development)
  ✓ Environment variables (production)
  ✓ Secrets manager (AWS Secrets Manager, etc.)

Never:
  ✗ Hardcoded in source code
  ✗ Logged to files
  ✗ Displayed in error messages
  ✗ Sent in URLs

Rotation:
  - Regenerate monthly
  - Keep old key for 24 hours during rotation
  - Update all .env files
```

---

## Performance & Scalability

### Current Design (Single Instance)

```
Capacity:
  - ~100 webhooks/second (limited by broker API)
  - 1 CPU core sufficient
  - Minimal memory usage

Bottlenecks:
  - Broker API rate limits (usually 100-1000 req/min)
  - Network latency (100-500ms per trade)
  - Database write speed (SQLite ~1000 writes/sec)

Optimization:
  - Queue webhook processing (Celery/Redis)
  - Async broker calls
  - Database indexing on symbol + timestamp
```

### Future Scaling

```
For 1000+ concurrent positions:
  1. Use PostgreSQL instead of SQLite
  2. Add async task queue (Celery + Redis)
  3. Horizontal scaling with load balancer
  4. Separate read/write database replicas
  5. Caching layer for lookups
```

---

## Deployment Architecture

### Development
```
Local machine:
  - Python virtualenv
  - Flask dev server (debug mode)
  - DEMO_MODE=true
  - SQLite database
```

### Production
```
Linux server (VPS/Cloud):
  - Python with virtualenv
  - gunicorn WSGI server
  - systemd service
  - PostgreSQL (optional)
  - nginx reverse proxy (optional)
  - Monitoring (Prometheus/Grafana)
  - Log aggregation (ELK Stack)
```

### Example Production Setup
```
nginx (Reverse Proxy)
    ↓
gunicorn (WSGI Server, 4 workers)
    ↓
Flask App
    ├─ Order Manager
    ├─ Risk Manager
    └─ Broker Connector
    ↓
PostgreSQL / SQLite
```

---

## Monitoring & Observability

### Metrics to Track

```
Webhook Metrics:
  - Requests/second
  - Signature verification failures
  - Parse errors
  - Response time

Trade Metrics:
  - Orders placed/rejected
  - Fill rate (%)
  - Average fill time
  - Win/loss ratio

Risk Metrics:
  - Daily loss vs limit
  - Position count vs limit
  - Largest position
  - Drawdown

System Metrics:
  - Uptime
  - CPU usage
  - Memory usage
  - Database size
```

### Health Checks

```
Endpoint: GET /health
Interval: Every 30 seconds (external monitoring)

Response:
  {
    "status": "healthy",
    "timestamp": "2026-04-18T10:35:00Z",
    "broker": "connected",
    "last_trade": "2026-04-18T10:32:15Z"
  }
```

---

See **setup-guide.md** for deployment instructions.
