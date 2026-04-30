# Week 2 Robustness Fixes — Roadmap

**Target:** Prevent edge-case failures (stale data, connection issues, calculation errors)

---

## Fix #6: Stale Price Validation [1 hour]

**File:** `continuous_trader.py`

**Problem:**
- Order params built from `tick.ask/bid` with no freshness check
- If WebSocket stalls, tick could be 30+ seconds old
- Orders execute at stale prices → unexpected slippage

**Solution:**
```python
def build_order_params(sym_cfg, tick, direction, ...):
    # Validate tick freshness
    tick_age_s = time.time() - getattr(tick, 'time', 0) / 1000
    if tick_age_s > 30:
        log.warning(f"Stale tick ({tick_age_s:.1f}s old) — rejecting order")
        return None
    
    price = tick.ask if direction == "BUY" else tick.bid
    # ... rest of function
```

**Testing:**
- Pause WebSocket; verify stale tick rejection
- Confirm trade skipped, not executed at stale price

---

## Fix #7: Bridge Connection Test at Startup [1 hour]

**File:** `continuous_trader.py`

**Problem:**
- Bridge created but not tested until first trade
- If chosen bridge (WebSocket/Webhook) unavailable, bot waits 30+ seconds
- Orders queue and fail silently

**Solution:**
```python
def connect_with_retry(bridge, max_attempts: int = 5) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            log.info(f"🔌 Connecting to bridge (attempt {attempt}/{max_attempts})...")
            if bridge.connect():
                log.info("✅ Bridge connected")
                return True
        except Exception as e:
            log.warning(f"Attempt {attempt} failed: {e}")
        if attempt < max_attempts:
            wait = 10 * attempt  # exponential backoff
            log.info(f"   Retrying in {wait}s...")
            time.sleep(wait)
    log.error("❌ Could not connect after all attempts")
    return False

# Call in ContinuousTrader.__init__:
if not connect_with_retry(self.bridge):
    raise RuntimeError("Cannot start trading without broker connection")
```

**Testing:**
- Disable WebSocket URL; verify startup fails with clear message
- Enable fallback bridge; verify trading continues

---

## Fix #8: ATR Zero/NaN Validation [1 hour]

**File:** `continuous_trader.py` (order params) + `core/analyzer.py`

**Problem:**
- ATR calculated from indicators; could be 0 or NaN if candles incomplete
- Code checks `if atr > 0`, but doesn't validate `not pd.isna(atr)`
- Fallback fixed stops used silently → risk sizing inaccurate

**Solution:**
```python
def build_order_params(sym_cfg, tick, direction, ..., atr: float = 0):
    import math
    
    # Validate ATR: must be positive number
    if pd.isna(atr) or atr <= 0:
        log.warning(f"Invalid ATR ({atr}) — using fallback fixed stops")
        sl_pips = sym_cfg["sl_pips"]
        tp_pips = sym_cfg["tp_pips"]
    else:
        # ATR-based calculation
        sl_mult = sym_cfg.get("sl_atr_mult", 1.5)
        tp_mult = sym_cfg.get("tp_atr_mult", 4.5)
        sl_pips = max(int(atr * sl_mult / pip), 15)
        tp_pips = max(int(atr * tp_mult / pip), 35)
    # ... rest of function
```

**Testing:**
- Force candles < 14 rows; verify fallback stops used and logged

---

## Fix #9: Synchronous File I/O → Background Thread [3 hours]

**File:** `continuous_trader.py`

**Problem:**
- `_save_cooldown()`, `_save_streaks()` block analysis cycle
- On 100 simultaneous closures: 100 × 50ms = 5s block
- Bot misses signals during I/O wait

**Solution:**
```python
import queue
import threading

# Global save queue
_save_queue = queue.Queue()

def _file_writer_thread():
    """Background thread: process all file saves asynchronously."""
    while True:
        try:
            file_path, data, label = _save_queue.get(timeout=1)
            _atomic_save(file_path, data, label)
        except queue.Empty:
            pass
        except Exception as e:
            log.warning(f"Background save error: {e}")

# Start thread on import
_writer_thread = threading.Thread(target=_file_writer_thread, daemon=True)
_writer_thread.start()

# Replace sync saves with queue puts
def _save_cooldown_async(cooldown: dict):
    _save_queue.put((COOLDOWN_FILE, cooldown, "cooldown"))
```

**Testing:**
- Monitor I/O time; verify no blocking
- Verify saves still complete (check files after close)

---

## Fix #10: Re-Enable AI Fallback Chain [3 hours]

**File:** `core/ai_client.py`

**Problem:**
- Only NVIDIA is active tier
- If NVIDIA API down → bot stuck with HOLD signals → missed trades
- Comment says "Gemini exhausted" (but no explanation)

**Solution:**
```python
# Re-enable Gemini fallback (if credits restored) OR use Claude/OpenAI
if _GEMINI_KEY:
    _TIERS.append(("gemini", _GEMINI_URL, _GEMINI_KEY, _T2_MODEL, 60, "T2-Gemini-Pro"))

# Add OpenRouter as T3 fallback
if _OPENROUTER_KEY:
    _TIERS.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                   _OPENROUTER_KEY, "mistral/mixtral-8x7b", 45, "T3-OpenRouter"))

# Test fallback chain: verify > 1 tier
if len(_TIERS) < 2:
    log.warning("[AI] No fallback tier configured. Single point of failure!")
    log.info("[AI] To add fallback: set GEMINI_API_KEY or OPENROUTER_API_KEY in .env")
```

**Testing:**
- Disable NVIDIA key; verify Gemini fallback activates
- Simulate NVIDIA timeout; verify fallback chain activates

---

## Fix #11: Cache Scoring Weights [30 minutes]

**File:** `core/analyzer.py`

**Problem:**
- Weights config (1KB) read from disk every signal
- 1000 signals/day = 1000 disk reads

**Solution:**
```python
_weights_cache = {"data": None, "ts": 0, "ttl_s": 60}

def _load_weights(force_reload=False) -> dict:
    now = time.time()
    if force_reload or (now - _weights_cache["ts"]) > _weights_cache["ttl_s"]:
        w = json.loads((CONFIG_DIR / "scoring_weights.json").read_text())
        _weights_cache["data"] = w
        _weights_cache["ts"] = now
    return _weights_cache["data"]
```

**Testing:**
- Change scoring_weights.json; verify cache invalidates after 60s

---

## Fix #12: Cache Indicator Calculations [2 hours]

**File:** `core/analyzer.py`

**Problem:**
- All indicators recomputed per signal (even if same candle)
- pandas_ta computations are CPU-intensive

**Solution:**
```python
_indicator_cache = {}  # (symbol, tf, candle_hash) -> indicators

def _compute_indicators_cached(df, timeframe):
    # Hash last 3 candles to detect changes
    candle_hash = hash(tuple(df.iloc[-3:]["c"].values))
    key = (timeframe, candle_hash)
    
    if key in _indicator_cache:
        return _indicator_cache[key]
    
    indicators = self._compute_indicators(df)
    _indicator_cache[key] = indicators
    return indicators
```

**Testing:**
- Monitor CPU; verify reduction in pandas_ta calls

---

## Fix #13: SQLite Connection Pooling [2 hours]

**File:** `continuous_trader.py` (TradeMemory usage)

**Problem:**
- New SQLite connection per query
- Limited write concurrency → "database is locked" errors

**Solution:**
```python
# In learning/memory.py
class TradeMemory:
    def __init__(self):
        self.db_path = ROOT_DIR / "data" / "trades.db"
        # Enable WAL mode for concurrent access
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA timeout=10000;")  # 10s lock timeout
        conn.close()
        self.pool = [sqlite3.connect(str(self.db_path), timeout=10) for _ in range(3)]
        self.pool_idx = 0
    
    def get_connection(self):
        conn = self.pool[self.pool_idx % 3]
        self.pool_idx += 1
        return conn
```

**Testing:**
- Run concurrent outcome recording; verify no "database is locked"

---

## Fix #14: Volatility-Aware Trailing Stop [2 hours]

**File:** `continuous_trader.py`

**Problem:**
- Trailing stop uses fixed 40% lock regardless of ATR regime
- In high-vol: 40% is too tight → false exits
- In calm: 40% is too loose → gives back too much

**Solution:**
```python
def check_and_close_positions(...):
    for pos in positions:
        # Scale trailing stop based on volatility
        atr = regime_data.get("atr", 0)
        vol_state = regime_data.get("volatility_state", "NORMAL")
        
        if vol_state == "HIGH":
            trail_lock_pct = 0.60  # Loose: give more upside in volatile markets
        elif vol_state == "COMPRESSED":
            trail_lock_pct = 0.30  # Tight: protect gains in calm markets
        else:
            trail_lock_pct = 0.40  # Normal
        
        if peak >= trail_trigger and profit < peak * trail_lock_pct:
            should_close = True
```

**Testing:**
- Backtest with different volatility regimes; verify WR improvement

---

## Summary: Week 2 Timeline

| Hour | Task | Complexity |
|------|------|-----------|
| 1–2 | Stale price validation | Easy |
| 3–4 | Bridge connection test | Easy |
| 5–6 | ATR validation | Easy |
| 7–10 | Background file I/O thread | Medium |
| 11–14 | Re-enable AI fallback chain | Medium |
| 15–16 | Cache scoring weights | Easy |
| 17–19 | Cache indicator calculations | Medium |
| 20–22 | SQLite connection pooling | Medium |
| 23–25 | Volatility-aware trailing stop | Medium |

**Total: 25 hours** (can be parallelized across 2–3 developers)

---

## Success Metrics (Week 2)

- [ ] No stale price trades
- [ ] Bridge tests at startup (fail fast)
- [ ] ATR validation prevents fallback usage
- [ ] File I/O < 5ms per save (async)
- [ ] AI fallback chain works (tested)
- [ ] Indicator cache hit rate > 80%
- [ ] No "database is locked" errors
- [ ] Trailing stop adjusts with volatility

---

## Week 3 Optimization (if needed)

1. Remove hardcoded lot cap (0.50) after live validation
2. Implement Kelly sizing auto-tuning
3. Add confidence-based position averaging
4. Streaming indicator updates (don't wait for full candle)

---

**Week 1 ✅ → Week 2 🔄 → Week 3 🎯**
