"""
position_scaler.py — AI-Based Position Scaling (Pyramiding)

Adds to winning positions when:
  1. The trade is profitable by a minimum threshold
  2. AI analysis confirms the trend continues
  3. Account balance/margin supports it
  4. Max scale count not exceeded

Inspired by Hermes skill-driven decision pattern.
"""

import json
import sqlite3
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("scaler")

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "minimax-m2.7:cloud"
DB_PATH      = Path(__file__).parent / "trade_memory.db"

# ── Scaling config (mirrors SKILL.md) ────────────────────────────────────────
SCALE_CFG = {
    "min_profit_pips":     10,      # position must be up this many pips (was 15)
    "min_confidence":      0.55,    # AI confidence threshold (was 0.65)
    "max_scales_per_trade": 2,      # hard cap on adds per ticket
    "min_free_margin_pct": 20,      # free margin must be > 20% of balance (was 30)
    "max_total_risk_pct":  5.0,     # all open positions < 5% of balance (was 3)
    "scale_lot_fraction":  0.5,     # scale lot = original × 0.5
    "blocked_sessions":    ["MARKET_CLOSED"],   # allow scaling in all open sessions
}

# ── Scale tracking table ─────────────────────────────────────────────────────

def _ensure_scale_table():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scale_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                parent_ticket TEXT NOT NULL,
                scale_ticket  TEXT,
                symbol      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                scale_number INTEGER NOT NULL,
                lot         REAL,
                entry_price REAL,
                sl          REAL,
                ai_confidence REAL,
                ai_reason   TEXT,
                outcome     TEXT
            )
        """)

def _count_scales(parent_ticket: str) -> int:
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM scale_history WHERE parent_ticket=? AND scale_ticket IS NOT NULL",
                (str(parent_ticket),)
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0

def _record_scale(parent_ticket, scale_ticket, symbol, direction,
                  scale_num, lot, price, sl, confidence, reason):
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                INSERT INTO scale_history
                (ts, parent_ticket, scale_ticket, symbol, direction,
                 scale_number, lot, entry_price, sl, ai_confidence, ai_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, str(parent_ticket), str(scale_ticket), symbol,
                  direction, scale_num, lot, price, sl, confidence, reason))
    except Exception as e:
        log.warning(f"[SCALER] Failed to record scale: {e}")

# ── Core scaling logic ────────────────────────────────────────────────────────

class PositionScaler:
    """
    Evaluates open positions and scales into winning ones when AI confirms.
    Called each analysis cycle from continuous_trader.py.
    """

    def __init__(self):
        _ensure_scale_table()

    def evaluate(self, positions: list, account, signal_data: dict,
                 sym_cfg: dict, session: str, bridge) -> list:
        """
        Check all open positions for scaling opportunities.
        Returns list of scale orders placed {ticket, lot, direction, reason}.
        """
        scaled = []

        if session in SCALE_CFG["blocked_sessions"]:
            return scaled

        balance  = getattr(account, "balance", 1000.0)
        equity   = getattr(account, "equity",  1000.0)
        margin   = getattr(account, "margin",  0.0)
        free_margin = getattr(account, "margin_free", balance)

        # Guard: equity must be > 97% of balance (not losing heavily elsewhere)
        if equity < balance * 0.97:
            log.debug(f"[SCALER] Skipping — equity drawdown {(balance-equity)/balance:.1%}")
            return scaled

        # Guard: free margin must be > min_free_margin_pct
        free_pct = (free_margin / balance * 100) if balance > 0 else 0
        if free_pct < SCALE_CFG["min_free_margin_pct"]:
            log.debug(f"[SCALER] Skipping — free margin {free_pct:.1f}% < {SCALE_CFG['min_free_margin_pct']}%")
            return scaled

        # Guard: total open risk check
        total_risk_pct = ((balance - equity + margin * 0.01) / balance * 100)
        if total_risk_pct >= SCALE_CFG["max_total_risk_pct"]:
            log.debug(f"[SCALER] Skipping — total risk {total_risk_pct:.1f}%")
            return scaled

        pip        = sym_cfg["pip"]
        direction  = signal_data.get("direction", "HOLD")
        confidence = signal_data.get("confidence", 0.0)
        score      = signal_data.get("score", 0.0)   # raw signed score
        disp       = sym_cfg["display"]

        # Accept HOLD/low-conf signals if score aligns with a profitable position
        # Score-based scaling: when position is in profit, use raw score as confirmation
        if direction == "HOLD":
            if score > 4:
                direction = "BUY"
                confidence = max(confidence, 0.52)
            elif score < -4:
                direction = "SELL"
                confidence = max(confidence, 0.52)
            else:
                return scaled  # truly neutral — skip

        # If AI says directional but low confidence, check if score strongly confirms
        if confidence < SCALE_CFG["min_confidence"]:
            # Allow scaling on score-confirmation alone (no AI required)
            # BUY: score > 6, SELL: score < -6
            if direction == "BUY" and score > 6:
                confidence = 0.55   # score-confirmed
            elif direction == "SELL" and score < -6:
                confidence = 0.55   # score-confirmed
            else:
                return scaled

        for pos in positions:
            pos_dir    = "BUY" if getattr(pos, "type", 1) == 0 else "SELL"
            profit     = getattr(pos, "profit", 0.0)
            open_price = getattr(pos, "price_open", 0.0)
            volume     = getattr(pos, "volume", 0.01)
            ticket     = getattr(pos, "ticket", "?")
            pos_sl     = getattr(pos, "sl", 0.0)
            pos_tp     = getattr(pos, "tp", 0.0)

            # Must be in same direction as AI signal
            if pos_dir != direction:
                continue

            # Must be profitable enough
            profit_pips = profit / (pip * 10 * volume) if (pip * 10 * volume) > 0 else 0
            if profit_pips < SCALE_CFG["min_profit_pips"]:
                log.debug(f"[SCALER] {disp} #{ticket}: only {profit_pips:.1f} pips profit "
                          f"(need {SCALE_CFG['min_profit_pips']})")
                continue

            # Max scales check
            scales_done = _count_scales(ticket)
            if scales_done >= SCALE_CFG["max_scales_per_trade"]:
                log.debug(f"[SCALER] {disp} #{ticket}: already scaled {scales_done} times")
                continue

            # Ask AI to confirm scaling
            ai_ok, ai_conf, ai_reason = self._ai_confirm_scale(
                disp, direction, signal_data, profit_pips, scales_done + 1)

            if not ai_ok:
                log.info(f"[SCALER] {disp} #{ticket}: AI says no scale — {ai_reason}")
                continue

            # Compute scale order
            scale_lot   = round(max(volume * SCALE_CFG["scale_lot_fraction"], 0.01), 2)
            digits      = 2 if pip >= 0.01 else 5

            try:
                tick = bridge.get_tick(sym_cfg["broker"])
                price = tick.ask if direction == "BUY" else tick.bid
            except Exception as e:
                log.warning(f"[SCALER] Can't get tick: {e}")
                continue

            # SL for scale = original entry price (breakeven on parent)
            # If BUY: SL just below open_price; if SELL: SL just above open_price
            buffer = pip * 2
            if direction == "BUY":
                scale_sl = round(open_price - buffer, digits)
            else:
                scale_sl = round(open_price + buffer, digits)

            # TP same as parent
            scale_tp = pos_tp

            order = {
                "symbol":    sym_cfg["broker"],
                "direction": direction,
                "lot":       scale_lot,
                "price":     price,
                "sl":        scale_sl,
                "tp":        scale_tp,
                "comment":   f"SCALE{scales_done+1}-{ticket}",
            }

            log.info(f"[SCALER] 📈 Scaling {disp} #{ticket} "
                     f"{direction} +{scale_lot}lot @ {price:.2f} "
                     f"(profit={profit_pips:.1f}pips, scale#{scales_done+1}, "
                     f"conf={ai_conf:.0%})")

            try:
                result = bridge.place_order(order)
                if result and hasattr(result, "order"):
                    scale_ticket = result.order
                    _record_scale(ticket, scale_ticket, disp, direction,
                                  scales_done + 1, scale_lot, price,
                                  scale_sl, ai_conf, ai_reason)
                    scaled.append({
                        "parent_ticket": ticket,
                        "scale_ticket":  scale_ticket,
                        "lot":           scale_lot,
                        "direction":     direction,
                        "reason":        ai_reason,
                    })
                    log.info(f"[SCALER] ✅ Scale order #{scale_ticket} placed")
                else:
                    log.warning(f"[SCALER] Scale order failed for #{ticket}")
            except Exception as e:
                log.warning(f"[SCALER] Order error: {e}")

        return scaled

    def _ai_confirm_scale(self, symbol: str, direction: str,
                          signal_data: dict, profit_pips: float,
                          scale_num: int) -> tuple:
        """
        Ask Ollama if scaling is justified right now.
        Returns (ok: bool, confidence: float, reason: str)
        """
        indicators = signal_data.get("indicators", {})
        factors    = signal_data.get("factor_scores", {})
        reason_txt = signal_data.get("reason", "")

        prompt = f"""A trade is open and in profit. Should we ADD to this position?

Symbol: {symbol}
Direction: {direction}
Current profit: {profit_pips:.1f} pips
Scale number: {scale_num} (adding another partial position)

Current market analysis:
- Signal: {direction} @ {signal_data.get('confidence', 0):.0%} confidence
- Reasons: {reason_txt}
- RSI: {indicators.get('rsi', 0):.1f}
- ADX: {indicators.get('adx', 0):.1f} (trend strength)
- MACD: {indicators.get('macd_signal', '')}
- EMA trend: {indicators.get('ema_trend', '')}
- ATR: {indicators.get('atr', 0)}
- Stochastic K: {indicators.get('stoch_k', 0):.0f}

Factor scores (signed, positive=bullish):
F1 H4: {factors.get('f1_h4_trend', 0):+.1f}
F2 H1: {factors.get('f2_h1_trend', 0):+.1f}
F3 RSI: {factors.get('f3_rsi_zone', 0):+.1f}
F4 MACD: {factors.get('f4_macd_momentum', 0):+.1f}
F5 ADX: {factors.get('f5_adx_strength', 0):+.1f}

Rules for scaling:
- Only add if trend is STRONG (ADX > 25, majority of factors agree with direction)
- Do NOT add if RSI is overbought/oversold against the direction
- Do NOT add if MACD is diverging against the direction
- Do NOT add if price is near resistance (BUY) or support (SELL)
- Prefer adding during momentum, not during pullbacks

Answer: should we add to this {direction} position?
Respond with ONLY JSON (no markdown):
{{"scale": true/false, "confidence": 0.0-1.0, "reason": "1 sentence"}}"""

        try:
            resp = requests.post(OLLAMA_URL, json={
                "model":  OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content":
                     "You are a risk-conscious trading advisor. Be conservative with scaling."},
                    {"role": "user", "content": prompt},
                ],
            }, timeout=20)
            resp.raise_for_status()
            text = resp.json()["message"]["content"].strip()
            if "```" in text:
                for part in text.split("```"):
                    part = part.strip().lstrip("json").strip()
                    if part.startswith("{"):
                        text = part
                        break
            data = json.loads(text)
            ok   = bool(data.get("scale", False))
            conf = float(data.get("confidence", 0.0))
            rsn  = data.get("reason", "AI decision")
            return ok, conf, rsn
        except Exception as e:
            log.warning(f"[SCALER] AI confirm error: {e}")
            # Fallback: allow scale if base signal is strong
            fallback_ok = signal_data.get("confidence", 0) >= 0.70
            return fallback_ok, signal_data.get("confidence", 0), "Fallback: base signal"

    def get_scale_stats(self) -> dict:
        """Return scaling performance stats."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM scale_history").fetchall()
                total = len(rows)
                placed = sum(1 for r in rows if r["scale_ticket"])
                return {"total_attempts": total, "placed": placed,
                        "symbols": list({r["symbol"] for r in rows})}
        except Exception:
            return {"total_attempts": 0, "placed": 0, "symbols": []}


if __name__ == "__main__":
    scaler = PositionScaler()
    stats = scaler.get_scale_stats()
    print(f"Scale history: {stats}")
