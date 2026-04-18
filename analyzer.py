"""
MarketAnalyzer — UPGRADED
  - Multi-timeframe analysis: M15 entry, H1 context, H4 trend
  - Indicators: RSI, EMA, MACD, Bollinger, ATR, ADX, Stochastic, Williams %R
  - Session detection: London, New York, Asian, Off-hours
  - Confluence scoring: only trade when multiple timeframes agree
  - Ollama/minimax AI with rich system prompt and structured output
"""

import json
import logging
import os
import re
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from ai_client import ask_gemini, ask_openrouter  # Gemini primary, OpenRouter fallback

log = logging.getLogger("analyzer")

SYSTEM_PROMPT = """You are an expert precious metals trader specialising in XAUUSD (Gold) and XAGUSD (Silver).
You have 20+ years of experience trading commodities and forex.
You use multi-timeframe analysis: D1/H4 for trend direction, H1 for context, M15 for entry timing.
You are disciplined — you only enter when multiple timeframes align AND ADX confirms trend strength.

CORE TRADING PHILOSOPHY (Trading in the Zone — Mark Douglas):
- Think in PROBABILITIES, not certainties. You never know which trade wins — you only know your edge.
- Every moment in the market is unique. Past wins do not guarantee this trade wins.
- Your edge is a higher probability of one thing over another — expressed in confidence score.
- A 0.70 confidence trade still loses 30% of the time. Accept this. Execute consistently.
- NEVER over-trade to recover losses. Each trade stands alone on its own merit.
- HOLD is a valid and profitable decision. Patience IS a position.

FIBONACCI RULES (mandatory for entry decisions):
- Price at 61.8% retracement (Golden Ratio) = highest probability reversal zone. Boost confidence.
- Price at 38.2% or 50% retracement = strong support/resistance. Valid entry zone.
- Price at 78.6% retracement = deep pullback, potential trend continuation or exhaustion.
- Price ABOVE the 100% extension = trend exhausted. DO NOT chase. HOLD or wait for pullback.
- Entry at Fibonacci confluence (Fib level + BB extreme + RSI extreme) = maximum edge.
- If price is between Fibonacci levels with no confluence = wait. HOLD.

TECHNICAL RULES:
- NEVER trade against the D1/H4 trend unless it is a strong counter-trend reversal setup.
- ADX > 25 required for trend trades. ADX < 15 = ranging market, use RSI/Stoch/Fib reversals ONLY.
- ADX 15-25 = developing trend — require 3+ confirming factors before entry.
- In bearish trends: -DI > +DI confirms sell pressure. In bullish: +DI > -DI.
- Gold is most volatile during London open (07:00 UTC) and NY open (13:00 UTC).
- HOLD when signals are mixed, ADX < 10, or ATR < 5th percentile (dead market).
- RANGING MARKET (ADX < 15): ONLY enter at Fibonacci retracement levels OR BB extremes with RSI confirmation.

Respond with ONLY raw JSON: {"direction": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "1-2 sentences"}"""



class MarketAnalyzer:
    def __init__(self, use_claude: bool = True):
        self.use_claude = use_claude
        self._nemotron_cache: dict = {}  # symbol -> {"ts": float, "context": str}

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, candles, tick, symbol: str, memory_context: str = "",
                tv_indicators: dict = None) -> dict:
        """
        candles: single DataFrame (M15) OR dict {"M15": df, "H1": df, "H4": df}
        memory_context: optional past trade memory for AI reasoning
        Returns signal dict with direction, confidence, reason, indicators, session
        """
        # Normalise input: accept both single df and multi-tf dict
        if isinstance(candles, pd.DataFrame):
            tf_data = {"M15": candles, "H1": candles, "H4": candles}
        else:
            tf_data = candles

        ind_m15 = self._compute_indicators(tf_data["M15"])
        ind_h1  = self._compute_indicators(tf_data.get("H1", tf_data["M15"]))
        ind_h4  = self._compute_indicators(tf_data.get("H4", tf_data["M15"]))
        ind_d1  = self._compute_indicators(tf_data["D1"]) if "D1" in tf_data and len(tf_data["D1"]) >= 30 else None

        # ── TradingView live override: replace broker-computed values with TV data ──
        # TV indicators are sourced directly from TradingView (institutional data quality).
        # Only override fields that pass sanity checks — startup/stale values are rejected.
        if tv_indicators:
            tv_src = {"M15": ind_m15, "H1": ind_h1, "H4": ind_h4}
            tv_count = 0
            for tf_label, broker_ind in tv_src.items():
                tv_tf = tv_indicators.get(tf_label)
                if tv_tf:
                    updated = self._apply_tv_override(broker_ind, tv_tf)
                    if tf_label == "M15":
                        ind_m15 = updated
                    elif tf_label == "H1":
                        ind_h1 = updated
                    elif tf_label == "H4":
                        ind_h4 = updated
                    tv_count += 1
            if ind_d1 and tv_indicators.get("D1"):
                ind_d1 = self._apply_tv_override(ind_d1, tv_indicators["D1"])
                tv_count += 1
            if tv_count:
                log.debug(f"[TV] {symbol}: live indicators applied for {tv_count} timeframes")

        session     = self._get_session()
        weights     = self._load_weights()
        base_signal = self._multi_tf_signal(ind_m15, ind_h1, ind_h4, ind_d1, weights=weights)
        base_signal["session"] = session

        # ── F12: Fibonacci proximity (Trading in the Zone — enter at edges) ──
        m15_df = tf_data.get("M15")
        if m15_df is not None and len(m15_df) >= 20:
            fib_data   = self.compute_fibonacci_levels(m15_df, lookback=100)
            f12_raw    = self._compute_fib_factor(m15_df, fib_data=fib_data)
            f12_scored = round(f12_raw * weights.get("f12_fibonacci", 0.9), 1)
            base_signal.setdefault("factor_scores", {})["f12_fibonacci"] = f12_scored
            base_signal["score"] = round(base_signal.get("score", 0) + f12_scored, 1)
            base_signal["fibonacci_data"] = fib_data

        if self.use_claude:
            research_ctx = self._fetch_nemotron_research(symbol, base_signal)
            signal = self._ai_reasoning(symbol, tick, ind_m15, ind_h1, ind_h4,
                                        base_signal, tf_data["M15"],
                                        memory_context=memory_context,
                                        research_context=research_ctx,
                                        d1=ind_d1, d1_df=tf_data.get("D1"))
        else:
            signal = base_signal

        signal["indicators"] = ind_m15
        signal["h1_trend"]   = ind_h1["ema_trend"]
        signal["h4_trend"]   = ind_h4["ema_trend"]
        signal["d1_trend"]   = ind_d1["ema_trend"] if ind_d1 else "UNKNOWN"
        signal["session"]    = session
        return signal

    # ── TradingView live data integration ─────────────────────────────────────

    def _apply_tv_override(self, broker_ind: dict, tv_ind: dict) -> dict:
        """
        Merge TradingView live indicators on top of broker-computed values.
        TradingView is the primary source of truth — it uses exchange-grade data.
        Sanity checks reject startup garbage (ADX=0, RSI=100, etc.).
        """
        if not tv_ind:
            return broker_ind
        merged = dict(broker_ind)
        for key, val in tv_ind.items():
            if val is None:
                continue
            # Per-field sanity gates
            if key == "adx" and (val < 3 or val > 100):
                continue   # ADX=0 = server not ready; ADX>100 = impossible
            if key == "rsi" and (val >= 99.5 or val <= 0.5):
                continue   # RSI=100 or 0 = not enough bar history
            if key == "ema_trend" and val not in ("BULLISH", "BEARISH", "MILD_BULL", "MILD_BEAR", "MIXED"):
                continue   # "undefined" / null from JS
            if key in ("plus_di", "minus_di") and val < 0:
                continue
            merged[key] = val
        return merged

    def _tv_to_ind(self, tv_tf: dict) -> dict:
        """Convert TV timeframe dict to the indicator format _get_factor_scores expects."""
        adx = tv_tf.get("adx", 0) or 0
        return {
            "price":                tv_tf.get("price", 0),
            "price_change":         tv_tf.get("price_change", 0),
            "ema_trend":            tv_tf.get("ema_trend", "MIXED"),
            "ema20":                tv_tf.get("ema20", 0),
            "ema50":                tv_tf.get("ema50", 0),
            "ema200":               tv_tf.get("ema200", 0),
            "rsi":                  tv_tf.get("rsi", 50),
            "macd_signal":          tv_tf.get("macd_signal", "NEUTRAL"),
            "macd_hist":            tv_tf.get("macd_hist", 0),
            "bb_position":          tv_tf.get("bb_position", "ABOVE_MID"),
            "bb_squeeze":           tv_tf.get("bb_squeeze", False),
            "adx":                  adx,
            "plus_di":              tv_tf.get("plus_di", 0),
            "minus_di":             tv_tf.get("minus_di", 0),
            "trend_strong":         adx > 25,
            "stoch_k":              tv_tf.get("stoch_k", 50),
            "stoch_d":              tv_tf.get("stoch_d", 50),
            "stoch_cross":          tv_tf.get("stoch_cross", "NONE"),
            "williams_r":           tv_tf.get("williams_r", -50),
            "atr":                  tv_tf.get("atr", 0),
            "vol_ratio":            tv_tf.get("vol_ratio", 1.0),
            "candle_pattern_score": 0,  # TV streams aggregated indicators, not raw bars
        }

    def analyze_from_tv(self, tv_tfs: dict, tick, symbol: str,
                        m15_candles=None, memory_context: str = "") -> dict:
        """TV-direct path: skip broker candle fetch and indicator computation entirely.

        tv_tfs: dict with keys M15, H1, H4 (and optionally D1) from tv_client
        m15_candles: optional broker M15 DataFrame — used only for Fibonacci (F12)
        """
        ind_m15 = self._tv_to_ind(tv_tfs.get("M15", {}))
        ind_h1  = self._tv_to_ind(tv_tfs.get("H1", {}))
        ind_h4  = self._tv_to_ind(tv_tfs.get("H4", {}))
        ind_d1  = self._tv_to_ind(tv_tfs["D1"]) if tv_tfs.get("D1") else None

        session     = self._get_session()
        weights     = self._load_weights()
        base_signal = self._multi_tf_signal(ind_m15, ind_h1, ind_h4, ind_d1, weights=weights)
        base_signal["session"] = session

        if m15_candles is not None and len(m15_candles) >= 20:
            fib_data   = self.compute_fibonacci_levels(m15_candles, lookback=100)
            f12_raw    = self._compute_fib_factor(m15_candles, fib_data=fib_data)
            f12_scored = round(f12_raw * weights.get("f12_fibonacci", 0.9), 1)
            base_signal.setdefault("factor_scores", {})["f12_fibonacci"] = f12_scored
            base_signal["score"] = round(base_signal.get("score", 0) + f12_scored, 1)
            base_signal["fibonacci_data"] = fib_data

        if self.use_claude:
            research_ctx = self._fetch_nemotron_research(symbol, base_signal)
            signal = self._ai_reasoning(symbol, tick, ind_m15, ind_h1, ind_h4,
                                        base_signal, m15_candles,
                                        memory_context=memory_context,
                                        research_context=research_ctx,
                                        d1=ind_d1, d1_df=None)
        else:
            signal = base_signal

        signal["indicators"] = ind_m15
        signal["h1_trend"]   = ind_h1.get("ema_trend", "UNKNOWN")
        signal["h4_trend"]   = ind_h4.get("ema_trend", "UNKNOWN")
        signal["d1_trend"]   = ind_d1.get("ema_trend", "UNKNOWN") if ind_d1 else "UNKNOWN"
        signal["session"]    = session
        return signal

    # ── Session detection ─────────────────────────────────────────────────────

    def _fetch_nemotron_research(self, symbol: str, base_signal: dict) -> str:
        # Placeholder — Nvidia NIM deep research integration (TBD)
        return ""

    def _get_session(self) -> str:
        now = datetime.now(timezone.utc)
        weekday, hour = now.weekday(), now.hour
        if weekday == 5 or (weekday == 4 and hour >= 22) or (weekday == 6 and hour < 22):
            return "MARKET_CLOSED"
        # Mirrors is_forex_market_open() in continuous_trader.py — keep in sync
        if  8 <= hour < 13: return "LONDON"
        if 13 <= hour < 17: return "LONDON_NY_OVERLAP"
        if 17 <= hour < 22: return "NEW_YORK"
        return "ASIAN"

    # ── Indicator suite ───────────────────────────────────────────────────────

    def _candle_pattern_score(self, df: pd.DataFrame) -> int:
        """Detect simple candlestick patterns on the last 2 candles. Returns signed score."""
        if len(df) < 3:
            return 0
        o, h, l, c = df["o"].values, df["h"].values, df["l"].values, df["c"].values
        score = 0
        body = abs(c[-1] - o[-1])
        prev_body = abs(c[-2] - o[-2])
        full_range = h[-1] - l[-1] or 0.0001

        # Engulfing (strong reversal)
        bearish_engulf = (c[-2] > o[-2]) and (o[-1] > c[-2]) and (c[-1] < o[-2])
        bullish_engulf = (c[-2] < o[-2]) and (o[-1] < c[-2]) and (c[-1] > o[-2])
        if bearish_engulf: score -= 6
        if bullish_engulf: score += 6

        # Pin bar / hammer (long wick, small body at opposite end)
        lower_wick = o[-1] - l[-1] if c[-1] > o[-1] else c[-1] - l[-1]
        upper_wick = h[-1] - c[-1] if c[-1] > o[-1] else h[-1] - o[-1]
        if lower_wick > 2 * body and upper_wick < body:  # bullish pin/hammer
            score += 4
        if upper_wick > 2 * body and lower_wick < body:  # bearish shooting star
            score -= 4

        # Doji (indecision — reduce score slightly toward 0)
        if body < full_range * 0.1 and full_range > 0:
            score = int(score * 0.5)

        return score

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        close = df["c"].values
        high  = df["h"].values
        low   = df["l"].values

        rsi_val  = self._rsi(close, 14)
        adx_val, plus_di, minus_di = self._adx(high, low, close, 14)
        stoch_k, stoch_d = self._stochastic(high, low, close, 14, 3)
        will_r   = self._williams_r(high, low, close, 14)
        candle_score = self._candle_pattern_score(df)

        return {
            "rsi":          round(rsi_val, 2),
            "ema20":        round(self._ema(close, 20)[-1], 5),
            "ema50":        round(self._ema(close, 50)[-1], 5),
            "ema200":       round(self._ema(close, 200)[-1], 5),
            "ema_trend":    self._ema_trend(close),
            "macd_signal":  self._macd_cross(close),
            "macd_hist":    round(self._macd_histogram(close), 6),
            "bb_position":  self._bb_position(close),
            "bb_squeeze":   self._bb_squeeze(close),
            "atr":          round(self._atr(high, low, close, 14), 5),
            "adx":          round(adx_val, 1),
            "plus_di":      round(plus_di, 1),
            "minus_di":     round(minus_di, 1),
            "trend_strong": adx_val > 25,
            "stoch_k":      round(stoch_k, 1),
            "stoch_d":      round(stoch_d, 1),
            "stoch_cross":  self._stoch_cross(high, low, close),
            "williams_r":           round(will_r, 1),
            "price":                round(close[-1], 5),
            "price_change":         round((close[-1] - close[-20]) / close[-20] * 100, 3),
            "vol_ratio":            self._volume_ratio(df),
            "candle_pattern_score": candle_score,
        }

    # ── Individual indicators ─────────────────────────────────────────────────

    def _rsi(self, close: np.ndarray, period: int = 14) -> float:
        delta = np.diff(close)
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    def _ema(self, close: np.ndarray, period: int) -> np.ndarray:
        k   = 2 / (period + 1)
        ema = np.zeros(len(close))
        ema[0] = close[0]
        for i in range(1, len(close)):
            ema[i] = close[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _ema_trend(self, close: np.ndarray) -> str:
        e20, e50, e200 = self._ema(close, 20)[-1], self._ema(close, 50)[-1], self._ema(close, 200)[-1]
        if e20 > e50 > e200:   return "BULLISH"
        if e20 < e50 < e200:   return "BEARISH"
        if e20 > e50:          return "MILD_BULL"
        if e20 < e50:          return "MILD_BEAR"
        return "MIXED"

    def _macd_cross(self, close: np.ndarray) -> str:
        macd   = self._ema(close, 12) - self._ema(close, 26)
        signal = self._ema(macd, 9)
        if macd[-1] > signal[-1] and macd[-2] <= signal[-2]: return "BULLISH_CROSS"
        if macd[-1] < signal[-1] and macd[-2] >= signal[-2]: return "BEARISH_CROSS"
        if macd[-1] > signal[-1]: return "BULLISH"
        if macd[-1] < signal[-1]: return "BEARISH"
        return "NONE"

    def _macd_histogram(self, close: np.ndarray) -> float:
        macd   = self._ema(close, 12) - self._ema(close, 26)
        signal = self._ema(macd, 9)
        return float(macd[-1] - signal[-1])

    def _bb_position(self, close: np.ndarray, period: int = 20) -> str:
        sma   = np.mean(close[-period:])
        std   = np.std(close[-period:])
        upper = sma + 2 * std
        lower = sma - 2 * std
        price = close[-1]
        if price >= upper:  return "ABOVE_UPPER"
        if price <= lower:  return "BELOW_LOWER"
        if price > sma:     return "ABOVE_MID"
        return "BELOW_MID"

    def _bb_squeeze(self, close: np.ndarray, period: int = 20) -> bool:
        """True when BB width is in lowest 20% of recent range = volatility compression."""
        if len(close) < period + 20:
            return False
        widths = []
        for i in range(-20, 0):
            sl = close[i - period: i] if i != 0 else close[-period:]
            widths.append(np.std(sl) * 4)
        return widths[-1] < np.percentile(widths, 20)

    def _atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        tr = np.maximum(high[1:] - low[1:],
             np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        return float(np.mean(tr[-period:]))

    def _adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
        """Returns (ADX, +DI, -DI)."""
        n   = len(close)
        tr  = np.maximum(high[1:] - low[1:],
              np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        pdm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                       np.maximum(high[1:] - high[:-1], 0), 0)
        mdm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                       np.maximum(low[:-1] - low[1:], 0), 0)

        atr_s = pdm_s = mdm_s = 0.0
        adx_vals = []
        dx_vals  = []

        for i in range(len(tr)):
            if i < period:
                atr_s += tr[i]; pdm_s += pdm[i]; mdm_s += mdm[i]
            else:
                atr_s = atr_s - atr_s / period + tr[i]
                pdm_s = pdm_s - pdm_s / period + pdm[i]
                mdm_s = mdm_s - mdm_s / period + mdm[i]
            if i >= period - 1 and atr_s > 0:
                pdi = 100 * pdm_s / atr_s
                mdi = 100 * mdm_s / atr_s
                dx  = 100 * abs(pdi - mdi) / (pdi + mdi + 1e-9)
                dx_vals.append(dx)

        adx = float(np.mean(dx_vals[-period:])) if len(dx_vals) >= period else 0.0
        pdi = 100 * pdm_s / (atr_s + 1e-9)
        mdi = 100 * mdm_s / (atr_s + 1e-9)
        return adx, pdi, mdi

    def _stochastic(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    k_period: int = 14, d_period: int = 3):
        k_vals = []
        for i in range(k_period - 1, len(close)):
            lo = np.min(low[i - k_period + 1: i + 1])
            hi = np.max(high[i - k_period + 1: i + 1])
            k  = 100 * (close[i] - lo) / (hi - lo + 1e-9)
            k_vals.append(k)
        k_arr = np.array(k_vals)
        d_arr = np.convolve(k_arr, np.ones(d_period) / d_period, mode="valid")
        return float(k_arr[-1]), float(d_arr[-1])

    def _stoch_cross(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> str:
        try:
            k1, d1 = self._stochastic(high, low, close)
            k2, d2 = self._stochastic(high[:-1], low[:-1], close[:-1])
            if k1 > d1 and k2 <= d2 and k1 < 80:  return "BULLISH"
            if k1 < d1 and k2 >= d2 and k1 > 20:  return "BEARISH"
        except Exception:
            pass
        return "NONE"

    def _williams_r(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        hi = np.max(high[-period:])
        lo = np.min(low[-period:])
        return -100 * (hi - close[-1]) / (hi - lo + 1e-9)

    def _volume_ratio(self, df: pd.DataFrame) -> float:
        """Current volume vs 20-bar average. >1.5 = high volume."""
        if "vol" not in df.columns or len(df) < 21:
            return 1.0
        avg = df["vol"].iloc[-21:-1].mean()
        return round(float(df["vol"].iloc[-1]) / (avg + 1e-9), 2)

    def compute_fibonacci_levels(self, df: pd.DataFrame, lookback: int = 100) -> dict:
        """
        Detect the most recent swing high and swing low over `lookback` bars
        and return Fibonacci retracement + extension levels with price proximity context.

        Swing detection: a pivot high is a bar whose high is the highest in a ±5 bar window.
        Same logic for pivot low.

        Returns dict with keys:
            swing_high, swing_low, trend,
            retracements: {23.6, 38.2, 50.0, 61.8, 78.6}  (price levels)
            extensions:   {127.2, 161.8, 200.0, 261.8}     (price levels)
            nearest_level: {"ratio": float, "price": float, "distance_pips": float, "type": str}
            at_key_level: bool  (True if price within 0.3% of any retracement level)
            zone_label: str  (human-readable context for AI prompt)
        """
        if len(df) < 20:
            return {}

        window = min(lookback, len(df))
        bars = df.tail(window)
        highs = bars["h"].values
        lows  = bars["l"].values
        close = bars["c"].values
        current_price = float(close[-1])

        # --- Swing pivot detection (±5 bar window) ---
        pivot = 5
        swing_high_idx = -1
        swing_low_idx  = -1
        swing_high_val = -np.inf
        swing_low_val  =  np.inf

        for i in range(pivot, len(highs) - pivot):
            if highs[i] == np.max(highs[i - pivot: i + pivot + 1]):
                if highs[i] > swing_high_val:
                    swing_high_val = highs[i]
                    swing_high_idx = i
            if lows[i] == np.min(lows[i - pivot: i + pivot + 1]):
                if lows[i] < swing_low_val:
                    swing_low_val = lows[i]
                    swing_low_idx = i

        if swing_high_val == -np.inf or swing_low_val == np.inf:
            return {}

        swing_range = swing_high_val - swing_low_val
        if swing_range < 1e-6:
            return {}

        # Determine trend direction: which pivot came LAST?
        trend = "UP" if swing_low_idx > swing_high_idx else "DOWN"

        # --- Retracement levels (price pulls back INTO the swing) ---
        # For UP trend: fib retracements are between swing_low and swing_high
        # For DOWN trend: fib retracements are between swing_high and swing_low
        retrace_ratios = [0.236, 0.382, 0.500, 0.618, 0.786]
        extend_ratios  = [1.272, 1.618, 2.000, 2.618]

        retracements = {}
        extensions   = {}

        if trend == "UP":
            # Retracements pull back from high toward low
            for r in retrace_ratios:
                retracements[round(r * 100, 1)] = round(swing_high_val - r * swing_range, 5)
            # Extensions project above the high
            for r in extend_ratios:
                extensions[round(r * 100, 1)] = round(swing_low_val + r * swing_range, 5)
        else:
            # Retracements pull back from low toward high (bearish swing)
            for r in retrace_ratios:
                retracements[round(r * 100, 1)] = round(swing_low_val + r * swing_range, 5)
            # Extensions project below the low
            for r in extend_ratios:
                extensions[round(r * 100, 1)] = round(swing_high_val - r * swing_range, 5)

        # --- Find nearest level to current price ---
        all_levels = [(ratio, price, "retracement") for ratio, price in retracements.items()]
        all_levels += [(ratio, price, "extension") for ratio, price in extensions.items()]

        nearest = min(all_levels, key=lambda x: abs(x[1] - current_price))
        nearest_ratio, nearest_price, nearest_type = nearest
        distance_pips = abs(current_price - nearest_price)
        # For gold (~2000), 1 pip ≈ 0.1; for silver (~30), 1 pip ≈ 0.01
        # Use percentage-based proximity instead
        proximity_pct = distance_pips / (current_price + 1e-9) * 100

        at_key_level = proximity_pct < 0.30  # within 0.3% = at the level

        # --- Zone label for AI prompt ---
        key_levels = {61.8: "golden ratio", 38.2: "strong confluence", 50.0: "midpoint",
                      78.6: "deep retracement", 23.6: "shallow pullback",
                      127.2: "minor extension", 161.8: "major extension"}
        level_name = key_levels.get(nearest_ratio, f"{nearest_ratio}% level")

        if at_key_level:
            zone_label = f"⚡ Price AT {nearest_ratio}% {nearest_type} ({level_name}) — HIGH-PROBABILITY ZONE"
        elif proximity_pct < 0.6:
            zone_label = f"Price approaching {nearest_ratio}% {nearest_type} ({level_name}) — {proximity_pct:.2f}% away"
        else:
            zone_label = f"Nearest Fib: {nearest_ratio}% {nearest_type} @ {nearest_price:.3f} ({proximity_pct:.2f}% away)"

        return {
            "swing_high":     round(float(swing_high_val), 5),
            "swing_low":      round(float(swing_low_val), 5),
            "trend":          trend,
            "retracements":   retracements,
            "extensions":     extensions,
            "nearest_level":  {
                "ratio":          nearest_ratio,
                "price":          round(nearest_price, 5),
                "distance_pct":   round(proximity_pct, 4),
                "type":           nearest_type,
            },
            "at_key_level":   at_key_level,
            "zone_label":     zone_label,
        }

    # ── Fibonacci factor scoring ──────────────────────────────────────────────

    def _compute_fib_factor(self, df: pd.DataFrame, fib_data: dict = None) -> float:
        """
        F12: Fibonacci proximity score.
        Returns a SIGNED score (positive = bullish Fib support, negative = bearish Fib resistance).

        Trading in the Zone principle: only trade at high-probability Fibonacci confluence zones.
        The score amplifies conviction when price is at a key level, and stays 0 when in no-man's-land.

        Score guide:
            ±12 : Price AT 61.8% (Golden Ratio) — highest probability zone
            ±10 : Price AT 38.2% or 50% — strong confluence
            ±6  : Price AT 78.6% or 23.6% — moderate level
            ±4  : Price APPROACHING (within 0.6%) a key level
              0 : No meaningful Fibonacci proximity
        """
        try:
            fib = fib_data if fib_data is not None else self.compute_fibonacci_levels(df, lookback=100)
            if not fib or not fib.get("nearest_level"):
                return 0.0

            fib_trend   = fib.get("trend", "")          # "UP" or "DOWN"
            at_level    = fib.get("at_key_level", False)
            nearest     = fib["nearest_level"]
            ratio       = nearest.get("ratio", 0)
            dist_pct    = nearest.get("distance_pct", 999)
            level_type  = nearest.get("type", "")

            # Direction: bullish when trend is UP (price pulled back to support)
            # bearish when trend is DOWN (price pulled back to resistance)
            direction_sign = 1.0 if fib_trend == "UP" else -1.0

            # ── At key level ────────────────────────────────────────────
            if at_level:
                strength_map = {
                    61.8: 12.0,   # Golden Ratio — maximum conviction
                    38.2: 10.0,   # Strong Fibonacci level
                    50.0: 10.0,   # 50% midpoint — institutional favourite
                    78.6: 6.0,    # Deep retracement — valid but riskier
                    23.6: 6.0,    # Shallow — valid only with strong trend
                    127.2: -4.0,  # Extension: over-extended, fade signal
                    161.8: -6.0,  # Major extension: exhaustion warning
                    200.0: -8.0,  # Double extension: very likely reversal
                }
                strength = strength_map.get(ratio, 4.0)
                # Extensions are inherently fading signals (opposite direction)
                if level_type == "extension":
                    return round(-abs(strength) * direction_sign, 1)
                return round(strength * direction_sign, 1)

            # ── Approaching (within 0.6%) ────────────────────────────
            if dist_pct < 0.60 and ratio in (61.8, 38.2, 50.0):
                return round(4.0 * direction_sign, 1)

            return 0.0

        except Exception as e:
            log.warning(f"[ANALYZER] Fibonacci computation failed: {e}")
            return 0.0

    # ── Factor scoring (pre-AI) ────────────────────────────────────────────────

    def _get_factor_scores(self, m15: dict, h1: dict, h4: dict, d1: dict = None,
                           weights: dict = None) -> dict:
        """
        Returns 9 DIRECTIONALLY SIGNED factor scores + regime flag.
        Positive = bullish, negative = bearish.  The signed sum directly
        determines direction — no more magnitude × direction_sign trick.

        Adaptive weights are loaded from scoring_weights.json when available,
        so the self-improvement engine can tune them over time.
        """
        if weights is None:
            weights = self._load_weights()

        # F1: H4 EMA trend (±10) — unchanged, already signed
        h4_map = {'BULLISH': 10, 'MILD_BULL': 5, 'MIXED': 0,
                  'MILD_BEAR': -5, 'BEARISH': -10}
        f1 = h4_map.get(h4['ema_trend'], 0)

        # F2: H1 EMA trend (±10) — unchanged
        h1_map = {'BULLISH': 10, 'MILD_BULL': 5, 'MIXED': 0,
                  'MILD_BEAR': -5, 'BEARISH': -10}
        f2 = h1_map.get(h1['ema_trend'], 0)

        # F3: RSI zone — NOW DIRECTIONALLY SIGNED
        # RSI < 30 = oversold = bullish (+), RSI > 70 = overbought = bearish (-)
        rsi = m15['rsi']
        if rsi < 30:
            f3 = min((50 - rsi) / 2, 10)     # +10 at RSI 30, +12.5 at RSI 25
        elif rsi > 70:
            f3 = -min((rsi - 50) / 2, 10)    # -10 at RSI 70, -12.5 at RSI 75
        elif rsi < 40:
            f3 = (50 - rsi) / 5              # mild bullish +2 at RSI 40
        elif rsi > 60:
            f3 = -(rsi - 50) / 5             # mild bearish -2 at RSI 60
        else:
            f3 = 0                            # neutral zone 40-60

        # F4: MACD momentum — NOW DIRECTIONALLY SIGNED
        macd_sig = m15['macd_signal']
        if macd_sig == 'BULLISH_CROSS':    f4 = 10
        elif macd_sig == 'BULLISH':        f4 = 6
        elif macd_sig == 'BEARISH_CROSS':  f4 = -10
        elif macd_sig == 'BEARISH':        f4 = -6
        else:                              f4 = 0

        # F5: ADX trend strength — SIGNED using +DI/-DI direction
        adx = m15['adx']
        plus_di  = m15.get('plus_di', 0)
        minus_di = m15.get('minus_di', 0)
        if adx >= 20:
            strength = min(adx / 3, 10)
            di_sign = 1 if plus_di > minus_di else -1
            f5 = strength * di_sign
        else:
            f5 = 0  # ranging — no directional signal

        # F6: Stochastic confirmation — NOW DIRECTIONALLY SIGNED
        stoch = m15['stoch_cross']
        stoch_k = m15['stoch_k']
        if stoch == 'BULLISH':
            f6 = 8
        elif stoch == 'BEARISH':
            f6 = -8
        elif stoch_k < 20:
            f6 = 4     # oversold = bullish
        elif stoch_k > 80:
            f6 = -4    # overbought = bearish
        else:
            f6 = 0

        # F7: Bollinger Band action — NOW DIRECTIONALLY SIGNED
        bb = m15['bb_position']
        if bb == 'BELOW_LOWER':
            f7 = 8     # mean revert long
        elif bb == 'ABOVE_UPPER':
            f7 = -8    # mean revert short
        elif bb == 'ABOVE_MID':
            f7 = 2     # mild bullish
        elif bb == 'BELOW_MID':
            f7 = -2    # mild bearish
        else:
            f7 = 0
        # Squeeze adds volatility awareness but NO direction
        # (squeeze means breakout imminent, not which direction)

        # F8: H1 RSI direction — NEW, half weight of M15
        h1_rsi = h1['rsi']
        if h1_rsi < 35:
            f8 = min((50 - h1_rsi) / 4, 5)    # max +5
        elif h1_rsi > 65:
            f8 = -min((h1_rsi - 50) / 4, 5)   # max -5
        else:
            f8 = 0

        # F9: H1 MACD direction — NEW, half weight of M15
        h1_macd = h1['macd_signal']
        if h1_macd == 'BULLISH_CROSS':    f9 = 5
        elif h1_macd == 'BULLISH':        f9 = 3
        elif h1_macd == 'BEARISH_CROSS':  f9 = -5
        elif h1_macd == 'BEARISH':        f9 = -3
        else:                             f9 = 0

        # F10: D1 daily trend — tiebreaker when H4/H1 conflict
        d1_map = {'BULLISH': 8, 'MILD_BULL': 4, 'MIXED': 0, 'MILD_BEAR': -4, 'BEARISH': -8}
        f10 = d1_map.get(d1.get('ema_trend', 'MIXED'), 0) if d1 else 0

        # F11: Candlestick pattern confirmation on M15
        f11 = m15.get('candle_pattern_score', 0)

        regime = 'RANGING' if adx < 18 else 'TRENDING'

        return {
            'f1_h4_trend':          round(f1  * weights.get('f1_h4_trend', 1.0), 1),
            'f2_h1_trend':          round(f2  * weights.get('f2_h1_trend', 1.0), 1),
            'f3_rsi_zone':          round(f3  * weights.get('f3_rsi', 1.0), 1),
            'f4_macd_momentum':     round(f4  * weights.get('f4_macd', 1.0), 1),
            'f5_adx_strength':      round(f5  * weights.get('f5_adx', 1.0), 1),
            'f6_stoch_confirm':     round(f6  * weights.get('f6_stoch', 1.0), 1),
            'f7_bb_action':         round(f7  * weights.get('f7_bb', 1.0), 1),
            'f8_h1_rsi':            round(f8  * weights.get('f8_h1_rsi', 0.5), 1),
            'f9_h1_macd':           round(f9  * weights.get('f9_h1_macd', 0.5), 1),
            'f10_d1_trend':         round(f10 * weights.get('f10_d1', 0.7), 1),
            'f11_candle_pattern':   round(f11 * weights.get('f11_candle', 0.8), 1),
            'adx_regime':           regime,
            'bb_squeeze':           m15.get('bb_squeeze', False),
        }

    def _load_weights(self) -> dict:
        """Load adaptive scoring weights from JSON file if available."""
        try:
            import os
            weights_path = os.path.join(os.path.dirname(__file__), "scoring_weights.json")
            if os.path.exists(weights_path):
                with open(weights_path, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}  # defaults handled by .get() calls

    # ── Multi-timeframe confluence signal ─────────────────────────────────────

    def _multi_tf_signal(self, m15: dict, h1: dict, h4: dict, d1: dict = None,
                         weights: dict = None) -> dict:
        if weights is None:
            weights = self._load_weights()
        scores = self._get_factor_scores(m15, h1, h4, d1, weights=weights)

        # SIGNED SUM — each factor already carries its direction
        signed_score = (
            scores['f1_h4_trend'] +
            scores['f2_h1_trend'] +
            scores['f3_rsi_zone'] +
            scores['f4_macd_momentum'] +
            scores['f5_adx_strength'] +
            scores['f6_stoch_confirm'] +
            scores['f7_bb_action'] +
            scores.get('f8_h1_rsi', 0) +
            scores.get('f9_h1_macd', 0) +
            scores.get('f10_d1_trend', 0) +
            scores.get('f11_candle_pattern', 0)
        )

        # ── ADX ranging penalty ──────────────────────────────────────────
        # Reduced from 0.6 to 0.8 — ranging market still has valid signals
        ranging_penalty = weights.get('ranging_penalty', 0.8)
        if scores['adx_regime'] == 'RANGING':
            signed_score = signed_score * ranging_penalty
            regime_note  = f'RANGING ADX<18 — score ×{ranging_penalty}'
        else:
            regime_note = ''

        # ── Confluence quality bonus ─────────────────────────────────────
        # Count how many factors agree with the direction
        factor_vals = [scores[k] for k in ['f1_h4_trend', 'f2_h1_trend',
                       'f3_rsi_zone', 'f4_macd_momentum', 'f5_adx_strength',
                       'f6_stoch_confirm', 'f7_bb_action'] if scores[k] != 0]
        if factor_vals:
            same_sign = sum(1 for v in factor_vals if (v > 0) == (signed_score > 0))
            confluence_ratio = same_sign / len(factor_vals)
        else:
            confluence_ratio = 0.5

        # Practical max under real market conditions with current weights (~25-30).
        # Using 80 (theoretical) meant score=20 only produced 0.25 confidence — too low.
        # With 25: score=15 → 0.60, score=20 → 0.80 (meaningful calibration).
        max_possible = 25.0

        buy_threshold = weights.get('buy_threshold', 18)
        sell_threshold = weights.get('sell_threshold', -18)

        # ── Final decision ───────────────────────────────────────────────
        if signed_score >= buy_threshold:
            direction  = 'BUY'
            raw_conf   = min(abs(signed_score) / max_possible, 0.95)
            confidence = raw_conf * (0.8 + 0.2 * confluence_ratio)
        elif signed_score <= sell_threshold:
            direction  = 'SELL'
            raw_conf   = min(abs(signed_score) / max_possible, 0.95)
            confidence = raw_conf * (0.8 + 0.2 * confluence_ratio)
        else:
            direction  = 'HOLD'
            # Proportional confidence based on how close we are to threshold
            # Score of 0 → 0.15, score near threshold → 0.35
            hold_conf = 0.15 + (abs(signed_score) / max(abs(buy_threshold), 1)) * 0.20
            confidence = round(min(hold_conf, 0.38), 4)

        # BB squeeze bonus: imminent breakout, boost confidence slightly
        if scores.get('bb_squeeze') and direction != 'HOLD':
            confidence = min(confidence * 1.08, 0.95)

        confidence = round(min(confidence, 0.95), 4)

        # Build reasons from high-scoring factors (signed now)
        reasons = []
        if abs(scores['f1_h4_trend'])      >= 8: reasons.append(f"H4 {h4['ema_trend']}")
        if abs(scores['f2_h1_trend'])      >= 8: reasons.append(f"H1 {h1['ema_trend']}")
        if abs(scores['f3_rsi_zone'])      >= 6: reasons.append(f"RSI {m15['rsi']:.0f}")
        if abs(scores['f4_macd_momentum']) >= 6: reasons.append(f"MACD {m15['macd_signal']}")
        if abs(scores['f5_adx_strength'])  >= 6: reasons.append(f"ADX {m15['adx']:.0f} +DI={m15['plus_di']:.0f} -DI={m15['minus_di']:.0f}")
        if abs(scores['f6_stoch_confirm']) >= 6: reasons.append(f"Stoch {m15['stoch_cross']} K={m15['stoch_k']:.0f}")
        if abs(scores['f7_bb_action'])     >= 6: reasons.append(f"BB {m15['bb_position']}")
        if abs(scores.get('f8_h1_rsi', 0)) >= 3: reasons.append(f"H1-RSI {h1['rsi']:.0f}")
        if abs(scores.get('f9_h1_macd', 0)) >= 3: reasons.append(f"H1-MACD {h1['macd_signal']}")
        if abs(scores.get('f10_d1_trend', 0)) >= 3: reasons.append(f"D1 {d1['ema_trend'] if d1 else 'N/A'}")
        if abs(scores.get('f11_candle_pattern', 0)) >= 3: reasons.append(f"Candle {int(m15.get('candle_pattern_score', 0)):+d}")
        if regime_note: reasons.append(regime_note)
        reasons.append(f"confluence={confluence_ratio:.0%}")

        return {
            'direction':     direction,
            'confidence':    confidence,
            'reason':        ' | '.join(reasons) if reasons else 'No clear confluence',
            'score':         round(signed_score, 1),
            'factor_scores': scores,
        }


    # ── AI reasoning ──────────────────────────────────────────────────────────

    def _ai_reasoning(self, symbol: str, tick, m15: dict, h1: dict, h4: dict,
                      base_signal: dict, candles: pd.DataFrame,
                      memory_context: str = "", research_context: str = "",
                      d1: dict = None, d1_df: pd.DataFrame = None) -> dict:
        last_candles = candles.tail(8)[["time", "o", "h", "l", "c", "vol"]].to_string(index=False)

        # Fibonacci levels — reuse cached result from analyze() to avoid duplicate computation
        fib = base_signal.get("fibonacci_data") or self.compute_fibonacci_levels(candles, lookback=100)
        fib_block = ""
        if fib:
            ret = fib["retracements"]
            ext = fib["extensions"]
            fib_block = f"""
=== FIBONACCI LEVELS (swing {fib['swing_low']:.3f} → {fib['swing_high']:.3f}, trend: {fib['trend']}) ===
Retracements: 23.6%={ret.get(23.6,'?')}  38.2%={ret.get(38.2,'?')}  50%={ret.get(50.0,'?')}  61.8%={ret.get(61.8,'?')}  78.6%={ret.get(78.6,'?')}
Extensions:   127.2%={ext.get(127.2,'?')}  161.8%={ext.get(161.8,'?')}  200%={ext.get(200.0,'?')}  261.8%={ext.get(261.8,'?')}
{fib['zone_label']}
"""

        # Daily pivot points from yesterday's D1 candle (more precise than swing high/low)
        if d1_df is not None and len(d1_df) >= 2:
            prev = d1_df.iloc[-2]
            pivot  = (prev["h"] + prev["l"] + prev["c"]) / 3
            r1     = round(2 * pivot - prev["l"], 5)
            s1     = round(2 * pivot - prev["h"], 5)
            r2     = round(pivot + (prev["h"] - prev["l"]), 5)
            s2     = round(pivot - (prev["h"] - prev["l"]), 5)
            pivot  = round(pivot, 5)
            levels_str = f"Pivot={pivot} R1={r1} R2={r2} S1={s1} S2={s2}"
        else:
            highs = candles["h"].values[-50:]
            lows  = candles["l"].values[-50:]
            res_level = round(float(np.max(highs)), 5)
            sup_level = round(float(np.min(lows)),  5)
            levels_str = f"Resistance={res_level} | Support={sup_level}"

        session = base_signal.get("session", "UNKNOWN")
        fs = base_signal.get("factor_scores", {})

        # Count factor agreement
        factor_vals = [fs.get(k, 0) for k in ['f1_h4_trend', 'f2_h1_trend',
                       'f3_rsi_zone', 'f4_macd_momentum', 'f5_adx_strength',
                       'f6_stoch_confirm', 'f7_bb_action', 'f8_h1_rsi', 'f9_h1_macd']]
        bullish_count = sum(1 for v in factor_vals if v > 0)
        bearish_count = sum(1 for v in factor_vals if v < 0)

        memory_block = ""
        if memory_context:
            memory_block = f"\n=== TRADE MEMORY (past performance in similar conditions) ===\n{memory_context}\n"

        research_block = ""
        if research_context:
            research_block = f"\n=== NEMOTRON DEEP RESEARCH (macro/fundamental context) ===\n{research_context}\n"

        # Pre-format D1 values to avoid invalid format specifier in f-string
        d1_ema = d1["ema_trend"] if d1 else "N/A"
        d1_adx = f"{d1['adx']:.0f}" if d1 else "N/A"
        d1_rsi = f"{d1['rsi']:.1f}" if d1 else "N/A"

        prompt = f"""Analyze {symbol} market data and decide: BUY, SELL, or HOLD.

=== MULTI-TIMEFRAME ANALYSIS ===
D1 (daily bias): EMA={d1_ema} ADX={d1_adx} RSI={d1_rsi}
H4 (trend bias): EMA={h4['ema_trend']} ADX={h4['adx']:.0f} RSI={h4['rsi']:.1f} MACD={h4['macd_signal']}
H1 (context):    EMA={h1['ema_trend']} ADX={h1['adx']:.0f} RSI={h1['rsi']:.1f} MACD={h1['macd_signal']}
M15 (entry):     EMA={m15['ema_trend']} ADX={m15['adx']:.0f} RSI={m15['rsi']:.1f} MACD={m15['macd_signal']}

=== M15 INDICATORS ===
Price: Ask={tick.ask:.5f} Bid={tick.bid:.5f}
EMA 20/50/200: {m15['ema20']}/{m15['ema50']}/{m15['ema200']}
MACD: {m15['macd_signal']} hist={m15['macd_hist']:.6f}
BB: {m15['bb_position']} squeeze={m15['bb_squeeze']}
Stoch: K={m15['stoch_k']:.0f} D={m15['stoch_d']:.0f} cross={m15['stoch_cross']}
ADX: {m15['adx']:.0f} +DI={m15['plus_di']:.0f} -DI={m15['minus_di']:.0f}
ATR: {m15['atr']}  Williams%R: {m15['williams_r']:.0f}
Candle pattern score: {int(m15.get('candle_pattern_score', 0)):+d}

=== KEY LEVELS (Daily Pivots) ===
{levels_str} | 20-bar change: {m15['price_change']:+.3f}%
{fib_block}

=== SESSION: {session} ===

=== SIGNED FACTOR SCORES (positive=bullish, negative=bearish) ===
F1  H4 Trend:       {fs['f1_h4_trend']:+.1f}
F2  H1 Trend:       {fs['f2_h1_trend']:+.1f}
F3  RSI:            {fs['f3_rsi_zone']:+.1f}
F4  MACD:           {fs['f4_macd_momentum']:+.1f}
F5  ADX/DI:         {fs['f5_adx_strength']:+.1f}
F6  Stochastic:     {fs['f6_stoch_confirm']:+.1f}
F7  Bollinger:      {fs['f7_bb_action']:+.1f}
F8  H1-RSI:         {fs.get('f8_h1_rsi', 0):+.1f}
F9  H1-MACD:        {fs.get('f9_h1_macd', 0):+.1f}
F10 D1 Trend:       {fs.get('f10_d1_trend', 0):+.1f}
F11 Candle Pattern: {fs.get('f11_candle_pattern', 0):+.1f}
F12 Fibonacci:      {fs.get('f12_fibonacci', 0):+.1f}
Regime: {fs['adx_regime']} | Bullish factors: {bullish_count}/12 | Bearish factors: {bearish_count}/12

=== INDICATOR-BASED SIGNAL ===
{base_signal['direction']} | Score: {base_signal['score']} | Confidence: {float(base_signal.get('confidence', 0)):.0%}
Reasons: {base_signal['reason']}
{memory_block}{research_block}
=== RECENT M15 CANDLES ===
{last_candles}

DECISION RULES:
- The signed score determines direction bias. Positive = bullish, negative = bearish.
- Your job: CONFIRM the indicator signal using price action context — not override it.
- BUY:  majority of factors positive AND price near support / bouncing EMA / oversold RSI
- SELL: majority of factors negative AND price near resistance / below EMAs / overbought RSI
- HOLD: factors genuinely split (within 2 factors of 50/50) AND no clear price action edge

⚠️  STRONG SIGNAL OVERRIDE (MANDATORY):
- If the signed score is ≥ +15 with 6+ bullish factors → you MUST output BUY, min confidence 0.65
- If the signed score is ≤ −15 with 6+ bearish factors → you MUST output SELL, min confidence 0.65
- A mildly-bearish H4 does NOT override a strongly-bullish D1+H1+M15 stack. H4 is already in the score.
- DO NOT say HOLD when 7 or more factors agree on direction. That is not a split — it is a signal.

RANGING market rules (ADX < 18):
- Use RSI extremes (<35 = BUY, >65 = SELL), BB extremes (below lower = BUY, above upper = SELL)
- Stoch cross in oversold/overbought territory = valid entry trigger
- Do NOT default to HOLD just because ADX is low — ranging markets have reversions.

Confidence calibration:
    0.55-0.65: marginal setup, 1-2 conflicting factors
    0.65-0.75: solid directional setup, most factors agree
    0.75-0.90: strong multi-factor confluence, clear price action
- NEVER output confidence below 0.50 if you say BUY or SELL.

Respond with ONLY raw JSON (no markdown, no backticks):
{{"direction": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "1-2 sentences"}}"""

        # ── Inject TradingView live data into prompt if available ────────────
        try:
            from tv_client import get_tv_client
            tv = get_tv_client()
            tv_snap = tv.snapshot()
            tv_sym  = tv_snap.get("symbols", {}).get(symbol, {})
            tv_ind  = tv_sym.get("indicators", {})
            tv_tfs  = tv_sym.get("timeframes", {})
            if tv_ind and tv_ind.get("adx") is not None:
                tv_block = f"""
=== TRADINGVIEW LIVE INDICATORS (source of truth) ===
Session: {tv_snap.get('session','?')}
M15: ADX={tv_tfs.get('M15',{}).get('adx','?')} +DI={tv_tfs.get('M15',{}).get('plus_di','?')} -DI={tv_tfs.get('M15',{}).get('minus_di','?')} RSI={tv_tfs.get('M15',{}).get('rsi','?')} EMA_TREND={tv_tfs.get('M15',{}).get('ema_trend','?')} MACD={tv_tfs.get('M15',{}).get('macd_signal','?')}
H1:  ADX={tv_tfs.get('H1',{}).get('adx','?')} +DI={tv_tfs.get('H1',{}).get('plus_di','?')} -DI={tv_tfs.get('H1',{}).get('minus_di','?')} RSI={tv_tfs.get('H1',{}).get('rsi','?')} EMA_TREND={tv_tfs.get('H1',{}).get('ema_trend','?')}
H4:  ADX={tv_tfs.get('H4',{}).get('adx','?')} +DI={tv_tfs.get('H4',{}).get('plus_di','?')} -DI={tv_tfs.get('H4',{}).get('minus_di','?')} RSI={tv_tfs.get('H4',{}).get('rsi','?')} EMA_TREND={tv_tfs.get('H4',{}).get('ema_trend','?')}
D1:  ADX={tv_tfs.get('D1',{}).get('adx','?')} +DI={tv_tfs.get('D1',{}).get('plus_di','?')} -DI={tv_tfs.get('D1',{}).get('minus_di','?')} RSI={tv_tfs.get('D1',{}).get('rsi','?')} EMA_TREND={tv_tfs.get('D1',{}).get('ema_trend','?')}
PRIMARY (M15): ADX={tv_ind.get('adx','?')} RSI={tv_ind.get('rsi','?')} BB={tv_ind.get('bb_position','?')} ATR={tv_ind.get('atr','?')} trend_strong={tv_ind.get('trend_strong','?')}
Williams%R={tv_ind.get('williams_r','?')} Stoch_K={tv_ind.get('stoch_k','?')} Stoch_cross={tv_ind.get('stoch_cross','?')}
"""
                prompt = prompt + tv_block
        except Exception:
            pass

        # ── AI signal: Gemini primary → OpenRouter fallback ───────────────────
        # HOW THIS WORKS:
        #   1. Gemini is called first (fast reasoning model).
        #   2. If Gemini fails/times out, OpenRouter (Llama 3.3 70B) is tried.
        #   3. If BOTH fail, the indicator score is used directly (Step 3 below).
        #
        # WHY YOU SEE "HOLD 55%" IN THE LOGS:
        #   This is NOT a hardcoded fallback. The AI IS being called and responding.
        #   It returns HOLD because the indicator's base_signal also says HOLD
        #   (when score < buy_threshold). The prompt tells the AI to "CONFIRM the
        #   indicator signal", so when the indicator is HOLD, the AI confirms HOLD.
        #   Fix: keep buy_threshold calibrated so the indicator generates
        #   directional signals (BUY/SELL) for clear market setups.
        ai_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        data = ask_gemini(ai_messages, label=symbol)
        if not data:
            log.warning(f"[ANALYZER] {symbol}: Gemini failed — trying OpenRouter")
            data = ask_openrouter(ai_messages, label=symbol)

        # ── Step 3: Hard indicator fallback (fires ONLY when both AI backends fail)
        if not data:
            log.warning(
                f"[ANALYZER] {symbol}: BOTH AI backends failed — falling back to "
                f"indicator signal (score={base_signal.get('score', 0):.1f}, "
                f"dir={base_signal.get('direction', 'HOLD')}). "
                f"Check Gemini/OpenRouter API keys and network."
            )
            data = {
                "direction":  base_signal.get("direction", "HOLD"),
                "confidence": base_signal.get("confidence", 0.55),
                "reason":     "[Indicator fallback] " + base_signal.get("reason", ""),
            }

        # ── Step 4: Post-processing (outside any try/except) ─────────────────
        ind_score     = base_signal.get("score", 0.0)
        ind_direction = base_signal.get("direction", "HOLD")
        ai_direction  = data.get("direction", "HOLD")

        # Strong-score override: if indicators strongly agree but AI says HOLD,
        # trust the indicators.  Threshold = 12 (1.5× the ±8 entry threshold) —
        # below 12 the score is genuinely borderline; above 12 it's a real signal.
        # Use the indicator's own confidence — it already encodes score strength
        # and confluence ratio.  Do NOT reset to a conservative formula.
        _score_override = False
        if ai_direction == "HOLD" and ind_direction in ("BUY", "SELL"):
            if abs(ind_score) >= 12:
                ai_direction = ind_direction
                ind_conf = float(base_signal.get("confidence", 0.62))

                # ADX-adaptive confidence cap (Trading in the Zone principle:
                # calibrate certainty to actual market conditions).
                # Ranging market (ADX < 15) = low certainty → cap 0.62
                # Developing trend (ADX 15-20) = medium certainty → cap 0.72
                # Trending (ADX >= 20) = full certainty → cap 0.82
                _adx_now = m15.get("adx", 25.0)
                if _adx_now < 15:
                    _override_cap = 0.62   # ranging — cap confidence low
                elif _adx_now < 20:
                    _override_cap = 0.72   # developing trend
                else:
                    _override_cap = 0.82   # trending — original cap

                data["direction"]  = ai_direction
                data["confidence"] = round(min(ind_conf, _override_cap), 4)
                data["reason"] = (f"[Score override {ind_score:+.1f}] "
                                  + data.get("reason", ""))
                _score_override = True

        # Graduated H4 override: nudge confidence DOWN when H4 disagrees.
        # KEY FIX: the score already numerically includes H4's contribution
        # (f1 is negative when H4 is bearish), so we must NOT double-penalise.
        # When a score override already fired, skip H4 penalty entirely — the
        # override confidence came directly from the score which includes f1.
        f1_score = base_signal.get("factor_scores", {}).get("f1_h4_trend", 0)
        if ai_direction in ("BUY", "SELL") and f1_score != 0 and not _score_override:
            h4_bullish = f1_score > 0
            ai_agrees  = ((ai_direction == "BUY"  and h4_bullish) or
                          (ai_direction == "SELL" and not h4_bullish))
            if not ai_agrees:
                h4_strength = abs(f1_score) / 10.0          # 0.5 for MILD, 1.0 for BEARISH
                penalty     = 1.0 - (h4_strength * 0.20)   # 0.90 for MILD, 0.80 for BEARISH
                data["confidence"] = max(float(data.get("confidence", 0.40)) * penalty, 0.25)
                data["reason"] = f"[H4 disagrees ×{penalty:.2f}] {data.get('reason', '')}"

        # Build AI context summary for trace logging (Stage 3)
        bull = sum(1 for v in base_signal.get("factor_scores", {}).values()
                   if isinstance(v, (int, float)) and v > 0)
        bear = sum(1 for v in base_signal.get("factor_scores", {}).values()
                   if isinstance(v, (int, float)) and v < 0)
        fib_data = base_signal.get("fibonacci_data", {})
        fib_zone = fib_data.get("zone_label", "") if fib_data else ""

        return {
            "direction":     data.get("direction", "HOLD"),
            "confidence":    round(float(data.get("confidence", 0.40)), 4),
            "reason":        data.get("reason", "AI analysis complete."),
            "score":         ind_score,
            "factor_scores": base_signal.get("factor_scores", {}),
            "indicators":    base_signal.get("indicators", {}),
            "h1_trend":      base_signal.get("h1_trend", ""),
            "h4_trend":      base_signal.get("h4_trend", ""),
            # Trace metadata (Stage 3 + Fib zone for Stage 2)
            "_ai_context":   (f"Score={ind_score:+.1f} | Bull:{bull}/12 Bear:{bear}/12 | "
                              f"IndSignal={ind_direction} → AI={data.get('direction','HOLD')}"),
            "_fib_zone":     fib_zone,
        }
