"""
decision_logger.py — 4-stage structured analysis trace.

Writes one JSONL record per analysis cycle covering every stage of the pipeline:

  Stage 1  RAW_DATA      TradingView live indicators per timeframe (M15/H1/H4/D1)
  Stage 2  CALCULATIONS  Factor scores F1-F12, composite score, regime
  Stage 3  AI_CONTEXT    Summary of what was sent to Gemini (key numbers)
  Stage 4  DECISION      Final BUY/SELL/HOLD + confidence + reason

View with:
    python3 trace_viewer.py               # last 20 decisions
    python3 trace_viewer.py --symbol XAUUSD
    python3 trace_viewer.py --action BUY
    python3 trace_viewer.py --last 50
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("decision_logger")

TRACE_FILE  = Path(__file__).parent / "analysis_trace.jsonl"
MAX_RECORDS = 5000   # ~3 days at 60s interval × 2 symbols before rotation


def write_trace(cycle: int, symbol: str, session: str, signal_data: dict):
    """Append one complete analysis-cycle record to the JSONL trace file."""
    try:
        ind  = signal_data.get("indicators", {})
        fs   = signal_data.get("factor_scores", {})
        tv   = signal_data.get("_tv_raw", {})

        record = {
            "ts":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "cycle":   cycle,
            "symbol":  symbol,
            "session": session,

            # ── Stage 1: Raw TradingView data ─────────────────────────────
            "raw_data": {
                tf: {k: v for k, v in tfd.items()
                     if k in ("adx", "rsi", "ema_trend", "macd_signal",
                               "bb_position", "stoch_k", "stoch_d",
                               "stoch_cross", "williams_r", "atr", "price")}
                for tf, tfd in tv.items() if isinstance(tfd, dict)
            },

            # ── Stage 2: Indicator calculations ──────────────────────────
            "calculations": {
                "score":   signal_data.get("score", 0),
                "regime":  fs.get("adx_regime", ""),
                "h1_trend": signal_data.get("h1_trend", ""),
                "h4_trend": signal_data.get("h4_trend", ""),
                "d1_trend": signal_data.get("d1_trend", ""),
                "m15": {
                    "adx": ind.get("adx"),
                    "rsi": ind.get("rsi"),
                    "bb":  ind.get("bb_position"),
                    "macd": ind.get("macd_signal"),
                    "stoch_k": ind.get("stoch_k"),
                    "stoch_cross": ind.get("stoch_cross"),
                    "williams_r": ind.get("williams_r"),
                    "atr": ind.get("atr"),
                },
                "factor_scores": {k: v for k, v in fs.items()
                                  if k not in ("adx_regime", "bb_squeeze")},
                "fib_zone": signal_data.get("_fib_zone", ""),
            },

            # ── Stage 3: AI context summary ───────────────────────────────
            "ai_context": signal_data.get("_ai_context", ""),

            # ── Stage 4: Decision ─────────────────────────────────────────
            "decision": {
                "direction":  signal_data.get("direction", "HOLD"),
                "confidence": round(float(signal_data.get("confidence", 0)), 4),
                "reason":     signal_data.get("reason", ""),
            },
        }

        with open(TRACE_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

        _rotate_if_needed()

    except Exception as e:
        log.debug(f"Trace write error: {e}")


def _rotate_if_needed():
    try:
        if not TRACE_FILE.exists():
            return
        lines = TRACE_FILE.read_text().splitlines()
        if len(lines) > MAX_RECORDS:
            TRACE_FILE.write_text("\n".join(lines[-MAX_RECORDS:]) + "\n")
    except Exception:
        pass
