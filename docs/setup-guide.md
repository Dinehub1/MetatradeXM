# Setup & Installation Guide

## Prerequisites

Before you start, ensure you have:

- **Python 3.8+** - `python --version`
- **pip** - Python package manager
- **Git** - For version control
- **Broker Account** - MetaTrader5, Binance, etc.
- **TradingView Account** - With webhook capability (Pro/Premium)
- **Linux/Mac/Windows** - Any OS with Python support

### API Keys Required

You'll need:
1. **Broker API Key & Secret** - From your broker account
2. **Webhook Secret** - A random string (you create this)
3. **Static IP or DNS** - For TradingView to reach your bot

---

## Installation Steps

### Step 1: Clone/Download the Bot

```bash
# Clone from git (if hosted on GitHub)
git clone https://github.com/your-username/trading-bot.git
cd trading-bot

# OR download as zip and extract
unzip trading-bot.zip
cd trading-bot
```

### Step 2: Create Python Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/Mac:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate
```

You should see `(venv)` at the start of your terminal prompt.

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs all required packages:
- Flask (web framework)
- MetaTrader5 / python-binance (broker APIs)
- python-dotenv (configuration)
- SQLAlchemy (database)
- pytest (testing)

**Note:** If a package fails to install:
- Check your Python version (3.8+)
- Try: `pip install --upgrade pip setuptools wheel`
- Then retry the install

### Step 4: Create Configuration File

```bash
# Copy example config
cp .env.example .env

# Edit the config
nano .env  # or use your preferred editor
```

### Step 5: Configure Your Bot

Edit `.env` with your details:

```bash
# ===== CRITICAL SETTINGS =====

# Your Broker
BROKER=metatrader5
API_KEY=your_broker_api_key_here
API_SECRET=your_broker_api_secret_here
API_ACCOUNT=your_account_number

# Webhook Security (Create a random string)
# Example: Generate with: python -c "import secrets; print(secrets.token_hex(32))"
WEBHOOK_SECRET=abc123def456ghi789jkl012mno345pqr

# Trading Parameters
DEFAULT_LOT_SIZE=0.1
MAX_POSITION_SIZE=1.0
MAX_DAILY_LOSS=500.0
MAX_OPEN_POSITIONS=5

# Allowed Symbols
ALLOWED_SYMBOLS=EURUSD,GBPUSD,USDJPY

# Safety Mode (Keep as true until fully tested!)
DEMO_MODE=true

# Logging
LOG_LEVEL=INFO
```

**⚠️ IMPORTANT:**
- Keep `DEMO_MODE=true` until you've tested everything
- Generate a strong WEBHOOK_SECRET
- Never share your API keys
- Always keep `.env` in `.gitignore`

### Step 6: Create Logs Directory

```bash
mkdir -p logs
```

The bot will automatically create log files here.

### Step 7: Test the Installation

```bash
# Verify everything works
python -c "from src.config import Config; Config.validate(); print('✓ Config valid')"

# Run basic tests
pytest tests/ -v

# Start the bot
python -m src.main
```

Expected output:
```
============================================================
Initializing Trading Bot
============================================================
✓ Configuration validated
✓ Broker connected
✓ Risk manager initialized
✓ Order manager initialized
✓ Webhook listener initialized
============================================================
Bot initialization complete
============================================================
Starting webhook listener...
Listening on 0.0.0.0:5000
```

---

## Configuration Example

### For MetaTrader5 (Forex Trading)

```bash
BROKER=metatrader5
API_KEY=metatrader5_login
API_SECRET=metatrader5_password
API_ACCOUNT=12345
ALLOWED_SYMBOLS=EURUSD,GBPUSD,USDJPY,AUDUSD
DEFAULT_LOT_SIZE=0.1
MAX_POSITION_SIZE=1.0
DEMO_MODE=true
```

### For Binance (Crypto Trading)

```bash
BROKER=binance
API_KEY=your_binance_api_key
API_SECRET=your_binance_api_secret
ALLOWED_SYMBOLS=ETHUSDT,BTCUSDT,BNBUSDT
DEFAULT_LOT_SIZE=0.1
DEMO_MODE=true
```

---

## Running the Bot

### Development Mode

```bash
# Start the bot
python -m src.main

# In another terminal, test the webhook
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "EURUSD",
    "action": "BUY",
    "quantity": 0.1,
    "stop_loss": 1.0900,
    "take_profit": 1.1050
  }'
```

Expected response:
```json
{
  "status": "success",
  "signal_id": "sig_1234567890",
  "trade_id": "ord_1234567890"
}
```

### Production Mode

```bash
# Install gunicorn (if not already in requirements)
pip install gunicorn

# Run with gunicorn (4 workers, port 5000)
gunicorn -w 4 -b 0.0.0.0:5000 src.main:app

# Or use systemd service (see below)
sudo systemctl start trading-bot
```

### Create Systemd Service (Linux)

```bash
# Create service file
sudo nano /etc/systemd/system/trading-bot.service
```

Paste this content:
```ini
[Unit]
Description=Trading Bot
After=network.target

[Service]
Type=simple
User=tradingbot
WorkingDirectory=/home/tradingbot/trading-bot
Environment="PATH=/home/tradingbot/trading-bot/venv/bin"
ExecStart=/home/tradingbot/trading-bot/venv/bin/python -m src.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
sudo systemctl status trading-bot
```

---

## Testing with Simulated Signals

### Test 1: Health Check

```bash
curl http://localhost:5000/health
```

Expected:
```json
{
  "status": "healthy",
  "timestamp": "2026-04-18T10:35:00Z"
}
```

### Test 2: Buy Signal (Demo Mode)

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: $(python -c 'import hmac, hashlib; print(hmac.new(b\"test-secret\", b\"{}\", hashlib.sha256).hexdigest())')" \
  -d '{
    "symbol": "EURUSD",
    "action": "BUY",
    "quantity": 0.1,
    "entry_price": 1.0950,
    "stop_loss": 1.0900,
    "take_profit": 1.1050
  }'
```

### Test 3: Close Position

```bash
curl -X POST http://localhost:5000/webhook \
  -d '{
    "symbol": "EURUSD",
    "action": "CLOSE"
  }'
```

### Test 4: Invalid Symbol (Should Reject)

```bash
curl -X POST http://localhost:5000/webhook \
  -d '{
    "symbol": "INVALID",
    "action": "BUY"
  }'
```

Expected: `400 Bad Request`

---

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'src'"

**Solution:**
```bash
# Make sure you're in the trading-bot directory
cd trading-bot

# Activate venv
source venv/bin/activate

# Install requirements again
pip install -r requirements.txt
```

### Issue: "Port 5000 already in use"

**Solution:**
```bash
# Find process using port 5000
lsof -i :5000

# Kill the process
kill -9 <PID>

# Or use a different port in .env
WEBHOOK_PORT=5001
```

### Issue: "Broker connection failed"

**Solution:**
- Verify API credentials in `.env`
- Check broker server status
- Ensure DEMO_MODE=true for testing
- Check firewall/network connectivity

### Issue: "No signals received"

**Solution:**
- Check TradingView webhook URL points to your bot
- Verify WEBHOOK_SECRET is set correctly
- Check bot logs: `tail -f logs/bot.log`
- Test with curl command above

### Issue: "Daily loss limit reached"

**This is by design** — the bot stops trading when daily loss hits the limit.

**To fix:**
- Wait until next day (automatic reset)
- Lower MAX_DAILY_LOSS in .env
- Review losing trades in `logs/trades.db`

---

## Logs & Monitoring

### View Live Logs

```bash
tail -f logs/bot.log
```

### Example Log Output

```
2026-04-18 10:30:05 - WebhookListener - INFO - Received webhook: {'symbol': 'EURUSD', ...}
2026-04-18 10:30:05 - OrderManager - INFO - Processing signal: BUY 0.1 EURUSD
2026-04-18 10:30:05 - RiskManager - INFO - Position opened: EURUSD x0.1 @ 1.0950
2026-04-18 10:30:06 - OrderManager - INFO - Order ord_1234567890 placed successfully
2026-04-18 10:35:10 - WebhookListener - INFO - Received webhook: {'symbol': 'EURUSD', 'action': 'CLOSE'}
2026-04-18 10:35:10 - RiskManager - INFO - Position closed: EURUSD P&L: +$100.00
```

### Database Queries

```bash
# View all trades
sqlite3 logs/trades.db "SELECT * FROM orders;"

# View today's trades
sqlite3 logs/trades.db "SELECT * FROM orders WHERE DATE(created_at) = DATE('now');"

# Summary stats
sqlite3 logs/trades.db "SELECT COUNT(*), SUM(pnl) FROM trades WHERE DATE(created_at) = DATE('now');"
```

---

## Security Checklist

Before going LIVE:

- [ ] API keys stored in `.env` (not in code)
- [ ] `.env` added to `.gitignore`
- [ ] WEBHOOK_SECRET set to strong random value
- [ ] WEBHOOK_SECRET matches TradingView config
- [ ] Firewall configured (allow only your IP)
- [ ] HTTPS enabled (SSL certificate)
- [ ] DEMO_MODE=false only after full testing
- [ ] Max daily loss set reasonably (not too high)
- [ ] Position size limits set
- [ ] Max open positions limit set
- [ ] Broker account has sufficient balance
- [ ] Stop losses configured on all trades
- [ ] Monitoring alerts configured
- [ ] Backup/disaster recovery plan in place

---

## Next Steps

1. ✓ Install bot
2. ✓ Configure .env
3. ✓ Test with demo mode
4. → **Set up TradingView webhook** (see `tradingview-webhook-spec.md`)
5. → Monitor logs and trades
6. → Switch to live mode (DEMO_MODE=false)

---

See **tradingview-webhook-spec.md** for webhook setup.
