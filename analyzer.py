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

    def analyze(self, candles, tick, symbol: str) -> dict:
        """
        candles: single DataFrame (M15) OR dict {"M15": df, "H1": df, "H4": df}
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
                                        base_signal, tf_data["M15"])
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
        Returns 7 individual factor scores (each 0-10 magnitude) + regime flag.
        Used to prime the AI with structured pre-analysis and to drive
        confidence-weighted position sizing in risk_manager.py.
        """
        # F1: H4 EMA trend (sign = direction, magnitude = strength)
        h4_map = {'BULLISH': 10, 'MILD_BULL': 5, 'MIXED': 0,
                  'MILD_BEAR': -5, 'BEARISH': -10}
        f1 = h4_map.get(h4['ema_trend'], 0)

        # F2: H1 EMA trend
        h1_map = {'BULLISH': 10, 'MILD_BULL': 5, 'MIXED': 0,
                  'MILD_BEAR': -5, 'BEARISH': -10}
        f2 = h1_map.get(h1['ema_trend'], 0)

        # F3: RSI zone — 0-10 from |RSI-50|/2 (higher = more extreme)
        f3 = min(abs(m15['rsi'] - 50) / 2, 10)

        # F4: MACD momentum magnitude
        macd_sig = m15['macd_signal']
        if 'BULLISH_CROSS' in macd_sig:   f4 = 10
        elif 'BULLISH' in macd_sig:        f4 = 6
        elif 'BEARISH_CROSS' in macd_sig:  f4 = 10
        elif 'BEARISH' in macd_sig:        f4 = 6
        else:                              f4 = 0

        # F5: ADX trend strength (only counts if trending, 0 if ranging)
        adx = m15['adx']
        if adx >= 30:   f5 = min(adx / 3, 10)
        elif adx >= 20: f5 = max((adx - 18) * 2, 0)
        else:           f5 = 0

        # F6: Stochastic confirmation magnitude
        stoch = m15['stoch_cross']
        if stoch == 'BULLISH':    f6 = 8
        elif stoch == 'BEARISH':  f6 = 8
        elif m15['stoch_k'] < 20: f6 = 4
        elif m15['stoch_k'] > 80: f6 = 4
        else:                     f6 = 0

        # F7: Bollinger Band action magnitude
        bb = m15['bb_position']
        if bb == 'BELOW_LOWER':   f7 = 8
        elif bb == 'ABOVE_UPPER': f7 = 8
        elif bb in ('ABOVE_MID', 'BELOW_MID'): f7 = 3
        else:                     f7 = 0
        if m15['bb_squeeze']: f7 = max(f7, 6)

        regime = 'RANGING' if adx < 18 else 'TRENDING'

        return {
            'f1_h4_trend':       f1,
            'f2_h1_trend':        f2,
            'f3_rsi_zone':        f3,
            'f4_macd_momentum':   f4,
            'f5_adx_strength':    f5,
            'f6_stoch_confirm':   f6,
            'f7_bb_action':       f7,
            'adx_regime':         regime,
        }

    # ── Multi-timeframe confluence signal ─────────────────────────────────────

    def _multi_tf_signal(self, m15: dict, h1: dict, h4: dict) -> dict:
        scores = self._get_factor_scores(m15, h1, h4)

        # Sum absolute factor magnitudes (0-70; old system was 0-12)
        raw_score = (
            abs(scores['f1_h4_trend']) +
            abs(scores['f2_h1_trend']) +
            scores['f3_rsi_zone'] +
            scores['f4_macd_momentum'] +
            scores['f5_adx_strength'] +
            scores['f6_stoch_confirm'] +
            scores['f7_bb_action']
        )

        # Direction sign driven by H4 trend (F1)
        direction_sign = 1 if scores['f1_h4_trend'] >= 0 else -1
        signed_score   = int(raw_score * direction_sign)

        # ── ADX ranging penalty ──────────────────────────────────────────
        if scores['adx_regime'] == 'RANGING':
            signed_score = int(signed_score * 0.4)
            regime_note  = 'RANGING ADX<18 — score reduced'
        else:
            regime_note = ''

        max_possible = 70

        # ── Final decision ───────────────────────────────────────────────
        if signed_score >= 28:
            direction  = 'BUY' if direction_sign > 0 else 'SELL'
            confidence = min(abs(signed_score) / max_possible, 0.92)
        elif signed_score <= -28:
            direction  = 'SELL'
            confidence = min(abs(signed_score) / max_possible, 0.92)
        else:
            direction  = 'HOLD'
            confidence = 0.35

        # Build reasons from high-scoring factors
        reasons = []
        if abs(scores['f1_h4_trend'])   >= 8: reasons.append(f"H4 {h4['ema_trend']}")
        if abs(scores['f2_h1_trend'])   >= 8: reasons.append(f"H1 {h1['ema_trend']}")
        if scores['f3_rsi_zone']       >= 8: reasons.append(f"RSI {m15['rsi']:.0f} extreme")
        if scores['f4_macd_momentum'] >= 8: reasons.append(f"MACD {m15['macd_signal']}")
        if scores['f5_adx_strength']   >= 8: reasons.append(f"ADX strong ({m15['adx']:.0f})")
        if scores['f6_stoch_confirm']  >= 8: reasons.append(f"Stoch {m15['stoch_cross']}")
        if scores['f7_bb_action']      >= 8: reasons.append(f"BB {m15['bb_position']}")
        if regime_note:                  reasons.append(regime_note)

        return {
            'direction':     direction,
            'confidence':    round(confidence, 4),
            'reason':        ' | '.join(reasons) if reasons else 'No clear confluence',
            'score':         signed_score,
            'factor_scores': scores,
        }


    # ── AI reasoning ──────────────────────────────────────────────────────────

    def _ai_reasoning(self, symbol: str, tick, m15: dict, h1: dict, h4: dict,
                      base_signal: dict, candles: pd.DataFrame) -> dict:
        last_candles = candles.tail(8)[["time", "o", "h", "l", "c", "vol"]].to_string(index=False)

        # Detect key levels: recent swing highs/lows
        highs = candles["h"].values[-50:]
        lows  = candles["l"].values[-50:]
        res_level = round(float(np.max(highs)), 5)
        sup_level = round(float(np.min(lows)),  5)

        session = base_signal.get("session", "UNKNOWN")
        fs = base_signal.get("factor_scores", {})

        prompt = f"""Analyze this forex market data for {symbol} and give a trading decision.

=== TIMEFRAME CONFLUENCE ===
H4 (bias):  EMA trend={h4['ema_trend']}  ADX={h4['adx']:.0f}  RSI={h4['rsi']:.1f}
H1 (context): EMA trend={h1['ema_trend']}  ADX={h1['adx']:.0f}  RSI={h1['rsi']:.1f}  MACD={h1['macd_signal']}
M15 (entry): EMA trend={m15['ema_trend']}  ADX={m15['adx']:.0f}  RSI={m15['rsi']:.1f}

=== M15 ENTRY INDICATORS ===
Price:         Ask={tick.ask:.5f}  Bid={tick.bid:.5f}
EMA 20/50/200: {m15['ema20']} / {m15['ema50']} / {m15['ema200']}
MACD signal:   {m15['macd_signal']}  histogram={m15['macd_hist']:.6f}
Bollinger:     {m15['bb_position']}  squeeze={m15['bb_squeeze']}
Stochastic:    K={m15['stoch_k']:.0f}  D={m15['stoch_d']:.0f}  cross={m15['stoch_cross']}
Williams %R:   {m15['williams_r']:.0f}
ADX:           {m15['adx']:.0f}  +DI={m15['plus_di']:.0f}  -DI={m15['minus_di']:.0f}
ATR:           {m15['atr']}
Volume ratio:  {m15['vol_ratio']}x avg

=== KEY LEVELS (50-bar range) ===
Resistance: {res_level}
Support:    {sup_level}
20-bar change: {m15['price_change']:+.3f}%

=== MARKET SESSION ===
{session} session

=== PRE-AI FACTOR SCORES (0-10 each, sign in F1/F2 = direction) ===
F1 H4 Trend:     {fs['f1_h4_trend']:+.0f}  (positive=bullish, negative=bearish)
F2 H1 Trend:     {fs['f2_h1_trend']:+.0f}
F3 RSI Zone:     {fs['f3_rsi_zone']:.0f}/10  (higher=more extreme from 50)
F4 MACD Momentum: {fs['f4_macd_momentum']:.0f}/10
F5 ADX Strength: {fs['f5_adx_strength']:.0f}/10  (>=7 = strong trend)
F6 Stoch Confirm: {fs['f6_stoch_confirm']:.0f}/10
F7 BB Action:    {fs['f7_bb_action']:.0f}/10
Regime:          {fs['adx_regime']}  (TRENDING if ADX >= 18)

=== INDICATOR-BASED SIGNAL ===
Direction: {base_signal['direction']} | Raw Score: {base_signal['score']} | Confidence: {base_signal['confidence']:.0%}
Reasons: {base_signal['reason']}

=== RECENT M15 CANDLES ===
{last_candles}

Rules:
- Only BUY if H4 trend is bullish (F1 > 0) AND at least 4 factors score >= 6
- Only SELL if H4 trend is bearish (F1 < 0) AND at least 4 factors score >= 6
- HOLD if timeframes conflict, ADX < 18 (RANGING), or insufficient confluence
- CRITICAL: Do NOT cluster confidence around 0.65-0.75. Mediocre signals (no factor >= 8) should score 0.40-0.55. Exceptional signals (>= 5 factors >= 8) should score 0.78-0.92.
- In RANGING markets (ADX < 18), reduce confidence by 0.10-0.20 regardless of other factors
- Be conservative: when in doubt → HOLD

Respond with ONLY this JSON (no markdown):
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
                }, timeout=25)    # 25s per attempt — fast fail for cloud model
                resp.raise_for_status()

                # Guard against empty body (cloud model occasionally returns nothing)
                raw = resp.text.strip()
                if not raw:
                    raise ValueError("Empty response from Ollama")

                text = resp.json()["message"]["content"].strip()
                # Strip accidental markdown fences
                if "```" in text:
                    parts = text.split("```")
                    for part in parts:
                        part = part.strip().lstrip("json").strip()
                        if part.startswith("{"):
                            text = part
                            break
                data = json.loads(text)
                ai_direction = data.get("direction", "HOLD")

                # Step 5: cross-validate AI direction against H4 trend (F1)
                f1_score = base_signal.get("factor_scores", {}).get("f1_h4_trend", 0)
                if ai_direction in ("BUY", "SELL") and f1_score != 0:
                    h4_bullish = f1_score > 0
                    ai_agrees  = (ai_direction == "BUY" and h4_bullish) or (ai_direction == "SELL" and not h4_bullish)
                    if not ai_agrees:
                        data["confidence"] = max(float(data.get("confidence", 0.35)) * 0.5, 0.25)
                        data["reason"] = f"[H4 override] {data.get('reason', '')}"

                return {
                    "direction":  data.get("direction", "HOLD"),
                    "confidence": float(data.get("confidence", 0.35)),
                    "reason":     data.get("reason", "AI analysis complete."),
                }
            except Exception as e:
                if attempt == 0 and "timed out" in str(e).lower():
                    print(f"  ⚠️  Ollama timeout, retrying once...")
                    continue
                print(f"  ⚠️  Ollama error: {e} — using indicator signal.")
                return base_signal
        return base_signal
