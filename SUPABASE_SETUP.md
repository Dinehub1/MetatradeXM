# 🚀 Supabase Integration Setup Guide

Complete guide to set up Supabase as your cloud database for MetatradeXM across all environments (Mac, Ubuntu VM, Windows).

---

## 📋 Table of Contents

1. [Quick Start (5 minutes)](#quick-start)
2. [Detailed Setup (Step-by-step)](#detailed-setup)
3. [Migration Process](#migration-process)
4. [Configuration](#configuration)
5. [Real-time Sync](#real-time-sync)
6. [Troubleshooting](#troubleshooting)

---

## ⚡ Quick Start

### 1. Create Supabase Project

1. Go to [https://supabase.com](https://supabase.com)
2. Click **"Create a new project"**
3. Sign in / Sign up
4. Fill in:
   - **Project name**: `metatradexm` (or your choice)
   - **Database password**: Create a strong password
   - **Region**: Choose closest to you (e.g., `us-east-1`)
5. Click **"Create new project"** and wait ~2 minutes

### 2. Get Your Credentials

1. Go to **Settings > API** in your Supabase dashboard
2. Copy these two values:
   - **Project URL**: `https://xxxxx.supabase.co`
   - **Anon Public Key**: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`

### 3. Create Database Schema

1. In Supabase, go to **SQL Editor**
2. Click **"New Query"**
3. Copy & paste the entire contents of `migrations/001_supabase_schema.sql`
4. Click **"Run"** (green play button)
5. Wait for tables to be created ✅

### 4. Configure Your Project

Create a `.env` file in the project root:

```bash
cat > /home/user/MetatradeXM/.env << 'EOF'
# Supabase Cloud Database
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# MetaApi (your existing credentials)
METAAPI_TOKEN=your_metaapi_token
METAAPI_ACCOUNT_ID=your_account_id

# Ollama (local AI)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=minimax-m2.7:cloud

# Trading
DRY_RUN=false
LOG_LEVEL=INFO
EOF
```

### 5. Install Dependencies

```bash
cd /home/user/MetatradeXM
pip install -r requirements.txt
# This installs: supabase, postgrest-py, and other dependencies
```

### 6. Migrate Your Existing Data (if any)

```bash
python3 scripts/migrate_to_supabase.py
```

This script:
- Reads your local `data/trade_memory.db` (SQLite)
- Transfers all trade history to Supabase
- Preserves all data: trade outcomes, entries, patterns, learning logs
- **Does NOT delete your local .db file** (you can keep it as backup)

### 7. Start Trading!

```bash
python3 start_trading_cycle.sh
```

Your bot will now use Supabase instead of SQLite. All three environments (Mac, Ubuntu, Windows) will share the same database.

---

## 🔧 Detailed Setup

### Step-by-step for Each Environment

#### **Mac (Local Development)**

```bash
# 1. Clone/update repo to feature branch
cd /home/user/MetatradeXM
git fetch origin
git checkout claude/add-supabase-integration-fbdr3

# 2. Create .env with Supabase credentials
# (see "Configure Your Project" section above)

# 3. Install Supabase SDK
pip install --upgrade supabase postgrest-py

# 4. Test connection
python3 -c "from src.core.supabase_db import SupabaseDB; db = SupabaseDB(); print('✅ Connected to Supabase!')"

# 5. Migrate data (if you have trade_memory.db)
python3 scripts/migrate_to_supabase.py

# 6. Run bot
python3 start_trading_cycle.sh
```

#### **Ubuntu VM**

```bash
# SSH into Ubuntu VM
ssh ubuntu@your_vm_ip

# 1. Navigate to project
cd ~/MetatradeXM
git fetch origin
git checkout claude/add-supabase-integration-fbdr3

# 2. Create .env with SAME Supabase credentials (cloud DB is shared!)
# Copy the .env from your Mac or create new one with same credentials

# 3. Install Supabase SDK
pip install --upgrade supabase postgrest-py

# 4. Test connection
python3 -c "from src.core.supabase_db import SupabaseDB; db = SupabaseDB(); print('✅ Connected to Supabase!')"

# 5. Run bot (data is already in cloud, no migration needed)
python3 start_trading_cycle.sh
```

#### **Windows (Native MT5 Bridge)**

```cmd
# PowerShell or CMD
cd C:\path\to\MetatradeXM
git fetch origin
git checkout claude/add-supabase-integration-fbdr3

# Create .env with SAME Supabase credentials
# (use Notepad or your editor)

# Install Supabase SDK
pip install --upgrade supabase postgrest-py

# Test connection
python -c "from src.core.supabase_db import SupabaseDB; db = SupabaseDB(); print('✅ Connected to Supabase!')"

# Run bot
python win_webhook_mt5.py
```

---

## 📦 Migration Process

### What Gets Migrated?

| Table | Records | Data |
|-------|---------|------|
| `trade_outcomes` | All past trades | Entry/exit prices, pips, confidence, factors |
| `trade_entries` | Open trades | Current positions, entry details |
| `market_patterns` | Pattern analysis | Hourly/daily win rates, pips by session |
| `learning_log` | AI insights | All self-improvement decisions |
| `filtered_trades` | Filtered signals | Trade rejections & reasons |

### Migration Script

```bash
python3 scripts/migrate_to_supabase.py
```

**Output:**
```
[2026-05-04] Migrating trade_outcomes... ✅ Migrated 42 trade_outcomes
[2026-05-04] Migrating trade_entries... ✅ Migrated 3 trade_entries
[2026-05-04] Migrating market_patterns... ✅ Migrated 156 market_patterns
[2026-05-04] Migrating learning_log... ✅ Migrated 12 learning_log entries
[2026-05-04] Migrating filtered_trades... ✅ Migrated 28 filtered_trades

✅ Migration complete! Your data is now in Supabase.
```

### Verify Migration

```bash
# Check Supabase dashboard
# Go to SQL Editor and run:
SELECT COUNT(*) as count FROM trade_outcomes;
SELECT COUNT(*) as count FROM trade_entries;
```

Or via Python:

```python
from src.core.supabase_db import SupabaseDB
db = SupabaseDB()
outcomes = db.get_all_outcomes(limit=5)
print(f"Found {len(outcomes)} recent trades")
for o in outcomes:
    print(f"  {o['symbol']} {o['outcome']} {o['pips_result']:+.1f}pips")
```

---

## ⚙️ Configuration

### Environment Variables

Create `.env` in project root:

```bash
# ── REQUIRED: Supabase Credentials ──────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# ── OPTIONAL: MetaApi (your existing credentials) ──
METAAPI_TOKEN=your_token
METAAPI_ACCOUNT_ID=your_account_id

# ── OPTIONAL: Local Ollama ─────────────────────────
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=minimax-m2.7:cloud

# ── Optional: Trading Mode ────────────────────────
DRY_RUN=false              # Set to true for paper trading
LOG_LEVEL=INFO             # DEBUG, INFO, WARNING, ERROR
```

### Where to Get Credentials

**Supabase Project URL & Anon Key:**
1. Log in to [supabase.com](https://supabase.com)
2. Open your project
3. **Settings > API**
4. Copy **Project URL** and **Anon Public Key**

**MetaApi Token & Account ID:**
- Already in your project from before (if you have them)
- Or from [MetaApi Dashboard](https://app.metaapi.cloud)

---

## 🔌 Real-time Sync

### What is Real-time Sync?

Supabase Realtime API sends WebSocket notifications whenever the database changes. This means:

- **Mac** starts a trade → **Ubuntu VM** sees it immediately
- **Ubuntu VM** closes a position → **Windows** dashboard updates instantly
- **Any environment** logs an insight → **All environments** learn from it

### Enable Real-time Listeners

#### In Your Trading Code:

```python
from src.core.supabase_realtime import SupabaseRealtimeListener

# Initialize listener
realtime = SupabaseRealtimeListener()

# Subscribe to specific tables
realtime.listen_to_trade_outcomes()
realtime.listen_to_trade_entries()
realtime.listen_to_learning_log()

# Start background thread
realtime.start_listening()

# Now run your trading bot
# Events will be logged automatically
```

#### Custom Callbacks:

```python
def on_new_trade(event_type, data):
    if event_type == "INSERT":
        print(f"🔴 NEW TRADE: {data['symbol']} {data['direction']}")
    elif event_type == "UPDATE":
        print(f"📊 TRADE UPDATED: {data['symbol']} {data['outcome']}")

realtime = SupabaseRealtimeListener()
realtime.listen_to_trade_outcomes(callback=on_new_trade)
realtime.start_listening()
```

### Real-time Dashboard

The Flask dashboard (`src/dashboard/dashboard.py`) can display:
- Live position updates from any environment
- Real-time trade outcomes as they happen
- Learning insights shared across environments

---

## 🐛 Troubleshooting

### "SUPABASE_URL and SUPABASE_ANON_KEY required"

**Problem:** `ValueError: SUPABASE_URL and SUPABASE_ANON_KEY required. Set in .env or pass as arguments.`

**Solution:**
```bash
# Check your .env file exists
cat /home/user/MetatradeXM/.env | grep SUPABASE

# If empty, add it:
echo "SUPABASE_URL=https://your-project.supabase.co" >> .env
echo "SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." >> .env
```

### "Failed to connect to Supabase"

**Problem:** Connection timeout or 404 error

**Solution:**
1. Check project URL is correct (no typos)
2. Verify API keys are recent (regenerate in Settings > API if needed)
3. Check internet connectivity
4. Verify Supabase project is not paused (check Status page)

### "Migration says 0 records migrated"

**Problem:** Script runs but migrates 0 rows

**Solution:**
```bash
# Check if SQLite db exists
ls -la /home/user/MetatradeXM/data/trade_memory.db

# If it doesn't exist, migration will skip it (that's OK!)
# If it does exist, check if it has data:
sqlite3 /home/user/MetatradeXM/data/trade_memory.db "SELECT COUNT(*) FROM trade_outcomes;"
```

### "Real-time listeners not working"

**Problem:** Webhook events not being received

**Solution:**
1. Check that Supabase project has Realtime enabled:
   - **Settings > Database > Replication**
   - Make sure tables are in the "Replication" list
2. Check WebSocket connection:
   ```python
   from src.core.supabase_realtime import SupabaseRealtimeListener
   realtime = SupabaseRealtimeListener()
   realtime.listen_to_trade_outcomes()
   realtime.start_listening()
   # Should see: "Subscribed to trade_outcomes real-time changes"
   ```

### Still having issues?

Check logs:
```bash
# View recent logs
tail -f logs/metatrader*.log | grep -E "SUPABASE|realtime|migration"

# Enable debug logging
export LOG_LEVEL=DEBUG
python3 start_trading_cycle.sh
```

---

## 📊 Verifying Everything Works

### Test 1: Database Connection

```bash
python3 << 'EOF'
from src.core.supabase_db import SupabaseDB
db = SupabaseDB()
print("✅ Connected to Supabase")
print(f"Database URL: {db.url}")
EOF
```

### Test 2: Record a Trade Entry

```bash
python3 << 'EOF'
from src.core.supabase_db import SupabaseDB
db = SupabaseDB()
db.record_entry(
    ticket="TEST-001",
    symbol="XAUUSD",
    direction="BUY",
    entry_price=2500.00,
    confidence=0.75,
    factors={"rsi": 35, "macd": 0.5},
    conditions={"adx": 25, "session": "LONDON"}
)
print("✅ Trade entry recorded")
EOF
```

### Test 3: Cross-Environment Sync

1. **Mac:** Run `python3 start_trading_cycle.sh`
2. **Ubuntu:** Run `python3 start_trading_cycle.sh`
3. **Any environment:** Execute trade via bot or WebSocket
4. **Check all three:** Verify trade appears in logs/dashboard on all machines

### Test 4: Real-time Listeners

```bash
python3 << 'EOF'
from src.core.supabase_realtime import SupabaseRealtimeListener

realtime = SupabaseRealtimeListener()
realtime.listen_to_trade_outcomes()
realtime.start_listening()

print("✅ Real-time listeners active (watch logs...)")
print("Now make a trade in another environment")
# Should see [REALTIME] logs appear

import time
time.sleep(30)
EOF
```

---

## 🎯 Summary

You now have:

✅ **Cloud Database** - Trade data synced across all environments  
✅ **Zero Data Loss** - No more local .db file overwrites  
✅ **Real-time Sync** - Changes propagate instantly via WebSocket  
✅ **Multi-Environment** - Mac, Ubuntu, Windows all share same DB  
✅ **Migration Tool** - Preserved all historical trade data  
✅ **Fallback Support** - Gracefully falls back to SQLite if Supabase unavailable

---

## 📞 Support

For Supabase docs: https://supabase.com/docs  
For API reference: https://supabase.com/docs/reference/python/overview  
For issues: Check project logs and troubleshooting section above
