"""
position_scaler.py — NVIDIA-signal-based Position Scaling (Pyramiding)

Adds to winning positions when:
  1. The trade is profitable by a minimum threshold
  2. The main NVIDIA-confirmed signal says the trend continues
  3. Account balance/margin supports it
  4. Max scale count not exceeded

Inspired by Hermes skill-driven decision pattern.
"""

import json
from core.logger_factory import get_logger
from datetime import datetime, timezone
from core.supabase_db import SupabaseDB


log = get_logger("scaler")

# ── Scaling config (mirrors SKILL.md) ────────────────────────────────────────
SCALE_CFG = {
    "min_profit_pips":     15,      # position must be up this many pips before scaling
    "min_confidence":      0.65,    # AI confidence threshold (must match main entry threshold)
    "max_scales_per_trade": 2,      # hard cap on adds per ticket
    "min_free_margin_pct": 30,      # free margin must be > 30% of balance
    "max_total_risk_pct":  3.0,     # all open positions margin < 3% of balance
    "scale_lot_fraction":  0.5,     # scale lot = original × 0.5
    "blocked_sessions":    ["MARKET_CLOSED"],   # allow scaling in all open sessions
}

# ── Scale tracking table ─────────────────────────────────────────────────────

def _ensure_scale_table():
    return None

def _count_scales(parent_ticket: str) -> int:
    try:
        events = SupabaseDB().get_live_events(limit=500)
        return sum(
            1
            for event in events
            if event.get("event_type") == "scale_order"
            and str((event.get("payload") or {}).get("parent_ticket")) == str(parent_ticket)
            and (event.get("payload") or {}).get("scale_ticket")
        )
    except Exception as e:
        try: log.debug(f'Caught exception: {e}')
        except: pass
        return 0

def _record_scale(parent_ticket, scale_ticket, symbol, direction,
                  scale_num, lot, price, sl, confidence, reason):
    ts = datetime.now(timezone.utc).isoformat()
    try:
        SupabaseDB().log_runtime_event(
            "scale_order",
            {
                "ts": ts,
                "parent_ticket": str(parent_ticket),
                "scale_ticket": str(scale_ticket),
                "direction": direction,
                "scale_number": scale_num,
                "lot": lot,
                "entry_price": price,
                "sl": sl,
                "ai_confidence": confidence,
                "ai_reason": reason,
            },
            source="position_scaler",
            symbol=symbol,
        )
    except Exception as e:
        log.warning(f"[SCALER] Failed to record scale: {e}")

# ── Core scaling logic ────────────────────────────────────────────────────────

class PositionScaler:
    """
    Evaluates open positions and scales into winning ones when the main
    NVIDIA-confirmed signal remains aligned.
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

        # Guard: total open risk check — margin used as % of balance
        total_risk_pct = (margin / balance * 100) if balance > 0 else 0
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
            contract_size = sym_cfg.get("contract_size", 100)
            profit_pips = profit / (pip * contract_size * volume) if (pip * contract_size * volume) > 0 else 0
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
        Confirm scaling from the main NVIDIA-confirmed market signal.

        The project uses NVIDIA API as the single AI decision source. This
        scaler does not make a second AI call; it consumes the already
        confirmed signal_data produced by analyzer.py.
        Returns (ok: bool, confidence: float, reason: str)
        """
        indicators = signal_data.get("indicators", {})
        factors    = signal_data.get("factor_scores", {})
        confidence = float(signal_data.get("confidence", 0) or 0)
        signal_direction = signal_data.get("direction", direction)
        adx = float(indicators.get("adx", 0) or 0)
        rsi = float(indicators.get("rsi", 50) or 50)
        macd = str(indicators.get("macd_signal", "")).upper()
        score = float(signal_data.get("score", 0) or 0)

        if signal_direction != direction:
            return False, confidence, "NVIDIA signal no longer aligns with parent trade"
        if confidence < SCALE_CFG["min_confidence"]:
            return False, confidence, "NVIDIA confidence below scaling gate"
        if adx < 25:
            return False, confidence, "ADX below trend-strength gate"
        if direction == "BUY" and (rsi > 72 or "BEAR" in macd or score < 0):
            return False, confidence, "BUY scale blocked by RSI/MACD/score divergence"
        if direction == "SELL" and (rsi < 28 or "BULL" in macd or score > 0):
            return False, confidence, "SELL scale blocked by RSI/MACD/score divergence"

        aligned_factors = sum(
            1
            for value in factors.values()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and ((direction == "BUY" and value > 0) or (direction == "SELL" and value < 0))
        )
        if aligned_factors < 4:
            return False, confidence, "Insufficient factor alignment for scaling"

        return True, confidence, "NVIDIA-confirmed signal remains aligned for scaling"

    def get_scale_stats(self) -> dict:
        """Return scaling performance stats."""
        try:
            rows = [
                e for e in SupabaseDB().get_live_events(limit=500)
                if e.get("event_type") == "scale_order"
            ]
            placed = sum(1 for e in rows if (e.get("payload") or {}).get("scale_ticket"))
            symbols = sorted({e.get("symbol") for e in rows if e.get("symbol")})
            return {"total_attempts": len(rows), "placed": placed, "symbols": symbols}
        except Exception as e:
            try: log.debug(f'Caught exception: {e}')
            except: pass
            return {"total_attempts": 0, "placed": 0, "symbols": []}
