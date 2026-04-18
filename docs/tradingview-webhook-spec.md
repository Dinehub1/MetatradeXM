# TradingView Webhook Specification

## Quick Setup

**Webhook URL:** `https://your-bot-domain.com/webhook`

**Test URL:** `https://your-bot-domain.com/health`

**Method:** POST

**Signature Header:** `X-Webhook-Signature` (HMAC-SHA256)

---

## JSON Payload Format

### Complete Schema

```json
{
  "id": "unique-signal-id",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": 1.0950,
  "stop_loss": 1.0900,
  "take_profit": 1.1050,
  "timestamp": "2026-04-18T10:30:00Z"
}
```

### Field Reference

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `id` | string | No | Unique signal ID; auto-generated if omitted | `sig_1234567890` |
| `symbol` | string | **Yes** | Trading pair (must match ALLOWED_SYMBOLS) | `EURUSD` |
| `action` | string | **Yes** | Trade action: BUY, SELL, or CLOSE | `BUY` |
| `quantity` | float | No | Position size in lots | `0.1` |
| `entry_price` | float | No | Entry price (0 = market order) | `1.0950` |
| `stop_loss` | float | No | Stop loss price | `1.0900` |
| `take_profit` | float | No | Take profit price | `1.1050` |
| `timestamp` | string | No | ISO 8601 timestamp | `2026-04-18T10:30:00Z` |

---

## Example Payloads

### Example 1: Market Buy Order

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

**What happens:**
- Buys 0.1 lots EURUSD at market price
- Sets stop loss at 1.0900
- Sets take profit at 1.1050

---

### Example 2: Limit Sell Order

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

**What happens:**
- Places sell order at limit price 1.2750
- When filled, sets stop loss at 1.2850
- Sets take profit at 1.2550

---

### Example 3: Close Position

```json
{
  "symbol": "EURUSD",
  "action": "CLOSE"
}
```

**What happens:**
- Closes all open EURUSD positions
- Exits at market price
- Calculates and logs P&L

---

### Example 4: Minimal Signal (Uses Defaults)

```json
{
  "symbol": "USDJPY",
  "action": "BUY"
}
```

**What happens:**
- Uses DEFAULT_LOT_SIZE from config
- Entry price = 0 (market order)
- Stop loss/take profit must be set by bot or manual override

---

## TradingView Alert Setup

### Step 1: Create Alert in TradingView

1. Open your strategy chart in TradingView
2. Click **Alert** (bell icon in top-right)
3. Click **Create Alert**
4. Configure:
   - **Alert Name:** e.g., "Buy EUR/USD"
   - **Condition:** Your strategy condition
   - **Show notification:** Yes
   - **Send email:** Optional

### Step 2: Set Webhook URL

In the alert settings:

1. Scroll to **Webhook URL** section
2. Paste: `https://your-bot-domain.com/webhook`
3. Click **Add webhook URL**

**Testing:**
```bash
# Test the URL from your terminal
curl -X POST https://your-bot-domain.com/health -H "Content-Type: application/json"

# Should respond with 200 OK:
# {"status": "healthy", "timestamp": "..."}
```

### Step 3: Set Message Content

In the **Message** field, paste:

```json
{
  "id": "{{timenow}}",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": {{close}},
  "stop_loss": {{close}} - 0.0050,
  "take_profit": {{close}} + 0.0100,
  "timestamp": "{{timenow}}"
}
```

**TradingView Variables You Can Use:**

| Variable | Description | Example |
|----------|-------------|---------|
| `{{open}}` | Candle open price | `1.0950` |
| `{{high}}` | Candle high price | `1.0960` |
| `{{low}}` | Candle low price | `1.0940` |
| `{{close}}` | Candle close price | `1.0955` |
| `{{time}}` | Candle time | `2026-04-18 10:30` |
| `{{timenow}}` | Current time | `2026-04-18 10:32:45` |
| `{{interval}}` | Chart interval | `5m`, `1h` |
| `{{strategy.position_size}}` | Position size from strategy | `0.1` |

### Step 4: Example Alert Message

For a strategy that buys on RSI < 30:

```json
{
  "id": "signal_{{timenow}}",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": {{close}},
  "stop_loss": {{low}} - 0.0020,
  "take_profit": {{close}} + 0.0100,
  "timestamp": "{{timenow}}"
}
```

When RSI < 30:
```json
{
  "id": "signal_2026-04-18 10:30:45",
  "symbol": "EURUSD",
  "action": "BUY",
  "quantity": 0.1,
  "entry_price": 1.0955,
  "stop_loss": 1.0935,
  "take_profit": 1.1055,
  "timestamp": "2026-04-18 10:30:45"
}
```

---

## Complete TradingView Strategy Example

### Pine Script Code

```pinescript
//@version=5
strategy("Bot Trading Strategy", overlay=true, default_qty_type=strategy.fixed, default_qty_value=0.1)

// RSI Indicator
rsi = ta.rsi(close, 14)

// Trading Logic
long_condition = rsi < 30
short_condition = rsi > 70
exit_condition = rsi == 50

// Webhook messages
if long_condition
    strategy.entry("Long", strategy.long)
    alert(
        json.stringify(
            {
                id: str.tostring(timenow),
                symbol: "EURUSD",
                action: "BUY",
                quantity: 0.1,
                entry_price: close,
                stop_loss: close - 0.005,
                take_profit: close + 0.010,
                timestamp: str.tostring(timenow)
            }
        ),
        alert.freq_once_per_bar_close
    )

if short_condition
    strategy.entry("Short", strategy.short)
    alert(
        json.stringify(
            {
                id: str.tostring(timenow),
                symbol: "EURUSD",
                action: "SELL",
                quantity: 0.1,
                entry_price: close,
                stop_loss: close + 0.005,
                take_profit: close - 0.010,
                timestamp: str.tostring(timenow)
            }
        ),
        alert.freq_once_per_bar_close
    )

if exit_condition
    strategy.close_all()
    alert(
        json.stringify(
            {
                id: str.tostring(timenow),
                symbol: "EURUSD",
                action: "CLOSE",
                timestamp: str.tostring(timenow)
            }
        ),
        alert.freq_once_per_bar_close
    )
```

---

## Signature Verification

### How HMAC Verification Works

1. Bot generates signature using WEBHOOK_SECRET
2. TradingView sends signature in `X-Webhook-Signature` header
3. Bot verifies signature matches

### Test HMAC Signature

```python
import hmac
import hashlib

# Your webhook secret
secret = "your-webhook-secret"

# Request body (JSON)
body = '{"symbol":"EURUSD","action":"BUY"}'

# Generate signature
signature = hmac.new(
    secret.encode(),
    body.encode(),
    hashlib.sha256
).hexdigest()

print(f"X-Webhook-Signature: {signature}")
```

### Send Signed Request via cURL

```bash
# Generate signature
SECRET="your-webhook-secret"
BODY='{"symbol":"EURUSD","action":"BUY","quantity":0.1}'
SIGNATURE=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | cut -d' ' -f2)

# Send request with signature
curl -X POST https://your-bot-domain.com/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: $SIGNATURE" \
  -d "$BODY"
```

**Response:**
```json
{
  "status": "success",
  "trade_id": "ord_1234567890",
  "signal_id": "sig_1234567890"
}
```

---

## Response Codes & Error Handling

### Success Responses

**200 OK - Order Accepted**
```json
{
  "status": "success",
  "trade_id": "ord_1234567890",
  "signal_id": "sig_1234567890",
  "symbol": "EURUSD",
  "quantity": 0.1
}
```

### Error Responses

**400 Bad Request - Invalid Signal**
```json
{
  "error": "Missing required fields: ['symbol', 'action']"
}
```

Reasons:
- Missing required fields
- Invalid JSON format
- Invalid action (not BUY/SELL/CLOSE)
- Symbol not in ALLOWED_SYMBOLS
- Invalid numeric values

**401 Unauthorized - Invalid Signature**
```json
{
  "error": "Unauthorized"
}
```

Reasons:
- WEBHOOK_SECRET doesn't match
- X-Webhook-Signature header missing
- Request body was modified in transit

**403 Forbidden - Risk Check Failed**
```json
{
  "error": "Daily loss limit reached"
}
```

Reasons:
- Daily loss limit hit
- Max open positions reached
- Position size too large
- Duplicate position for symbol

**500 Server Error - Broker Connection Failed**
```json
{
  "error": "Broker temporarily unavailable"
}
```

Reasons:
- Broker API unreachable
- Database error
- Internal server error

---

## Debugging Webhooks

### Check if Bot is Receiving Webhooks

```bash
# View logs in real-time
tail -f logs/bot.log | grep "Received webhook"
```

**Expected output:**
```
2026-04-18 10:30:05 - WebhookListener - INFO - Received webhook: {'symbol': 'EURUSD', 'action': 'BUY', ...}
```

### Test with cURL

```bash
# Test valid signal
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"EURUSD","action":"BUY","quantity":0.1}'

# Test with invalid symbol
curl -X POST http://localhost:5000/webhook \
  -d '{"symbol":"INVALID","action":"BUY"}'
```

### TradingView Alert Test

In TradingView:
1. Go to Alerts page
2. Find your alert
3. Click **Test Alert** button
4. It will send a test webhook to your bot

Check bot logs to confirm receipt.

---

## Best Practices

### 1. Always Set Stop Loss

Every signal should include a stop loss:
```json
{
  "symbol": "EURUSD",
  "action": "BUY",
  "stop_loss": 1.0900,  // ← Required for safety
  "take_profit": 1.1050
}
```

### 2. Use Market Orders for Execution

For reliable entry:
```json
{
  "entry_price": 0,  // Market order
  "stop_loss": 1.0900
}
```

Not:
```json
{
  "entry_price": 1.0950,  // Limit order might not fill
  "stop_loss": 1.0900
}
```

### 3. Include Unique Signal IDs

For debugging:
```json
{
  "id": "signal_{{timenow}}_RSI_BUY",
  "symbol": "EURUSD"
}
```

### 4. Test on Demo Account First

Keep `DEMO_MODE=true` until you've verified 10+ trades.

### 5. Monitor Logs

```bash
tail -f logs/bot.log
```

Watch for:
- Webhook receives ✓
- Risk checks passed ✓
- Orders placed ✓
- P&L logged ✓

---

## Security Reminder

- **Never expose WEBHOOK_SECRET** in logs or error messages
- **Use HTTPS** for production (not HTTP)
- **Verify X-Webhook-Signature** header
- **Whitelist TradingView IPs** in firewall
- **Keep API keys in .env** (never in code)

---

## Support

For issues:
1. Check bot logs: `tail -f logs/bot.log`
2. Test webhook: `curl http://localhost:5000/health`
3. Review this specification
4. Check `how-it-works.md` for detailed flow
