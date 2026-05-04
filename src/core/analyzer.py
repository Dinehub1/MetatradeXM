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
from typing import Dict, Optional
from dataclasses import dataclass
from core.regime_detector import RegimeDetector
from datetime import datetime, timezone

from core.ai_client import ask_nvidia

log = logging.getLogger("analyzer")

SYSTEM_PROMPT = """You are an expert precious metals trader specialising in XAUUSD (Gold) and XAGUSD (Silver).
You have 20+ years of experience and you are PROTECTING REAL CAPITAL.

PERFORMANCE REALITY (30-day live data as of 2026-05-03):
- Gold: 941 trades, 57.5% WR, avg win $5.56, avg loss -$11.19, net -$1,469.
- The bot LOSES MONEY because avg loss is 2× avg win (R:R = 0.50).
- ROOT CAUSE: too many marginal entries that get stopped out for full SL.
- YOUR MISSION: only signal when 3+ factors confirm = higher win rate + bigger winners.
- QUALITY > QUANTITY. 10 good trades beat 50 mediocre ones.

CRITICAL RULES:
1. REQUIRE 3+ CONFIRMING FACTORS before any BUY/SELL signal.
   Factors: ADX direction, MACD alignment, RSI zone, EMA trend, Stochastic, BB position.
2. DO NOT give confidence above 0.50 unless you can name 3 confirming factors in your reason.
3. HOLD is the DEFAULT. BUY/SELL is the exception for strong setups only.
4. Do NOT "probe" or "test" — every trade risks real money and the trailing stop needs room.
5. Trades need to run 20+ pips to activate our trailing stop. If you don't see 20+ pip potential, HOLD.

YOUR TRADE MEMORY section shows recent performance. USE IT:
- If similar setups lost money recently, HOLD (don't repeat the same mistake).
- If a pattern has been winning, increase confidence slightly.

WHEN TO HOLD (DEFAULT — most of the time):
- ADX < 20 (no confirmed trend — this is where most losses happen)
- Fewer than 3 confirming factors in one direction
- Score magnitude < 12 (weak signal, high noise)
- Conflicting timeframes (M15 says BUY but H1/H4 says SELL)
- Late NY / Asian session (17:00-01:00 UTC) — thin liquidity, 35% WR historically
- RSI between 35-65 with no other confluence (no edge)

WHEN TO BUY/SELL (only with strong confluence):
- Score >= +12 with ADX >= 22 AND MACD aligned → BUY (0.60+ confidence)
- Score <= -12 with ADX >= 22 AND MACD aligned → SELL (0.60+ confidence)
- 3+ timeframes agree (M15 + H1 + H4 all bullish/bearish) → 0.65+ confidence
- ADX > 28 with clear DI alignment + score direction → 0.70+ confidence (strong trend)
- RSI < 25 or > 75 WITH Stochastic cross AND BB touch → valid reversal at 0.55

CONFIDENCE CALIBRATION (recalibrated for profitability):
- 0.70-1.00: 4+ confirming factors across multiple timeframes = HIGH conviction
- 0.60-0.70: 3 confirming factors with clear trend = SOLID entry
- 0.50-0.60: Borderline — only enter if trend is developing AND ADX rising
- 0.00-0.50: HOLD — not enough edge to justify risk

TECHNICAL RULES:
- ADX > 25 = strong trend. Follow it. Do NOT counter-trend unless RSI extreme + Fib level.
- ADX 20-25 = developing. Need MACD + RSI + score all aligned. No shortcuts.
- ADX < 20 = ranging. HOLD. Do not trade ranges — our system is trend-following.
- Gold sessions: best 06:00-16:00 UTC (London + NY overlap). Avoid outside this window.

Respond with ONLY raw JSON: {"direction": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "1-2 sentences naming the confirming factors"}"""



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

        if self.use_claude:
            research_ctx = self._fetch_nemotron_research(symbol, base_signal)
            signal = self._ai_reasoning(symbol, tick, ind_m15, ind_h1, ind_h4,
                                        base_signal, tf_data["M15"],
                                        memory_context=memory_context,
                                        research_context=research_ctx,
                                        d1=ind_d1, d1_df=tf_data.get("D1"),
                                        m1_df=tf_data.get("M1"))
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
        m15_candles: optional broker M15 DataFrame — used for last-candle display in AI prompt
        """
        ind_m15 = self._tv_to_ind(tv_tfs.get("M15", {}))
        ind_h1  = self._tv_to_ind(tv_tfs.get("H1", {}))
        ind_h4  = self._tv_to_ind(tv_tfs.get("H4", {}))
        ind_d1  = self._tv_to_ind(tv_tfs["D1"]) if tv_tfs.get("D1") else None

        session     = self._get_session()
        weights     = self._load_weights()
        base_signal = self._multi_tf_signal(ind_m15, ind_h1, ind_h4, ind_d1, weights=weights)
        base_signal["session"] = session

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

        # F1: H4 EMA trend (±6) — reduced from ±10. Trend is CONTEXT, not a gatekeeper.
        # Old ±10 made trend factors alone (H4+D1 = ±18) exceed buy_threshold (12),
        # making it impossible for M15 entry signals to generate counter-trend trades.
        h4_map = {'BULLISH': 6, 'MILD_BULL': 3, 'MIXED': 0,
                  'MILD_BEAR': -3, 'BEARISH': -6}
        f1 = h4_map.get(h4['ema_trend'], 0)

        # F2: H1 EMA trend (±8) — reduced from ±10. Still significant but not overwhelming.
        h1_map = {'BULLISH': 8, 'MILD_BULL': 4, 'MIXED': 0,
                  'MILD_BEAR': -4, 'BEARISH': -8}
        f2 = h1_map.get(h1['ema_trend'], 0)

        # F3: RSI zone — TIGHTENED 2026-04-30 (25/75 vs old 30/70)
        # Stricter levels = fewer false reversal signals = higher quality entries
        rsi = m15['rsi']
        if rsi < 25:
            f3 = min((50 - rsi) / 2, 12)     # +12 at RSI 25, +12.5 at RSI 25
        elif rsi > 75:
            f3 = -min((rsi - 50) / 2, 12)    # -12 at RSI 75
        elif rsi < 35:
            f3 = (50 - rsi) / 5              # mild bullish (was at 40)
        elif rsi > 65:
            f3 = -(rsi - 50) / 5             # mild bearish (was at 60)
        else:
            f3 = 0                            # neutral zone 35-65 (was 40-60)

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

        # F10: D1 daily trend — reduced from ±8 to ±4. Tiebreaker, not dictator.
        # Old ±8 combined with H4 ±10 = ±18 from trend alone, exceeding threshold.
        d1_map = {'BULLISH': 4, 'MILD_BULL': 2, 'MIXED': 0, 'MILD_BEAR': -2, 'BEARISH': -4}
        f10 = d1_map.get(d1.get('ema_trend', 'MIXED'), 0) if d1 else 0

        # F11: Candlestick pattern confirmation on M15
        f11 = m15.get('candle_pattern_score', 0)

        # Advanced regime detection
        regime_data = RegimeDetector.detect(m15, h1, h4)

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
            'adx_regime':           regime_data['regime_label'],
            'volatility_state':     regime_data['volatility'],
            'recommended_strategy': regime_data['recommended_strategy'],
            'bb_squeeze':           m15.get('bb_squeeze', False),
        }

    def _load_weights(self) -> dict:
        """Load adaptive scoring weights from JSON file if available.

        Guardrails: buy/sell thresholds are capped at ±20 (max practical score
        ≈ 25-30). A threshold above 20 makes it impossible to generate signals
        and effectively halts the bot — so we cap and warn instead of silently
        breaking trading.
        """
        _THRESHOLD_MIN = 8    # below 8 → too permissive, low-quality signals
        _THRESHOLD_MAX = 20   # above 20 → impossible to achieve, stops trading

        weights: dict = {}
        try:
            from core.paths import CONFIG_DIR
            weights_path = CONFIG_DIR / "scoring_weights.json"
            if weights_path.exists():
                weights = json.loads(weights_path.read_text())
        except Exception:
            pass

        # Guard: clamp thresholds so the bot never stops trading due to
        # an over-aggressive self-improvement adjustment.
        raw_buy  = weights.get("buy_threshold",  12)
        raw_sell = weights.get("sell_threshold", -12)

        clamped_buy  = max(_THRESHOLD_MIN, min(_THRESHOLD_MAX, raw_buy))
        clamped_sell = min(-_THRESHOLD_MIN, max(-_THRESHOLD_MAX, raw_sell))

        if clamped_buy != raw_buy:
            log.warning(
                f"[WEIGHTS] buy_threshold {raw_buy} → clamped to {clamped_buy} "
                f"(valid range {_THRESHOLD_MIN}–{_THRESHOLD_MAX}). "
                "Threshold above 20 makes signals impossible."
            )
            weights["buy_threshold"] = clamped_buy

        if clamped_sell != raw_sell:
            log.warning(
                f"[WEIGHTS] sell_threshold {raw_sell} → clamped to {clamped_sell} "
                f"(valid range -{_THRESHOLD_MAX} – -{_THRESHOLD_MIN})."
            )
            weights["sell_threshold"] = clamped_sell

        return weights

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
        if scores['adx_regime'].startswith('RANGING'):
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
                      d1: dict = None, d1_df: pd.DataFrame = None,
                      m1_df: pd.DataFrame = None) -> dict:
        last_candles = candles.tail(8)[["time", "o", "h", "l", "c", "vol"]].to_string(index=False)

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

        # ── Regime-specific strategy guidance for AI ─────────────────────────
        _regime_label = fs.get('adx_regime', 'UNKNOWN')
        _vol_state    = fs.get('volatility_state', 'NORMAL')
        _rec_strategy = fs.get('recommended_strategy', 'TREND_FOLLOWING')
        regime_guidance_map = {
            "STRONG_TREND_UP":    "📈 STRONG UP TREND — prefer BUY on pullbacks, trail winners. Avoid SELL.",
            "STRONG_TREND_DOWN":  "📉 STRONG DOWN TREND — prefer SELL on bounces, trail winners. Avoid BUY.",
            "WEAK_TREND_UP":      "↗️ WEAK UP TREND — BUY on support with 2+ confirmations. SELL needs reversal evidence.",
            "WEAK_TREND_DOWN":    "↘️ WEAK DOWN TREND — SELL on resistance with 2+ confirmations. BUY needs reversal evidence.",
            "SQUEEZE_UP":         "⚡ SQUEEZE (bias UP) — breakout imminent. BUY on volume/candle confirmation.",
            "SQUEEZE_DOWN":       "⚡ SQUEEZE (bias DOWN) — breakout imminent. SELL on volume/candle confirmation.",
            "RANGING_CHOP":       "⚠️ RANGING CHOP — use mean-reversion ONLY. Require RSI extreme + Fib level. Skip marginal setups.",
            "RANGING_VOLATILE":   "🌊 RANGING VOLATILE — wide swings. Mean-reversion at Fib+BB extremes with Stoch cross only.",
        }
        _regime_guidance = regime_guidance_map.get(_regime_label, f"Regime: {_regime_label} | Strategy: {_rec_strategy}")

        memory_block = ""
        if memory_context:
            memory_block = f"\n=== TRADE MEMORY (past performance in similar conditions) ===\n{memory_context}\n"

        research_block = ""
        if research_context:
            research_block = f"\n=== NEMOTRON DEEP RESEARCH (macro/fundamental context) ===\n{research_context}\n"

        # ── M1 micro-structure block (entry timing) ──────────────────────────
        m1_block = ""
        if m1_df is not None and len(m1_df) >= 15:
            try:
                m1_tail = m1_df.tail(15)
                m1_candles_str = m1_tail[["time", "o", "h", "l", "c", "vol"]].to_string(index=False)
                # Simple M1 momentum: price change over last 5 bars
                m1_close = m1_tail["c"].values
                m1_momentum = "UP" if m1_close[-1] > m1_close[-5] else "DOWN"
                m1_range = float(m1_tail["h"].max() - m1_tail["l"].min())
                # M1 RSI (simple)
                m1_deltas = pd.Series(m1_close).diff().dropna()
                m1_gains = m1_deltas.clip(lower=0).rolling(14, min_periods=5).mean()
                m1_losses = (-m1_deltas.clip(upper=0)).rolling(14, min_periods=5).mean()
                m1_rs = m1_gains.iloc[-1] / m1_losses.iloc[-1] if m1_losses.iloc[-1] > 0 else 100
                m1_rsi_val = round(100 - (100 / (1 + m1_rs)), 1)
                m1_block = f"""
=== M1 MICRO-STRUCTURE (last 15 bars = 15 min of tick action) ===
{m1_candles_str}
M1 RSI: {m1_rsi_val}
M1 Momentum (5-bar): {m1_momentum}
M1 Range: {m1_range:.5f}
"""
            except Exception:
                m1_block = ""

        # Pre-format D1 values to avoid invalid format specifier in f-string
        d1_ema = d1["ema_trend"] if d1 else "N/A"
        d1_adx = f"{d1['adx']:.0f}" if d1 else "N/A"
        d1_rsi = f"{d1['rsi']:.1f}" if d1 else "N/A"

        # Build data quality summary for AI
        _n_candles = len(candles) if candles is not None else 0
        _indicators_list = [
            f"ADX({m15.get('adx', 0):.0f})",
            f"RSI({m15.get('rsi', 50):.0f})",
            f"MACD({m15.get('macd_signal', '?')})",
            f"Stoch(K={m15.get('stoch_k', 50):.0f})",
            f"BB({m15.get('bb_position', '?')})",
            f"EMA(20/50/200)",
            f"ATR({m15.get('atr', 0):.5f})",
            f"W%R({m15.get('williams_r', -50):.0f})",
        ]

        # Silver-specific market context injected before regime/indicator data
        _symbol_ctx = ""
        if symbol in ("XAGUSD", "SILVER", "XAGUSD.i", "SILVER.i#"):
            _symbol_ctx = """
=== SILVER-SPECIFIC CONTEXT ===
Silver is a hybrid industrial/precious metal — different from Gold in key ways:
- Beta to Gold: Silver amplifies Gold moves by 2-3×. Expect wider swings per ATR unit.
- Industrial demand component: bearish during risk-off if manufacturing sentiment weakens.
- Mean-reversion character: H4 BULLISH + M15 MACD BEARISH is a NORMAL Silver setup —
  price pulls back to H4 support before bouncing. Do NOT treat M15 MACD bearish as a veto when H4 is bullish.
- RSI 40-50 (neutral zone) + H4 BULLISH + ADX 22-28 = Silver's ideal BUY zone.
  RSI does NOT need to be oversold (20-25) — Silver rarely reaches extremes.
- Silver trends end faster than Gold. Realistic TP is 3× ATR, not 4-5×.
- LONDON session is structurally bearish for Silver due to European industrial risk flows.
  Weight London SELL signals higher than BUY signals.
- NEW_YORK and ASIAN sessions: primary windows for Silver mean-reversion setups.

"""

        prompt = f"""Analyze {symbol} market data and decide: BUY, SELL, or HOLD.
{_symbol_ctx}
=== MARKET REGIME (adapt your strategy to this) ===
{_regime_guidance}
Volatility: {_vol_state} | Recommended approach: {_rec_strategy}

=== DATA QUALITY & SOURCES ===
Source: MetaTrader 5 broker candles (via MetaApi bridge)
M15 candles: {_n_candles} bars | H1/H4/D1: multi-timeframe confirmed
Indicators computed: {', '.join(_indicators_list)}
ADX regime: {fs.get('adx_regime', 'UNKNOWN')} — ADX measures TREND STRENGTH (>25=trending, <18=ranging)
  +DI vs -DI tells you WHICH side is dominant: +DI>{m15.get('plus_di', 0):.0f} vs -DI={m15.get('minus_di', 0):.0f}
All values below are REAL-TIME computed from the latest available candle data.

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
Regime: {fs['adx_regime']} | Bullish factors: {bullish_count}/11 | Bearish factors: {bearish_count}/11

=== INDICATOR-BASED SIGNAL ===
{base_signal['direction']} | Score: {base_signal['score']} | Confidence: {float(base_signal.get('confidence', 0)):.0%}
Reasons: {base_signal['reason']}
{memory_block}{research_block}
=== RECENT M15 CANDLES ===
{last_candles}
{m1_block}

DECISION FRAMEWORK:
- The indicator score shows the prevailing BIAS, not a mandate.
- Score NEGATIVE = bearish environment. SELL is easier, BUY needs reversal evidence.
- Score POSITIVE = bullish environment. BUY is easier, SELL needs reversal evidence.
- HOLD is your DEFAULT when no clear setup exists.

TREND-FOLLOWING (go WITH the score):
- Score strongly agrees with your direction → confidence 0.65-0.85
- 6+ factors in same direction → strong signal, minimum confidence 0.65

COUNTER-TREND (go AGAINST the score — HIGH BAR required):
- Only trade against score when: RSI extreme (<25 or >75) + Fibonacci 61.8% + BB extreme ALL together
- Require at least 3 reversal confirmations simultaneously
- Counter-trend minimum confidence: 0.68 (same bar as trend-following)
- If only 1-2 reversal signals: HOLD

RANGING market (ADX < 18):
- Use RSI extremes at Fibonacci levels → mean-reversion entries
- BB extremes with Stoch cross = valid trigger
- Require ALL THREE: RSI extreme + Fib level + BB extreme
- ADX < 15: HOLD unless the above 3 confirmations are all present

Confidence calibration:
    0.65-0.70: minimum viable — cautious entry, strong setup required
    0.70-0.80: solid setup, 2+ timeframes agree, confident entry
    0.80-0.95: high conviction — all timeframes agree + Fibonacci confluence
- Minimum confidence for ANY trade (BUY or SELL): 0.65
- Below 0.65: HOLD. No exceptions.

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
        _prompt_chars = len(prompt)
        log.info(
            f"AI-REQ | {symbol} | chars {_prompt_chars:,} | chain NVIDIA->Gemini | "
            f"ADX {m15.get('adx', 0):.0f} | RSI {m15.get('rsi', 50):.0f} | score {base_signal.get('score', 0):+.1f}"
        )
        data = ask_nvidia(ai_messages, label=symbol)

        # ── Step 3: Hard indicator fallback (fires ONLY when ALL AI tiers fail)
        if not data:
            _fb_score = base_signal.get("score", 0)
            _fb_dir   = base_signal.get("direction", "HOLD")
            _w = self._load_weights()
            _fb_buy_t  = _w.get("buy_threshold",  12)
            _fb_sell_t = _w.get("sell_threshold", -12)

            # When AI is down, allow 85% of threshold to trigger — prevents
            # the bot going completely dark during API outages.
            _fb_lower = _fb_buy_t * 0.85
            if _fb_dir == "HOLD":
                if _fb_score >= _fb_lower:
                    _fb_dir = "BUY"
                elif _fb_score <= -_fb_lower:
                    _fb_dir = "SELL"

            log.warning(
                f"[ANALYZER] {symbol}: ALL AI tiers exhausted — using indicator fallback "
                f"(score={_fb_score:.1f} vs thresh={_fb_buy_t}, lowered to {_fb_lower:.1f}) "
                f"dir={_fb_dir}. Renew GEMINI_API_KEY at aistudio.google.com"
            )
            data = {
                "direction":  _fb_dir,
                "confidence": min(base_signal.get("confidence", 0.55), 0.65),
                "reason":     "[AI-down fallback] " + base_signal.get("reason", ""),
            }

        # ── Step 4: Post-processing (outside any try/except) ─────────────────
        ind_score     = base_signal.get("score", 0.0)
        ind_direction = base_signal.get("direction", "HOLD")
        ai_direction  = data.get("direction", "HOLD")

        # ── COUNTER-TREND ADVISORY: log warning but DO NOT block ──
        # OLD: hard veto at ±15 forced HOLD, causing 100% SELL-only bias.
        # NEW: log for awareness. The MTF disagreement penalty below handles
        # risk reduction. Counter-trend trades at Fibonacci levels are profitable.
        if ai_direction == "BUY" and ind_score <= -12:
            log.info(f"AI-NOTE | {symbol} | counter-trend BUY vs score {ind_score:+.1f} | MTF penalty next")
            data["reason"] = f"[Counter-trend: score={ind_score:+.1f}] " + data.get("reason", "")
        elif ai_direction == "SELL" and ind_score >= 12:
            log.info(f"AI-NOTE | {symbol} | counter-trend SELL vs score {ind_score:+.1f} | MTF penalty next")
            data["reason"] = f"[Counter-trend: score={ind_score:+.1f}] " + data.get("reason", "")

        # Strong-score override: if indicators strongly agree but AI says HOLD,
        # trust the indicators.  Threshold = 12 (1.5× the ±8 entry threshold) —
        # below 12 the score is genuinely borderline; above 12 it's a real signal.
        # Use the indicator's own confidence — it already encodes score strength
        # and confluence ratio.  Do NOT reset to a conservative formula.
        _score_override = False
        if ai_direction == "HOLD" and ind_direction in ("BUY", "SELL"):
            # Lower threshold during ranging markets — indicators are still valid
            _override_thresh = 8 if m15.get("adx", 25) < 18 else 12
            if abs(ind_score) >= _override_thresh:
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

                # Weak-score penalty applied here so it is never skipped.
                # Override at score 8-12 (ranging market) is genuinely borderline;
                # cap it hard rather than letting the ADX cap (0.62-0.82) pass through.
                _so_abs = abs(ind_score)
                if _so_abs < 8:
                    data["confidence"] = min(data["confidence"], 0.40)
                    log.info(f"AI-NOTE | {symbol} | override noise score {ind_score:+.1f} (< 8) — cap 40%")
                elif _so_abs < 12:
                    data["confidence"] = round(data["confidence"] * 0.75, 4)
                    log.info(f"AI-NOTE | {symbol} | override weak score {ind_score:+.1f} (< 12) — conf ×0.75")

                data["reason"] = (f"[Score override {ind_score:+.1f}] "
                                  + data.get("reason", ""))
                _score_override = True

        # ── MULTI-TIMEFRAME DISAGREEMENT PENALTY ──────────────────────────────
        # Graduated penalty based on how many higher TFs disagree.
        # Calibration (2026-04-24): old ×0.55 for 3-TF made it IMPOSSIBLE
        # for any signal to pass the 55% gate (95% × 0.55 = 52% < 55%).
        # New values allow high-conviction counter-trend trades through.
        # Skip when score override already fired (score already includes TF info).
        _fs = base_signal.get("factor_scores", {})
        f1_score = _fs.get("f1_h4_trend", 0)
        f2_score = _fs.get("f2_h1_trend", 0)
        f10_score = _fs.get("f10_d1_trend", 0)

        if ai_direction in ("BUY", "SELL") and not _score_override:
            disagree_count = 0
            if ai_direction == "BUY":
                if f1_score < 0: disagree_count += 1   # H4 bearish
                if f2_score < 0: disagree_count += 1   # H1 bearish
                if f10_score < 0: disagree_count += 1  # D1 bearish
            elif ai_direction == "SELL":
                if f1_score > 0: disagree_count += 1   # H4 bullish
                if f2_score > 0: disagree_count += 1   # H1 bullish
                if f10_score > 0: disagree_count += 1  # D1 bullish

            # ── GRADUATED PENALTY (not a kill switch) ──
            # Calibration math at each tier (AI 65% is typical):
            #   3 TFs: 65% × 0.80 = 52.0% — passes normal adaptive gates
            #          55% × 0.80 = 44.0% — permits small Fib reversal probes
            #          85% × 0.80 = 68.0% — comfortably passes all gates
            #   2 TFs: 65% × 0.85 = 55.3% — passes normal gate (0.55)
            #   1 TF:  65% × 0.92 = 59.8% — minimal reduction
            # This allows high-conviction counter-trend trades at Fib levels
            # while still penalizing weak signals against the trend.
            if disagree_count >= 3:
                penalty = 0.80
                log.info(f"AI-NOTE | {symbol} | 3 higher TFs disagree with {ai_direction} | conf x{penalty}")
            elif disagree_count == 2:
                penalty = 0.85
                log.info(f"AI-NOTE | {symbol} | 2 higher TFs disagree with {ai_direction} | conf x{penalty}")
            elif disagree_count == 1:
                penalty = 0.92
            else:
                penalty = 1.0

            if penalty < 1.0:
                old_conf = float(data.get("confidence", 0.40))
                data["confidence"] = max(old_conf * penalty, 0.20)
                data["reason"] = f"[MTF ×{penalty} ({disagree_count} TFs disagree)] {data.get('reason', '')}"

        # ── WEAK SCORE PENALTY ────────────────────────────────────────────────
        # For non-override paths: AI returned BUY/SELL directly but score is weak.
        # Override paths already had the penalty applied inside the override block.
        _score_abs = abs(ind_score)
        if ai_direction in ("BUY", "SELL") and not _score_override:
            if _score_abs < 8:
                # Score is pure noise (< 8) — cap confidence at 45%
                data["confidence"] = min(float(data.get("confidence", 0.40)), 0.45)
                log.info(f"AI-NOTE | {symbol} | weak score {ind_score:+.1f} (< 8) — confidence capped 45%")
            elif _score_abs < 12:
                # Score is weak (8-12) — apply 25% penalty to AI confidence
                old_conf = float(data.get("confidence", 0.65))
                data["confidence"] = max(old_conf * 0.75, 0.50)  # ×0.75 penalty, floor 50%
                log.info(f"AI-NOTE | {symbol} | weak score {ind_score:+.1f} (< 12) — confidence ×0.75 penalty")

        # Build AI context summary for trace logging (Stage 3)
        bull = sum(1 for v in base_signal.get("factor_scores", {}).values()
                   if isinstance(v, (int, float)) and v > 0)
        bear = sum(1 for v in base_signal.get("factor_scores", {}).values()
                   if isinstance(v, (int, float)) and v < 0)
        return {
            "direction":     data.get("direction", "HOLD"),
            "confidence":    round(float(data.get("confidence", 0.40)), 4),
            "reason":        data.get("reason", "AI analysis complete."),
            "score":         ind_score,
            "factor_scores": base_signal.get("factor_scores", {}),
            "indicators":    base_signal.get("indicators", {}),
            "h1_trend":      base_signal.get("h1_trend", ""),
            "h4_trend":      base_signal.get("h4_trend", ""),
            "_ai_context":   (f"Score={ind_score:+.1f} | Bull:{bull}/11 Bear:{bear}/11 | "
                              f"IndSignal={ind_direction} → AI={data.get('direction','HOLD')}"),
        }
