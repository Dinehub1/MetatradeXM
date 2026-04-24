#!/usr/bin/env python3
"""
glm51_cron.py — Every-10-min deep market analysis via NVIDIA GLM 5.1.

Collects ALL chart data (candles, indicators, Fibonacci, trade history,
open positions) and streams it to GLM 5.1 for comprehensive analysis.

Uses STREAMING to avoid timeout errors (GLM 5.1 needs ~30-60s for deep reasoning).

Usage:
    python3 scripts/glm51_cron.py              # run once
    python3 scripts/glm51_cron.py --loop       # run every 10 min
    python3 scripts/glm51_cron.py --interval 5 # custom interval (minutes)
"""

import os
import sys
import json
import time
import logging
import logging.handlers
import argparse
import signal
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ── Project root setup ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from core.paths import LOG_DIR, DATA_DIR, STATE_DIR

# ── Logging ──
LOG_FILE = LOG_DIR / "glm51_analysis.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GLM5.1] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("glm51")

# Silence noisy libs
for n in ("socketio", "engineio", "metaapi_cloud_sdk", "urllib3", "requests", "websocket"):
    logging.getLogger(n).setLevel(logging.WARNING)

# ── GLM 5.1 Config ──
NVIDIA_KEY = os.getenv("NVIDIA_API_KEY", "")
GLM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
GLM_MODEL = "z-ai/glm-5.1"
REPORTS_DIR = ROOT / "data" / "glm51_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Graceful shutdown ──
_stop = False
def _handle_sig(sig, frame):
    global _stop
    log.info("🛑 Shutdown signal — exiting after current cycle")
    _stop = True
signal.signal(signal.SIGINT, _handle_sig)
signal.signal(signal.SIGTERM, _handle_sig)


def _call_glm51_once(messages: list, max_tokens: int, timeout: int) -> str:
    """Single streaming call to GLM 5.1. Returns full response text."""
    import requests as req

    payload = {
        "model": GLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "top_p": 0.95,
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {NVIDIA_KEY}",
    }

    t0 = time.time()
    full_text = ""

    resp = req.post(GLM_URL, headers=headers, json=payload,
                    timeout=(15, timeout), stream=True)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    log.info(f"  Connected in {time.time()-t0:.1f}s — streaming...")

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    full_text += content
        except (json.JSONDecodeError, IndexError, KeyError):
            continue

    elapsed = time.time() - t0
    log.info(f"  GLM 5.1 responded: {len(full_text)} chars in {elapsed:.1f}s")
    return full_text


def call_glm51_streaming(prompt: str, system_prompt: str = "",
                          max_tokens: int = 4096, timeout: int = 300,
                          retries: int = 3) -> str:
    """Call GLM 5.1 with streaming + retry on transient errors (502/503/429)."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    last_err = None
    for attempt in range(retries):
        try:
            return _call_glm51_once(messages, max_tokens, timeout)
        except Exception as e:
            last_err = e
            err_str = str(e)
            is_transient = any(code in err_str for code in ["502", "503", "429", "504"])
            if is_transient and attempt < retries - 1:
                wait = (attempt + 1) * 10  # 10s, 20s, 30s
                log.warning(f"  ⚠️ GLM 5.1 transient error (attempt {attempt+1}/{retries}): {err_str[:80]}")
                log.info(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

    raise last_err  # should not reach here


def collect_market_data() -> dict:
    """Connect to broker, fetch ALL chart data, indicators, positions, history."""
    from continuous_trader import make_bridge, connect_with_retry, SYMBOLS
    from core.analyzer import MarketAnalyzer
    import concurrent.futures

    bridge = make_bridge()
    if not connect_with_retry(bridge, max_attempts=3):
        raise ConnectionError("Cannot connect to broker")

    analyzer = MarketAnalyzer(use_claude=False)  # no AI call — just indicators
    data = {"timestamp": datetime.now(timezone.utc).isoformat(), "symbols": {}}

    # Account info
    try:
        acct = bridge.get_account_info()
        data["account"] = {
            "balance": getattr(acct, "balance", 0),
            "equity": getattr(acct, "equity", 0),
            "margin": getattr(acct, "margin", 0),
            "free_margin": getattr(acct, "freeMargin", 0),
            "profit": getattr(acct, "profit", 0),
        }
    except Exception as e:
        data["account"] = {"error": str(e)}

    # Open positions
    try:
        positions = bridge.get_open_positions() or []
        data["open_positions"] = [{
            "symbol": getattr(p, "symbol", "?"),
            "type": "BUY" if getattr(p, "type", 1) == 0 else "SELL",
            "volume": getattr(p, "volume", 0),
            "profit": getattr(p, "profit", 0),
            "open_price": getattr(p, "price_open", 0),
            "current_price": getattr(p, "price_current", 0),
            "sl": getattr(p, "stopLoss", 0),
            "tp": getattr(p, "takeProfit", 0),
        } for p in positions]
    except Exception as e:
        data["open_positions"] = []

    # Per-symbol candles + indicators
    for sym_cfg in SYMBOLS:
        sym_key = sym_cfg["display"]
        broker_sym = sym_cfg["broker"]
        sym_data = {"config": sym_cfg}

        # Fetch candles for all timeframes
        tf_candles = {}
        for tf in ["M1", "M15", "H1", "H4", "D1"]:
            count = 60 if tf == "M1" else 200
            try:
                with concurrent.futures.ThreadPoolExecutor(1) as ex:
                    df = ex.submit(bridge.get_candles, broker_sym, tf, count).result(timeout=15)
                if df is not None and len(df) > 10:
                    tf_candles[tf] = df
                    # Store last N candles as plain data for prompt
                    n = 15 if tf == "M1" else 20 if tf == "M15" else 10
                    tail = df.tail(n)
                    cols = [c for c in ["time", "o", "h", "l", "c", "vol"] if c in tail.columns]
                    sym_data[f"candles_{tf}"] = tail[cols].to_string(index=False)
                    sym_data[f"candles_{tf}_count"] = len(df)
            except Exception as e:
                sym_data[f"candles_{tf}"] = f"Error: {e}"

        # Compute indicators per timeframe
        for tf_name, tf_key in [("M15", "M15"), ("H1", "H1"), ("H4", "H4"), ("D1", "D1")]:
            if tf_key in tf_candles and len(tf_candles[tf_key]) >= 30:
                try:
                    ind = analyzer._compute_indicators(tf_candles[tf_key])
                    sym_data[f"indicators_{tf_name}"] = ind
                except Exception as e:
                    sym_data[f"indicators_{tf_name}"] = {"error": str(e)}

        # Fibonacci levels
        if "M15" in tf_candles:
            try:
                fib = analyzer.compute_fibonacci_levels(tf_candles["M15"], lookback=100)
                if fib:
                    sym_data["fibonacci"] = {
                        "swing_high": fib["swing_high"],
                        "swing_low": fib["swing_low"],
                        "trend": fib["trend"],
                        "retracements": fib["retracements"],
                        "extensions": fib["extensions"],
                        "zone_label": fib["zone_label"],
                        "at_key_level": fib["at_key_level"],
                    }
            except Exception:
                pass

        # Factor scores (multi-TF confluence)
        if all(k in tf_candles for k in ["M15", "H1", "H4"]):
            try:
                ind_m15 = analyzer._compute_indicators(tf_candles["M15"])
                ind_h1 = analyzer._compute_indicators(tf_candles["H1"])
                ind_h4 = analyzer._compute_indicators(tf_candles["H4"])
                ind_d1 = analyzer._compute_indicators(tf_candles["D1"]) if "D1" in tf_candles and len(tf_candles["D1"]) >= 30 else None
                scores = analyzer._get_factor_scores(ind_m15, ind_h1, ind_h4, ind_d1)
                mtf = analyzer._multi_tf_signal(ind_m15, ind_h1, ind_h4, ind_d1)
                sym_data["factor_scores"] = scores
                sym_data["mtf_signal"] = {
                    "direction": mtf["direction"],
                    "confidence": mtf["confidence"],
                    "score": mtf["score"],
                    "reason": mtf["reason"],
                }
            except Exception as e:
                sym_data["factor_scores_error"] = str(e)

        # Current tick
        try:
            tick = bridge.get_tick(broker_sym)
            sym_data["tick"] = {"ask": tick.ask, "bid": tick.bid, "spread": round(tick.ask - tick.bid, 5)}
        except Exception:
            pass

        data["symbols"][sym_key] = sym_data

    # Trade history (from memory DB)
    try:
        from learning.memory import TradeMemory
        mem = TradeMemory()
        for sym_cfg in SYMBOLS:
            ctx = mem.prefetch_context(sym_cfg["display"])
            if ctx:
                data["symbols"][sym_cfg["display"]]["trade_history"] = ctx
    except Exception:
        pass

    return data


def build_analysis_prompt(market_data: dict) -> str:
    """Build comprehensive analysis prompt from collected data."""
    lines = [
        f"=== COMPREHENSIVE MARKET ANALYSIS — {market_data['timestamp']} ===\n",
    ]

    # Account
    acct = market_data.get("account", {})
    if not acct.get("error"):
        lines.append(f"ACCOUNT: Balance=${acct.get('balance',0):.2f}  Equity=${acct.get('equity',0):.2f}  "
                     f"Margin=${acct.get('margin',0):.2f}  P&L=${acct.get('profit',0):.2f}")

    # Open positions
    positions = market_data.get("open_positions", [])
    if positions:
        lines.append(f"\nOPEN POSITIONS ({len(positions)}):")
        for p in positions:
            lines.append(f"  {p['symbol']} {p['type']} vol={p['volume']} "
                        f"open={p['open_price']} current={p['current_price']} "
                        f"P&L=${p['profit']:.2f} SL={p['sl']} TP={p['tp']}")
    else:
        lines.append("\nNO OPEN POSITIONS")

    # Per-symbol data
    for sym, sym_data in market_data.get("symbols", {}).items():
        lines.append(f"\n{'='*60}")
        lines.append(f"=== {sym} ===")

        # Tick
        tick = sym_data.get("tick", {})
        if tick:
            lines.append(f"Price: Ask={tick.get('ask',0):.5f}  Bid={tick.get('bid',0):.5f}  Spread={tick.get('spread',0):.5f}")

        # Indicators per TF
        for tf in ["D1", "H4", "H1", "M15"]:
            ind = sym_data.get(f"indicators_{tf}")
            if ind and not ind.get("error"):
                lines.append(f"\n--- {tf} Indicators ---")
                lines.append(f"  EMA: 20={ind.get('ema20',0):.5f}  50={ind.get('ema50',0):.5f}  200={ind.get('ema200',0):.5f}  Trend={ind.get('ema_trend','?')}")
                lines.append(f"  RSI: {ind.get('rsi',0):.1f}  ADX: {ind.get('adx',0):.0f}  +DI={ind.get('plus_di',0):.0f}  -DI={ind.get('minus_di',0):.0f}")
                lines.append(f"  MACD: {ind.get('macd_signal','?')}  hist={ind.get('macd_hist',0):.6f}")
                lines.append(f"  Stoch: K={ind.get('stoch_k',0):.0f}  D={ind.get('stoch_d',0):.0f}  cross={ind.get('stoch_cross','?')}")
                lines.append(f"  BB: {ind.get('bb_position','?')}  squeeze={ind.get('bb_squeeze',False)}")
                lines.append(f"  ATR: {ind.get('atr',0):.5f}  W%R: {ind.get('williams_r',0):.0f}  VolRatio: {ind.get('vol_ratio',0):.2f}")

        # Factor scores
        fs = sym_data.get("factor_scores", {})
        if fs:
            lines.append(f"\n--- Factor Scores (signed: +=bullish, -=bearish) ---")
            for k, v in fs.items():
                if isinstance(v, (int, float)):
                    lines.append(f"  {k}: {v:+.1f}")
                else:
                    lines.append(f"  {k}: {v}")

        # MTF signal
        mtf = sym_data.get("mtf_signal", {})
        if mtf:
            lines.append(f"\n--- Multi-TF Signal ---")
            lines.append(f"  Direction: {mtf.get('direction','?')}  Confidence: {mtf.get('confidence',0):.0%}  Score: {mtf.get('score',0):+.1f}")
            lines.append(f"  Reason: {mtf.get('reason','')}")

        # Fibonacci
        fib = sym_data.get("fibonacci", {})
        if fib:
            lines.append(f"\n--- Fibonacci Levels (swing {fib.get('swing_low',0):.3f} → {fib.get('swing_high',0):.3f}, trend: {fib.get('trend','?')}) ---")
            ret = fib.get("retracements", {})
            for r, p in ret.items():
                lines.append(f"  {r}% = {p}")
            lines.append(f"  Zone: {fib.get('zone_label','')}")

        # Candles (M15 — most recent)
        candles_m15 = sym_data.get("candles_M15", "")
        if candles_m15:
            lines.append(f"\n--- Recent M15 Candles (last 20) ---")
            lines.append(candles_m15)

        # M1 micro-structure
        candles_m1 = sym_data.get("candles_M1", "")
        if candles_m1:
            lines.append(f"\n--- Recent M1 Candles (last 15) ---")
            lines.append(candles_m1)

        # H1 candles
        candles_h1 = sym_data.get("candles_H1", "")
        if candles_h1:
            lines.append(f"\n--- Recent H1 Candles (last 10) ---")
            lines.append(candles_h1)

        # Trade history
        hist = sym_data.get("trade_history", "")
        if hist:
            lines.append(f"\n--- Recent Trade History ---")
            lines.append(hist)

    return "\n".join(lines)


GLM_SYSTEM_PROMPT = """You are an elite quantitative trading analyst with 25+ years of experience in precious metals (Gold XAUUSD, Silver XAGUSD).

You receive COMPLETE market data dumps every 10 minutes. Your job is to provide:

1. **MARKET REGIME ASSESSMENT**: Is the market trending, ranging, or transitioning? What phase of the trend?
2. **MULTI-TIMEFRAME ANALYSIS**: How do D1, H4, H1, M15 align? Where are the conflicts?
3. **KEY LEVELS**: Identify the most important support/resistance, Fibonacci, and pivot levels.
4. **PATTERN RECOGNITION**: Any developing chart patterns, divergences, or candlestick formations?
5. **RISK ASSESSMENT**: Current position risk, drawdown potential, and volatility regime.
6. **ACTIONABLE RECOMMENDATIONS**: Specific entry/exit levels, stop-loss placement, and confidence.
7. **10-MINUTE OUTLOOK**: What to watch for in the next 10 minutes.

Be thorough but structured. Use concrete price levels and percentages.
Flag any WARNINGS (divergences, exhaustion, news risk windows).

Format your response as structured markdown with clear headers."""


def run_analysis_cycle():
    """Run one full analysis cycle: collect data → GLM 5.1 → save report."""
    cycle_start = time.time()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    log.info("=" * 60)
    log.info(f"🔄 GLM 5.1 Analysis Cycle — {ts}")
    log.info("=" * 60)

    # Step 1: Collect market data
    log.info("📊 Step 1: Collecting market data...")
    try:
        market_data = collect_market_data()
        log.info(f"  ✅ Data collected: {len(market_data.get('symbols', {}))} symbols, "
                f"{len(market_data.get('open_positions', []))} open positions")
    except Exception as e:
        log.error(f"  ❌ Data collection failed: {e}")
        traceback.print_exc()
        return

    # Step 2: Build prompt
    log.info("📝 Step 2: Building analysis prompt...")
    prompt = build_analysis_prompt(market_data)
    log.info(f"  Prompt size: {len(prompt):,} chars")

    # Step 3: Call GLM 5.1 (streaming)
    log.info("🤖 Step 3: Calling GLM 5.1 (streaming)...")
    try:
        analysis = call_glm51_streaming(
            prompt=prompt,
            system_prompt=GLM_SYSTEM_PROMPT,
            max_tokens=4096,
            timeout=300,
        )
    except Exception as e:
        log.error(f"  ❌ GLM 5.1 failed: {e}")
        traceback.print_exc()
        return

    if not analysis.strip():
        log.warning("  ⚠️ Empty response from GLM 5.1")
        return

    # Step 4: Save report
    report_file = REPORTS_DIR / f"analysis_{ts}.md"
    report_content = f"""# GLM 5.1 Market Analysis — {ts}

**Generated**: {datetime.now(timezone.utc).isoformat()}
**Model**: {GLM_MODEL}
**Data Points**: {len(prompt):,} chars of market data

---

{analysis}

---

## Raw Data Summary

- Account: Balance=${market_data.get('account',{}).get('balance',0):.2f}
- Open Positions: {len(market_data.get('open_positions',[]))}
- Symbols Analyzed: {', '.join(market_data.get('symbols',{}).keys())}
"""
    report_file.write_text(report_content)
    log.info(f"  📄 Report saved: {report_file}")

    # Also save latest to a fixed path for easy access
    latest = REPORTS_DIR / "latest_analysis.md"
    latest.write_text(report_content)

    elapsed = time.time() - cycle_start
    log.info(f"✅ Cycle complete in {elapsed:.1f}s")
    log.info(f"  Preview: {analysis[:200]}...")

    # Trim old reports (keep last 144 = 24 hours at 10-min intervals)
    try:
        reports = sorted(REPORTS_DIR.glob("analysis_*.md"))
        if len(reports) > 144:
            for old in reports[:-144]:
                old.unlink()
                log.debug(f"  Cleaned old report: {old.name}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="GLM 5.1 Market Analysis Cron")
    parser.add_argument("--loop", action="store_true", help="Run continuously every N minutes")
    parser.add_argument("--interval", type=int, default=10, help="Minutes between analyses (default: 10)")
    args = parser.parse_args()

    if not NVIDIA_KEY:
        log.error("❌ NVIDIA_API_KEY not set in .env — cannot run GLM 5.1 analysis")
        sys.exit(1)

    log.info(f"🚀 GLM 5.1 Deep Analysis {'(loop mode)' if args.loop else '(single run)'}")
    log.info(f"   Model: {GLM_MODEL}")
    log.info(f"   Reports: {REPORTS_DIR}")
    if args.loop:
        log.info(f"   Interval: every {args.interval} minutes")

    if args.loop:
        while not _stop:
            try:
                run_analysis_cycle()
            except Exception as e:
                log.error(f"Cycle error: {e}")
                traceback.print_exc()

            if _stop:
                break
            log.info(f"💤 Sleeping {args.interval} minutes until next cycle...")
            for _ in range(args.interval * 60):
                if _stop:
                    break
                time.sleep(1)
    else:
        run_analysis_cycle()


if __name__ == "__main__":
    main()
