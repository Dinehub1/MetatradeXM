"""
MarketAnalyzer — UPGRADED
  - Multi-timeframe analysis: M15 entry, H1 context, H4 trend
  - Indicators: RSI, EMA, MACD, Bollinger, ATR, ADX, Stochastic, Williams %R
  - Session detection: London, New York, Asian, Off-hours
  - Confluence scoring: only trade when multiple timeframes agree
  - Ollama/minimax AI with rich system prompt and structured output
"""

import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

OLLAMA_MODEL     = "minimax-m2.7:cloud"
OLLAMA_CHAT_URL  = "http://localhost:11434/api/chat"

SYSTEM_PROMPT = """You are a professional forex trader with 15+ years of experience.
You specialize in technical analysis and risk management.
You analyze market data objectively and give clear, actionable trading decisions.
You are conservative — when in doubt, you say HOLD to protect capital.
You always consider the higher timeframe trend before entering on lower timeframes."""


class MarketAnalyzer:
    def __init__(self, use_claude: bool = True):
        self.use_claude = use_claude

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, candles, tick, symbol: str, memory_context: str = "") -> dict:
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

        session     = self._get_session()
        base_signal = self._multi_tf_signal(ind_m15, ind_h1, ind_h4)
        base_signal["session"] = session

        if self.use_claude:
            signal = self._ai_reasoning(symbol, tick, ind_m15, ind_h1, ind_h4,
                                        base_signal, tf_data["M15"],
                                        memory_context=memory_context)
        else:
            signal = base_signal

        signal["indicators"] = ind_m15
        signal["h1_trend"]   = ind_h1["ema_trend"]
        signal["h4_trend"]   = ind_h4["ema_trend"]
        signal["session"]    = session
        return signal

    # ── Session detection ─────────────────────────────────────────────────────

    def _get_session(self) -> str:
        now = datetime.now(timezone.utc)
        weekday, hour = now.weekday(), now.hour
        if weekday == 5 or (weekday == 4 and hour >= 22) or (weekday == 6 and hour < 22):
            return "MARKET_CLOSED"
        if 7  <= hour < 13: return "LONDON"
        if 13 <= hour < 16: return "LONDON_NY_OVERLAP"
        if 16 <= hour < 22: return "NEW_YORK"
        return "ASIAN"

    # ── Indicator suite ───────────────────────────────────────────────────────

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        close = df["c"].values
        high  = df["h"].values
        low   = df["l"].values

        rsi_val  = self._rsi(close, 14)
        adx_val, plus_di, minus_di = self._adx(high, low, close, 14)
        stoch_k, stoch_d = self._stochastic(high, low, close, 14, 3)
        will_r   = self._williams_r(high, low, close, 14)

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
            "williams_r":   round(will_r, 1),
            "price":        round(close[-1], 5),
            "price_change": round((close[-1] - close[-20]) / close[-20] * 100, 3),
            "vol_ratio":    self._volume_ratio(df),
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

    def _get_factor_scores(self, m15: dict, h1: dict, h4: dict) -> dict:
        """
        Returns 9 DIRECTIONALLY SIGNED factor scores + regime flag.
        Positive = bullish, negative = bearish.  The signed sum directly
        determines direction — no more magnitude × direction_sign trick.

        Adaptive weights are loaded from scoring_weights.json when available,
        so the self-improvement engine can tune them over time.
        """
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

        regime = 'RANGING' if adx < 18 else 'TRENDING'

        return {
            'f1_h4_trend':       round(f1 * weights.get('f1_h4_trend', 1.0), 1),
            'f2_h1_trend':       round(f2 * weights.get('f2_h1_trend', 1.0), 1),
            'f3_rsi_zone':       round(f3 * weights.get('f3_rsi', 1.0), 1),
            'f4_macd_momentum':  round(f4 * weights.get('f4_macd', 1.0), 1),
            'f5_adx_strength':   round(f5 * weights.get('f5_adx', 1.0), 1),
            'f6_stoch_confirm':  round(f6 * weights.get('f6_stoch', 1.0), 1),
            'f7_bb_action':      round(f7 * weights.get('f7_bb', 1.0), 1),
            'f8_h1_rsi':         round(f8 * weights.get('f8_h1_rsi', 0.5), 1),
            'f9_h1_macd':        round(f9 * weights.get('f9_h1_macd', 0.5), 1),
            'adx_regime':        regime,
            'bb_squeeze':        m15.get('bb_squeeze', False),
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

    def _multi_tf_signal(self, m15: dict, h1: dict, h4: dict) -> dict:
        scores = self._get_factor_scores(m15, h1, h4)
        weights = self._load_weights()

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
            scores.get('f9_h1_macd', 0)
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

        # Max possible with 9 signed factors: ~80 (10+10+10+10+10+8+8+5+5)
        max_possible = 80.0

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
                      memory_context: str = "") -> dict:
        last_candles = candles.tail(8)[["time", "o", "h", "l", "c", "vol"]].to_string(index=False)

        # Detect key levels: recent swing highs/lows
        highs = candles["h"].values[-50:]
        lows  = candles["l"].values[-50:]
        res_level = round(float(np.max(highs)), 5)
        sup_level = round(float(np.min(lows)),  5)

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

        prompt = f"""Analyze {symbol} market data and decide: BUY, SELL, or HOLD.

=== MULTI-TIMEFRAME ANALYSIS ===
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

=== KEY LEVELS ===
Resistance: {res_level} | Support: {sup_level} | 20-bar change: {m15['price_change']:+.3f}%

=== SESSION: {session} ===

=== SIGNED FACTOR SCORES (positive=bullish, negative=bearish) ===
F1 H4 Trend:    {fs['f1_h4_trend']:+.1f}
F2 H1 Trend:    {fs['f2_h1_trend']:+.1f}
F3 RSI:         {fs['f3_rsi_zone']:+.1f}
F4 MACD:        {fs['f4_macd_momentum']:+.1f}
F5 ADX/DI:      {fs['f5_adx_strength']:+.1f}
F6 Stochastic:  {fs['f6_stoch_confirm']:+.1f}
F7 Bollinger:   {fs['f7_bb_action']:+.1f}
F8 H1-RSI:      {fs.get('f8_h1_rsi', 0):+.1f}
F9 H1-MACD:     {fs.get('f9_h1_macd', 0):+.1f}
Regime: {fs['adx_regime']} | Bullish factors: {bullish_count}/9 | Bearish factors: {bearish_count}/9

=== INDICATOR-BASED SIGNAL ===
{base_signal['direction']} | Score: {base_signal['score']} | Confidence: {base_signal['confidence']:.0%}
Reasons: {base_signal['reason']}
{memory_block}
=== RECENT M15 CANDLES ===
{last_candles}

DECISION RULES:
- The signed score determines direction bias. Positive score = bullish lean, negative = bearish lean.
- Your job: confirm or reject the indicator signal based on price action context.
- BUY: majority of factors positive AND price near support / bouncing off EMA / oversold
- SELL: majority of factors negative AND price near resistance / below EMAs / overbought
- HOLD: factors are genuinely split (within 1 factor of 50/50) AND no clear price action edge
- DO NOT default to HOLD just because ADX is low — ranging markets still have direction.
- RANGING markets: use RSI extremes, BB extremes, and Stoch crosses as primary signals.
- Confidence calibration:
    0.52-0.60: marginal setup, weak confluence
    0.60-0.75: solid directional setup
    0.75-0.90: strong multi-factor confluence
- NEVER output confidence below 0.40 if you say BUY or SELL — that's contradictory.
- Be decisive. If bearish factors outnumber bullish 6:3 or more, say SELL.

Respond with ONLY raw JSON (no markdown, no backticks):
{{"direction": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "1-2 sentences"}}"""

        for attempt in range(2):   # 1 retry on timeout
            try:
                resp = requests.post(OLLAMA_CHAT_URL, json={
                    "model":  OLLAMA_MODEL,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                }, timeout=25)
                resp.raise_for_status()

                raw = resp.text.strip()
                if not raw:
                    raise ValueError("Empty response from Ollama")

                text = resp.json()["message"]["content"].strip()
                # Strip markdown fences
                if "```" in text:
                    parts = text.split("```")
                    for part in parts:
                        part = part.strip().lstrip("json").strip()
                        if part.startswith("{"):
                            text = part
                            break
                data = json.loads(text)
                ai_direction = data.get("direction", "HOLD")
                ai_conf      = float(data.get("confidence", 0.40))
                ind_score    = base_signal.get("score", 0.0)
                ind_direction = base_signal.get("direction", "HOLD")

                # ── Strong-score override: if indicators strongly agree but AI says HOLD,
                #    trust the indicators (|score| >= 15 = well above ±6 threshold)
                if ai_direction == "HOLD" and ind_direction in ("BUY", "SELL"):
                    if abs(ind_score) >= 15:
                        ai_direction = ind_direction
                        # Confidence: starts at 0.52, rises with score strength
                        # Score 15 → 0.52, score 25 → 0.60, score 35+ → 0.65
                        base_conf = min(0.52 + (abs(ind_score) - 15) * 0.008, 0.65)
                        data["direction"]  = ai_direction
                        data["confidence"] = round(base_conf, 4)
                        data["reason"] = (f"[Score override {ind_score:+.1f}] "
                                          + data.get("reason", ""))

                # ── GRADUATED H4 override (penalise when H4 disagrees with AI direction)
                f1_score = base_signal.get("factor_scores", {}).get("f1_h4_trend", 0)
                if ai_direction in ("BUY", "SELL") and f1_score != 0:
                    h4_bullish = f1_score > 0
                    ai_agrees  = ((ai_direction == "BUY"  and h4_bullish) or
                                  (ai_direction == "SELL" and not h4_bullish))
                    if not ai_agrees:
                        h4_strength = abs(f1_score) / 10.0
                        penalty = 0.5 + (1.0 - h4_strength) * 0.4
                        data["confidence"] = max(float(data.get("confidence", 0.40)) * penalty, 0.20)
                        data["reason"] = f"[H4 disagrees ×{penalty:.1f}] {data.get('reason', '')}"

                return {
                    "direction":     data.get("direction", "HOLD"),
                    "confidence":    round(float(data.get("confidence", 0.40)), 4),
                    "reason":        data.get("reason", "AI analysis complete."),
                    "score":         ind_score,
                    "factor_scores": base_signal.get("factor_scores", {}),
                    "indicators":    base_signal.get("indicators", {}),
                    "h1_trend":      base_signal.get("h1_trend", ""),
                    "h4_trend":      base_signal.get("h4_trend", ""),
                }
            except Exception as e:
                if attempt == 0 and "timed out" in str(e).lower():
                    print(f"  ⚠️  Ollama timeout, retrying once...")
                    continue
                print(f"  ⚠️  Ollama error: {e} — using indicator signal.")
                return base_signal
        return base_signal
