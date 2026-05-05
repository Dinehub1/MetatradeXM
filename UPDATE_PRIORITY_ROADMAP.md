# 🚀 Supabase Update Priority Roadmap
**Status**: Ready to Execute  
**Target**: Stabilize system → Activate learning → Improve profitability

---

## 🔴 TIER 1: CRITICAL (Do Today)

### Issue #1: Learning Log Not Activated
**Severity**: 🔴 CRITICAL | **Impact**: Self-improvement engine disabled

**What's broken**:
- `learning_log` table is empty (0 records)
- `self_improver.py` runs analysis but doesn't log insights
- No feedback loop to adapt strategy

**Current State**:
```
self_improver.py → Analyzes past trades → (insights disappear)
                  → No logging to learning_log
                  → Strategy never improves
```

**How to fix**:
1. **File**: `src/self_improver.py`
2. **Action**: Add `db.log_learning()` calls after each insight discovery
3. **Example**:
```python
# In self_improver.py, after analyzing correlations:
for factor, stats in factor_analysis.items():
    if stats['win_rate'] > 0.65:
        db.log_learning(
            insight_type="factor_correlation",
            insight_text=f"{factor} shows {stats['win_rate']:.0%} WR — increase threshold",
            data=stats,
            applied=True  # Will be auto-applied in next cycle
        )
```
4. **Verify**: Run `test_supabase.py` → should show learning records

**Priority**: 🔴 **DO IMMEDIATELY** (blocks adaptive learning)

---

### Issue #2: Negative Win Rate & Underwater P&L
**Severity**: 🔴 CRITICAL | **Impact**: Losing real money

**What's broken**:
- Win rate: 48.8% (need ≥52% for edge)
- P&L: -$250.40 (negative)
- Last 5 trades: 5 LOSSES

**Analysis Required**:
```python
# Check which factors are failing:
stats = db.get_factor_stats()
# Check time-of-day patterns:
patterns = db.get_pattern_summary("XAUUSD")
```

**Likely Root Causes**:
1. **Scoring thresholds too loose** → entry on weak signals
2. **Exit strategy too aggressive** → stops hit on noise
3. **Wrong session/time** → trading low-probability hours
4. **Market regime change** → strategy needs recalibration

**How to fix**:
1. **File**: `src/analyzer.py` + `scoring_weights.json`
2. **Action**: Analyze and implement:
   - Raise `buy_threshold` from 6 to 8 (filter weak signals)
   - Raise `sell_threshold` similarly
   - Add session filter (skip low-probability times)
   - Review ADX requirement (need strong trend)

3. **Example Fix**:
```json
{
  "buy_threshold": 8,
  "sell_threshold": 8,
  "min_adx": 25,
  "session_filter": {
    "LONDON": 0.95,
    "LONDON_NY_OVERLAP": 1.0,
    "NEW_YORK": 0.92,
    "ASIAN": 0.70
  }
}
```

**Priority**: 🔴 **DO TODAY** (losing money every hour)

---

## 🟡 TIER 2: HIGH PRIORITY (This Week)

### Issue #3: Live Positions Not Syncing
**Severity**: 🟡 HIGH | **Impact**: Dashboard shows outdated data

**What's broken**:
- `live_positions` table is empty
- Dashboard can't show current open positions
- Account snapshot outdated (last update: unknown)

**How to fix**:
1. **File**: `src/continuous_trader.py` main loop
2. **Action**: Sync positions after every MetaApi call
```python
# In main trading loop:
positions = await metaapi_bridge.get_open_positions()
db.replace_live_positions(positions, source="metaapi")

account = await metaapi_bridge.get_account_info()
db.upsert_live_account_snapshot(account, source="metaapi")
```

3. **Verify**: 
   - `live_positions` has current open trades
   - `live_account_snapshots` updated_at timestamp ≤ 1 min ago

**Priority**: 🟡 **This Week** (dashboard accuracy)

---

### Issue #4: Missing Market Snapshots
**Severity**: 🟡 HIGH | **Impact**: Dashboard shows stale prices

**What's broken**:
- `live_market_snapshots` only has 2 rows (should update every tick)
- Price data stale when someone views dashboard

**How to fix**:
1. **File**: `src/continuous_trader.py`
2. **Action**: Update snapshot on each analysis cycle
```python
# After getting current price/indicators:
db.upsert_live_market_snapshot(
    symbol="XAUUSD",
    broker_symbol=broker_symbol,
    payload={
        "price": current_price,
        "bid": bid,
        "ask": ask,
        "signal": signal,
        "confidence": confidence,
        "indicators": {
            "rsi": rsi,
            "macd": macd,
            "atr": atr,
            "adx": adx
        }
    }
)
```

**Priority**: 🟡 **This Week** (dashboard UX)

---

## 🟢 TIER 3: MEDIUM PRIORITY (Next 2 Weeks)

### Issue #5: Missing Volume/Profit Data
**Severity**: 🟢 MEDIUM | **Impact**: Historical analysis incomplete

**What's broken**:
- 30 trade_outcomes rows missing `volume`
- 30 rows missing `profit_usd`
- 30 rows missing `broker_symbol`

**How to fix (Option A - Recommended)**:
- **Don't fix old data** — mark as legacy
- **Ensure new trades** have complete data via `sync_broker_history()`

**How to fix (Option B - If needed)**:
- **Backfill from MT5** if broker history still available
- **Archive incomplete rows** to `archived_trades` table

**Priority**: 🟢 **Next 2 weeks** (nice-to-have)

---

### Issue #6: Learning Log Not Being Applied
**Severity**: 🟢 MEDIUM | **Impact**: Insights generated but not used

**What's broken**:
- Learning log will store insights, but strategy doesn't read them
- No feedback loop to adjust weights

**How to fix**:
1. **File**: `src/self_improver.py` or new `learning_applier.py`
2. **Action**: On each cycle, check for `applied=True` insights
```python
# In strategy initialization:
recent_insights = db.get_recent_learning(limit=5)
for insight in recent_insights:
    # Apply insight to current weights
    if insight['insight_type'] == 'factor_correlation':
        data = insight['data_json']
        if data['win_rate'] > 0.70:
            increase_weight(data['factor'], 0.05)
```

**Priority**: 🟢 **Next 2 weeks** (feedback loop)

---

## 📋 IMPLEMENTATION CHECKLIST

### Day 1 (Critical Issues)
- [ ] **Fix #1**: Add `db.log_learning()` to `self_improver.py`
  - Estimated time: 30 minutes
  - File: `src/self_improver.py`
  - Verify: Check `learning_log` has records

- [ ] **Fix #2**: Analyze and tune scoring thresholds
  - Estimated time: 2-3 hours
  - Files: `scoring_weights.json`, `src/analyzer.py`
  - Verify: Backtest on historical data → compare P&L before/after

### Week 1 (High Priority)
- [ ] **Fix #3**: Sync live positions to dashboard
  - Estimated time: 1-2 hours
  - File: `src/continuous_trader.py`
  - Verify: `live_positions` updates in real-time

- [ ] **Fix #4**: Update market snapshots
  - Estimated time: 1 hour
  - File: `src/continuous_trader.py`
  - Verify: Dashboard shows current prices

### Week 2-3 (Medium Priority)
- [ ] **Fix #5**: Backfill or archive incomplete data
  - Estimated time: 1-2 hours
  - File: Migration script
  - Verify: All new trades have volume/profit/broker_symbol

- [ ] **Fix #6**: Apply learning insights to weights
  - Estimated time: 2-3 hours
  - File: New `learning_applier.py`
  - Verify: Strategy weights adjust based on insights

---

## 🎯 Success Criteria

After completing all fixes, verify:

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| learning_log records | 0 | 5+ | 🔴 |
| Win rate | 48.8% | ≥52% | 🔴 |
| P&L (cumulative) | -$250 | Positive | 🔴 |
| live_positions synced | ❌ | ✅ Real-time | 🔴 |
| Market snapshots fresh | ⚠️ (2 old) | ✅ <1 min | 🔴 |
| Data completeness | 98% | 100% | 🟡 |

---

## 🔗 Related Files

### Supabase
- Connection: `src/core/supabase_db.py` (890 lines)
- Real-time: `src/core/supabase_realtime.py`
- Schema: `migrations/001_supabase_schema.sql`

### Strategy
- Main logic: `src/continuous_trader.py`
- Scoring: `src/analyzer.py`
- Weights: `scoring_weights.json`
- Self-improvement: `src/self_improver.py`

### Testing
- Connection test: `test_supabase.py`
- Data tools: `tools/verify_data.py`, `tools/query.py`
- Sync script: `scripts/sync_mt5_history.py`

---

## 📞 Need Help?

**Questions about implementation**:
1. Check `CLAUDE.md` (project context)
2. Review `SUPABASE_AUDIT.md` (this report)
3. Run `test_supabase.py` for connection verification
4. Query `learning_log` to see logged insights

**Commands to run**:
```bash
# Test connection
python3 test_supabase.py

# Check data
python3 -c "from src.core.supabase_db import SupabaseDB; db = SupabaseDB(); print(db.get_all_outcomes(limit=5))"

# Verify learning
python3 tools/query.py  # If available
```

---

**Generated**: 2026-05-05  
**By**: Claude Code Supabase Audit  
**Status**: Ready to implement ✅
