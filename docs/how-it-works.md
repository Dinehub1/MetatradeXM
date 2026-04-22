# How It Works - Detailed Flow

## Signal Processing Flow

### Complete Trade Lifecycle

```
1. TradingView Alert Triggered
   ↓
2. Webhook HTTP POST to /webhook
   ↓
3. Flask Receives Request
   ↓
4. HMAC Signature Verification
   ├─ Valid? Continue
   └─ Invalid? Reject (401)
   ↓
5. Parse JSON Payload
   ├─ Validate Required Fields
   ├─ Check Symbol is Allowed
   └─ Validate Action (BUY/SELL/CLOSE)
   ↓
6. Risk Manager Validation
   ├─ Check Daily Loss Limit
   ├─ Check Position Count Limit
   ├─ Check Position Size Limit
   ├─ Check Stop Loss is Set
   └─ Return: ALLOWED or REJECTED
   ↓
7. Order Manager Creates Order
   ├─ Assign Order ID
   ├─ Set Status = PENDING
   └─ Store in Memory
   ↓
8. Send to Broker API
   ├─ Place Order Request
   ├─ Receive Order ID from Broker
   └─ Set Status = PLACED
   ↓
9. Return Success Response
   ├─ HTTP 200 OK
   ├─ Include Trade ID
   └─ Include Order Status
   ↓
10. Log Transaction
    ├─ Write to logs/bot.log
    ├─ Record in Database
    └─ Update Risk Metrics
```

---

## Detailed Step Breakdown

### Step 1-2: TradingView to Webhook

**What Happens:**
- TradingView alert fires based on your strategy
- Webhook URL called with POST request
- JSON payload sent in request body

**Example Alert Configuration in TradingView:**
```
Alert Name: "Buy EUR/USD"
Webhook URL: https://your-bot-server.com/webhook
Message: 
{
  "id": "{{timenow}}",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": {{close}},
  "stop_loss": {{close}} - 0.005,
  "take_profit": {{close}} + 0.010
}
```

### Step 3-4: Receive and Verify

**Code in `websocket_listener.py`:**
```python
@app.route("/webhook", methods=["POST"])
def webhook():
    # Verify HMAC signature
    if not _verify_signature(request):
        return {"error": "Unauthorized"}, 401
    
    payload = request.get_json()
    # ... continue processing
```

**Security Check:**
- X-Webhook-Signature header checked against secret key
- Prevents spoofed requests
- Uses HMAC-SHA256

### Step 5: Signal Parsing

**Validation Checks:**
```python
required_fields = ["symbol", "action"]
✓ All required fields present?
✓ Symbol in ALLOWED_SYMBOLS?
✓ Action in ["BUY", "SELL", "CLOSE"]?
✓ Quantity > 0?
✓ Prices are valid numbers?
```

**Example Validation:**
```python
signal = {
    "symbol": "EURUSD",        # ✓ Required, ✓ Allowed
    "action": "BUY",           # ✓ Required, ✓ Valid
    "quantity": 0.1,           # ✓ Valid number
    "stop_loss": 1.0900,       # ✓ Valid price
    "take_profit": 1.1050      # ✓ Valid price
}
```

### Step 6: Risk Manager Checks

**Validation Rules:**

```python
# Check 1: Daily Loss Limit
if daily_loss >= MAX_DAILY_LOSS:
    REJECT("Daily loss limit reached")

# Check 2: Position Count
if len(open_positions) >= MAX_OPEN_POSITIONS:
    REJECT("Max positions reached")

# Check 3: Position Size
if quantity > MAX_POSITION_SIZE:
    REJECT("Position too large")

# Check 4: Stop Loss Required
if order_type in ["BUY", "SELL"] and stop_loss == 0:
    REJECT("Stop loss required")

# Check 5: Duplicate Position
if symbol in open_positions:
    REJECT("Position already open")
```

**Risk Config (from `.env`):**
```
MAX_DAILY_LOSS=500.0           # Stop trading after $500 loss
MAX_OPEN_POSITIONS=5            # Never more than 5 trades
MAX_POSITION_SIZE=1.0           # Each trade max 1.0 lot
DEFAULT_LOT_SIZE=0.1            # Normal trade size
```

### Step 7-8: Order Placement

**Order Object Created:**
```python
order = Order(
    symbol="EURUSD",
    order_type="BUY",
    quantity=0.1,
    price=1.0950,
    stop_loss=1.0900,
    take_profit=1.1050,
    signal_id="sig_1234567890"
)

# Status transitions:
order.status = PENDING  # Created, not yet sent
order.status = PLACED   # Sent to broker
order.status = FILLED   # Broker confirmed
```

**Broker API Call:**
```python
result = broker.place_order(
    symbol="EURUSD",
    order_type="BUY",
    quantity=0.1,
    stop_loss=1.0900,
    take_profit=1.1050
)

# Broker returns:
{
    "success": True,
    "order_id": "broker_12345",
    "filled_price": 1.0950,
    "filled_time": "2026-04-18T10:30:05Z"
}
```

### Step 9-10: Response & Logging

**HTTP Response:**
```json
{
  "status": "success",
  "trade_id": "ord_1234567890",
  "signal_id": "sig_1234567890",
  "symbol": "EURUSD",
  "quantity": 0.1
}
```

**Log Entry:**
```
2026-04-18 10:30:05 - OrderManager - INFO - Order ord_1234567890 placed successfully
2026-04-18 10:30:05 - RiskManager - INFO - Position opened: EURUSD x0.1 @ 1.0950
2026-04-18 10:30:05 - WebhookListener - INFO - Received webhook for EURUSD BUY
```

---

## Order Types Supported

### 1. Market Buy/Sell
```json
{
  "symbol": "EURUSD",
  "action": "BUY",
  "entry_price": 0,  // 0 = market order
  "stop_loss": 1.0900,
  "take_profit": 1.1050
}
```
→ Executed immediately at current market price

### 2. Limit Orders
```json
{
  "symbol": "EURUSD",
  "action": "BUY",
  "entry_price": 1.0950,  // Specific price
  "stop_loss": 1.0900,
  "take_profit": 1.1050
}
```
→ Placed at specified price, filled when market reaches it

### 3. Position Close
```json
{
  "symbol": "EURUSD",
  "action": "CLOSE"
}
```
→ Closes entire position for EURUSD at market

---

## Error Handling & Fallback Logic

### Webhook Reception Errors

| Error | Cause | Action |
|-------|-------|--------|
| 401 Unauthorized | Invalid signature | Alert + Log, reject request |
| 400 Bad Request | Missing/invalid fields | Reject + error response |
| 500 Server Error | Broker connection down | Retry with exponential backoff |

### Risk Manager Rejections

```
Daily Loss Limit Hit
├─ Log: "Daily loss limit reached: $500/$500"
├─ Action: Do not place order
├─ Alert: Send to monitoring dashboard
└─ Recovery: Automatic at next day's market open

Position Limit Hit
├─ Log: "Max open positions reached: 5/5"
├─ Action: Do not place order
└─ Recovery: Close an existing position first

Invalid Symbol
├─ Log: "Symbol BTC not in allowed list"
├─ Action: Reject with 400 error
└─ Recovery: Update ALLOWED_SYMBOLS in .env
```

### Broker Errors

```python
try:
    result = broker.place_order(...)
except BrokerConnectionError:
    # Retry up to 3 times
    for attempt in range(3):
        result = broker.place_order(...)
        if result["success"]:
            break
        time.sleep(2 ** attempt)  # Exponential backoff
    
    if not result["success"]:
        LOG ERROR
        ALERT operations team
        Return 500 to TradingView (retry)

except InvalidSymbolError:
    # Broker doesn't support this symbol
    LOG WARNING
    Return 400 to TradingView (don't retry)
```

---

## Position Lifecycle

### Example: 1-Hour EUR/USD Trade

```
Time    Action              Status          P&L
------  ----------------    -----          -----
10:30   BUY 0.1 EURUSD      PLACED          -
10:31   Order Filled        FILLED          -
        Position: LONG      
        Entry: 1.0950       
        
10:45   (mid-trade)         OPEN            +$50
                            SL: 1.0900      
                            TP: 1.1050      
        
11:30   CLOSE Signal        CLOSING         +$100
        Exit @ 1.1050       
        
11:31   Position Closed     CLOSED          +$100
        P&L: +$100          
        Risk Manager Updated
```

**Key Events Logged:**
1. Signal received
2. Risk checks passed
3. Order placed
4. Order filled (price, quantity)
5. Position opened
6. Close signal received
7. Position closed (exit price, P&L)
8. Risk metrics updated

---

## Monitoring & Debugging

### View Live Logs
```bash
tail -f logs/bot.log
```

### Check Order History
```bash
sqlite3 logs/trades.db "SELECT * FROM orders ORDER BY created_at DESC LIMIT 10;"
```

### Health Check
```bash
curl http://localhost:5000/health
# {
#   "status": "healthy",
#   "timestamp": "2026-04-18T10:35:00Z"
# }
```

### Risk Summary
Check log output for:
```
Daily Loss: $250/$500
Open Positions: 2/5
Can Trade: Yes
```

---

See **architecture.md** for system design details.
