# Signal Processing Sequence Diagram

## Complete Trade Flow: TradingView → Broker → Database

```mermaid
sequenceDiagram
    participant TradingView as TradingView<br/>(Chart Alert)
    participant Bot as Flask Bot<br/>(Webhook Server)
    participant Validator as Signal<br/>Validator
    participant RiskMgr as Risk<br/>Manager
    participant OrderMgr as Order<br/>Manager
    participant Broker as Broker API<br/>(MetaTrader5)
    participant Database as SQLite<br/>Database
    participant Logger as Logger<br/>(bot.log)

    TradingView->>Bot: 1. POST /webhook<br/>{symbol, action, qty, SL, TP}
    
    activate Bot
    Bot->>Logger: Webhook received
    
    Note over Bot: 2. Verify HMAC<br/>Signature
    alt Invalid Signature
        Bot-->>TradingView: 401 Unauthorized
        Logger->>Logger: Log security error
        deactivate Bot
    end
    
    Bot->>Validator: 3. Parse Signal
    activate Validator
    
    alt Invalid Format
        Validator-->>Bot: Error
        Bot-->>TradingView: 400 Bad Request
        Logger->>Logger: Log parse error
        deactivate Validator
        deactivate Bot
    else Valid
        Validator->>Validator: Validate fields<br/>Check symbol allowed
        Validator-->>Bot: Parsed signal ✓
        deactivate Validator
    end
    
    Bot->>RiskMgr: 4. Validate Trade<br/>Check limits
    activate RiskMgr
    
    RiskMgr->>RiskMgr: Check daily loss
    RiskMgr->>RiskMgr: Check position count
    RiskMgr->>RiskMgr: Check position size
    RiskMgr->>RiskMgr: Check stop loss set
    
    alt Risk Check Fails
        RiskMgr-->>Bot: REJECTED
        Bot-->>TradingView: 403 Forbidden<br/>(e.g., daily loss hit)
        Logger->>Logger: Log risk rejection
        deactivate RiskMgr
        deactivate Bot
    else Risk Check Passes
        RiskMgr-->>Bot: ALLOWED ✓
        deactivate RiskMgr
    end
    
    Bot->>OrderMgr: 5. Process Signal
    activate OrderMgr
    
    OrderMgr->>OrderMgr: Create Order object<br/>Assign ID
    OrderMgr->>OrderMgr: Set status=PENDING
    
    alt Action = CLOSE
        OrderMgr->>OrderMgr: Find open position
        OrderMgr->>Broker: 6. Close position request
    else Action = BUY/SELL
        OrderMgr->>Broker: 6. Place order<br/>(symbol, qty, SL, TP)
    end
    
    activate Broker
    Broker->>Broker: Process order
    
    alt Broker Error
        Broker-->>OrderMgr: Error response
        OrderMgr->>OrderMgr: Set status=REJECTED
        OrderMgr-->>Bot: Failed
        Bot-->>TradingView: 500 Server Error
        Logger->>Logger: Log broker error
        deactivate Broker
        deactivate OrderMgr
        deactivate Bot
    else Order Placed
        Broker->>Broker: 7. Order Execution<br/>Request sent to market
        Broker-->>OrderMgr: Order ID + Status
        deactivate Broker
        
        OrderMgr->>OrderMgr: Update order with<br/>broker order ID
        OrderMgr->>OrderMgr: Set status=PLACED
        
        OrderMgr->>RiskMgr: 8. Record Position
        activate RiskMgr
        RiskMgr->>RiskMgr: Add to open positions
        RiskMgr->>RiskMgr: Track metrics
        deactivate RiskMgr
        
        OrderMgr-->>Bot: Success ✓
    end
    
    deactivate OrderMgr
    
    Bot->>Database: 9. Save Trade Record
    activate Database
    Database->>Database: INSERT into orders table
    Database-->>Bot: ✓
    deactivate Database
    
    Bot->>Logger: 10. Log All Details
    Logger->>Logger: Write to logs/bot.log
    Logger->>Logger: Include timestamp,<br/>symbol, qty, P&L
    
    Bot-->>TradingView: 11. HTTP 200 OK<br/>{status, trade_id}
    
    deactivate Bot
```

---

## Detailed Step-by-Step Breakdown

### Step 1-2: Webhook Reception & Signature Verification

```
TradingView generates alert
    ↓
Sends POST to: https://your-bot.com/webhook
Body: JSON payload
Headers: X-Webhook-Signature: {HMAC-SHA256}
    ↓
Bot receives request
    ↓
Bot verifies HMAC using WEBHOOK_SECRET
    ├─ If valid: Continue ✓
    └─ If invalid: Return 401, reject
```

### Step 3: Signal Parsing

```
Bot receives JSON:
{
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "stop_loss": 1.0900,
  "take_profit": 1.1050
}
    ↓
Validate structure:
  ✓ Required fields present
  ✓ Symbol in ALLOWED_SYMBOLS list
  ✓ Action is BUY/SELL/CLOSE
  ✓ Prices are valid numbers
    ↓
If valid: Continue
If invalid: Return 400 Bad Request
```

### Step 4: Risk Manager Validation

```
Risk manager checks:
  1. Daily Loss ≤ MAX_DAILY_LOSS? ($500 limit)
     └─ YES: Continue, NO: REJECT
  
  2. Open Positions < MAX_OPEN_POSITIONS? (5 max)
     └─ YES: Continue, NO: REJECT
  
  3. Quantity ≤ MAX_POSITION_SIZE? (1.0 max)
     └─ YES: Continue, NO: REJECT
  
  4. Stop Loss is set (for BUY/SELL)?
     └─ YES: Continue, NO: REJECT
  
  5. Symbol not already open?
     └─ YES: Continue, NO: REJECT

Result: ALLOWED or REJECTED (with reason)
```

### Step 5-6: Order Creation & Placement

```
Order manager creates Order object:
  - order_id: ord_1234567890
  - symbol: EURUSD
  - order_type: BUY
  - quantity: 0.1
  - status: PENDING
    ↓
Sends to broker API:
  place_order(
    symbol="EURUSD",
    qty=0.1,
    stop_loss=1.0900,
    take_profit=1.1050
  )
    ↓
Broker processes order:
  - Validates parameters
  - Routes to market
  - Assigns broker order ID
    ↓
Returns to bot:
  {
    "order_id": "broker_12345",
    "status": "placed",
    "filled_price": 1.0950
  }
    ↓
Bot updates order:
  - status = PLACED
  - broker_order_id = broker_12345
```

### Step 7-8: Position Recording & Risk Update

```
Risk Manager records:
  open_positions["EURUSD"] = {
    "order_id": "ord_1234567890",
    "quantity": 0.1,
    "entry_price": 1.0950,
    "opened_at": 2026-04-18 10:30:05
  }
    ↓
Updates metrics:
  - open_positions count: 1/5
  - daily_loss: $0/$500
  - can_trade: YES
```

### Step 9-11: Persistence & Response

```
Database writes:
  INSERT INTO orders (
    id, symbol, action, quantity,
    status, created_at, broker_order_id
  ) VALUES (...)
    ↓
Logger writes to file:
  2026-04-18 10:30:06 - OrderManager - INFO - 
  Order ord_1234567890 placed: EURUSD BUY 0.1
    ↓
HTTP Response sent to TradingView:
  200 OK
  {
    "status": "success",
    "trade_id": "ord_1234567890",
    "signal_id": "sig_1234567890",
    "symbol": "EURUSD"
  }
```

---

## Error Handling Sequences

### Scenario A: Daily Loss Limit Hit

```
TradingView sends BUY signal
    ↓
Bot receives webhook ✓
    ↓
Signature valid ✓
    ↓
Signal parsed ✓
    ↓
Risk manager checks: daily_loss ($600) >= MAX_DAILY_LOSS ($500)?
    └─ YES! REJECT
    ↓
Bot returns 403 Forbidden
Response: {"error": "Daily loss limit reached"}
    ↓
Logger records:
"Risk check failed: Daily loss limit reached: $600/$500"
    ↓
Trade NOT placed ✓ (Bot protected from further losses)
```

### Scenario B: Broker Connection Error

```
TradingView sends BUY signal
    ↓
All validations pass ✓
    ↓
Order manager calls broker.place_order()
    ↓
Broker API unreachable (connection error)
    ↓
OrderManager catches exception:
  - Sets order.status = ERROR
  - Logs error details
  - Returns 500 Server Error
    ↓
HTTP 500: {"error": "Broker temporarily unavailable"}
    ↓
TradingView receives 500 → Should retry webhook
    ↓
Next attempt: Bot tries again (broker may be back online)
```

### Scenario C: Invalid Symbol

```
TradingView sends signal with symbol "BTCXYZ"
    ↓
Bot receives webhook
    ↓
Signature valid
    ↓
Signal parser checks:
  Is "BTCXYZ" in ALLOWED_SYMBOLS (EURUSD,GBPUSD)?
    └─ NO! Invalid symbol
    ↓
Bot returns 400 Bad Request
Response: {"error": "Symbol BTCXYZ not allowed"}
    ↓
Logger: "Symbol BTCXYZ not in allowed list"
    ↓
Trade rejected (no retry needed)
```

---

## Position Closure Flow

```
Position Open: EURUSD (0.1 lot, entry 1.0950)
    ↓
Later: TradingView sends CLOSE signal
    ↓
Bot receives webhook
    ↓
Signature & parsing: Valid ✓
    ↓
Risk manager: Find open position for EURUSD ✓
    ↓
Order manager calls broker.close_position("EURUSD")
    ↓
Broker closes at market price (exit: 1.1050)
    ↓
Profit/Loss calculated: (1.1050 - 1.0950) * 0.1 = +$100
    ↓
Risk manager:
  - Record loss: (no, profit instead)
  - Remove from open_positions
  - Update metrics
    ↓
Database:
  - INSERT trade record with P&L: +$100
  - Mark as CLOSED
    ↓
Logger:
  "Position closed: EURUSD P&L: +$100"
    ↓
HTTP 200 OK
{
  "status": "success",
  "trade_id": "ord_...",
  "symbol": "EURUSD",
  "pnl": 100.0
}
```

---

## Timing Notes

**Typical latency breakdown:**

| Step | Time |
|------|------|
| TradingView alert trigger → webhook sent | 0-2s |
| Network transit (TradingView → Bot) | 50-200ms |
| Webhook reception & signature verify | 10-50ms |
| Signal parsing & validation | 5-20ms |
| Risk manager checks | 5-10ms |
| Order creation | 5-10ms |
| Broker API call | 100-500ms |
| Database write | 10-50ms |
| Logging | 5-10ms |
| Response sent | 10-50ms |
| **Total** | **~250-1000ms** |

For most scenarios, orders are placed within **500-800ms** of the TradingView alert firing.

---

See [how-it-works.md](how-it-works.md) for more details on each component.
