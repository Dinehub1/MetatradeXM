# 🗂️ Supabase Quick Reference
**Last Updated**: 2026-05-05

---

## ✅ What You Have

### ✅ ACTIVE (Working)
```
✓ Core trading tables (1,586 trades)
  ├─ trade_outcomes: 1,586 rows [complete trade results]
  ├─ trade_entries: 1,583 rows [entry records]
  ├─ market_patterns: 25,744 rows [session/hour patterns]
  └─ filtered_trades: 55 rows [rejected signals]

✓ Live Dashboard (real-time snapshots)
  ├─ live_account_snapshots: 1 row [current balance]
  ├─ live_events: 192 rows [bot event log]
  └─ live_market_snapshots: 2 rows [current prices]

✓ Audit tables
  └─ live_events: Active logging ✓
```

### ⚠️ PARTIAL (Needs Work)
```
⚠ live_positions: Empty (need to sync from broker)
⚠ learning_log: Empty (need to activate logging)
```

---

## 🔴 What Needs Update

### Priority 1: CRITICAL (Today)
```
1. Activate learning_log 🧠
   └─ Add db.log_learning() to self_improver.py
   
2. Fix negative P&L 💰
   └─ Review scoring thresholds → raise buy_threshold from 6→8
   └─ Add session filters
```

### Priority 2: HIGH (This Week)
```
3. Sync live_positions 📍
   └─ Add position sync to continuous_trader.py
   
4. Update market_snapshots 📊
   └─ Add real-time price updates to analysis loop
```

### Priority 3: MEDIUM (Next 2 weeks)
```
5. Backfill missing data 📦
   └─ 30 trades missing volume/profit_usd/broker_symbol
   
6. Apply learning insights 🎯
   └─ Create feedback loop to adjust weights
```

---

## 🗄️ Table Quick Reference

| Table | Rows | Purpose | Status |
|-------|------|---------|--------|
| `trade_outcomes` | 1,586 | Trade results | ✅ |
| `trade_entries` | 1,583 | Entry records | ✅ |
| `market_patterns` | 25,744 | Session patterns | ✅ |
| `live_account_snapshots` | 1 | Account balance | ✅ |
| `live_events` | 192 | Event log | ✅ |
| `live_market_snapshots` | 2 | Current prices | ⚠️ Sparse |
| `live_positions` | 0 | Open trades | ❌ Empty |
| `learning_log` | 0 | AI insights | ❌ Empty |
| `filtered_trades` | 55 | Rejected signals | ✅ |

---

## 📊 Key Metrics

| Metric | Value | Status |
|--------|-------|--------|
| Total Trades | 1,000+ | ✅ |
| Win Rate | 48.8% | ⚠️ Below 50% target |
| Avg Pips/Trade | -30.0 | 🔴 Negative |
| P&L | -$250.40 | 🔴 Underwater |
| Recent Trend | 5 losses | 🔴 Losing streak |

---

## 🔧 Common Commands

### Check Connection
```bash
cd /Users/mac/Documents/Devlopments/metatradeXM/MetatradeXM
python3 test_supabase.py
```

### View Recent Trades
```python
from src.core.supabase_db import SupabaseDB
db = SupabaseDB()
trades = db.get_all_outcomes(limit=10)
for t in trades:
    print(f"{t['ts'][:19]} | {t['symbol']} {t['outcome']} {t['pips_result']:+.1f}p")
```

### Check Learning Insights
```python
insights = db.get_recent_learning(limit=5)
for i in insights:
    print(f"[{i['insight_type']}] {i['insight_text']}")
```

### View Account Balance
```python
account = db.get_live_account_snapshot()
print(f"Balance: ${account['balance']}, Equity: ${account['equity']}")
```

### Get Factor Statistics
```python
stats = db.get_factor_stats()
for factor, data in stats.items():
    print(f"{factor}: {data['win_rate']:.0%} WR")
```

---

## 📝 Files to Modify

### To Activate Learning Log
**File**: `src/self_improver.py`
```python
# Add after generating insights:
db.log_learning(
    insight_type="factor_correlation",
    insight_text="...",
    data={...},
    applied=True
)
```

### To Fix Win Rate
**File**: `scoring_weights.json`
```json
{
  "buy_threshold": 8,  // was 6
  "sell_threshold": 8  // was 6
}
```

### To Sync Positions
**File**: `src/continuous_trader.py`
```python
# In main loop:
positions = await get_open_positions()
db.replace_live_positions(positions)
```

---

## 🎯 Success Checklist

- [ ] learning_log has 5+ records
- [ ] Win rate ≥ 52%
- [ ] P&L positive
- [ ] live_positions syncs
- [ ] Market snapshots < 1 min old

---

## 📚 Reference Docs

- **Full Audit**: `SUPABASE_AUDIT.md` (detailed analysis)
- **Roadmap**: `UPDATE_PRIORITY_ROADMAP.md` (implementation plan)
- **Schema**: `migrations/001_supabase_schema.sql` (database structure)
- **Code**: `src/core/supabase_db.py` (890 lines of adapter code)

---

**MCP Status**: Authenticated ✅  
**Data Status**: Connected & Valid ✅  
**Ready to Update**: YES ✅
