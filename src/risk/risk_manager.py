"""
RiskManager — FIXED 2026-05-03
  - Dynamic position sizing from account balance + risk %
  - ATR-based SL/TP (preferred over fixed pips)
  - Correct decimal digits per symbol (gold=2, silver=3)
  - Session-aware lot sizing
  - Daily drawdown protection
  - Trailing stop / breakeven management

BUGS FIXED:
  - build_order() was using `info = None` → always hardcoded fallback
  - _position_size() had `account = None` → dynamic sizing NEVER ran
  - digits was 5 for gold (should be 2) → order rejection risk
  - ATR-based sl_atr_mult / tp_atr_mult from config were IGNORED
  - _position_size() referenced undefined `mt5` variable
"""

import logging
from datetime import date

log = logging.getLogger("risk_mgr")

# ── Symbol metadata (hardcoded for gold/silver — primary instruments) ────────
_SYMBOL_META = {
    "GOLD.i#":   {"digits": 2, "pip": 0.10, "contract_size": 100,
                  "volume_min": 0.01, "volume_max": 10.0, "volume_step": 0.01},
    "SILVER.i#": {"digits": 3, "pip": 0.01, "contract_size": 5000,
                  "volume_min": 0.01, "volume_max": 10.0, "volume_step": 0.01},
}


class RiskManager:
    def __init__(self, config: dict):
        self.config = config
        # Drawdown tracking (resets each UTC day)
        self._drawdown_date          = date.today()
        self._drawdown_start_balance = None

    def build_order(self, signal: dict, tick, candles,
                    account_info=None, atr: float = 0.0) -> dict:
        """Build a trade order with proper position sizing and SL/TP.

        Args:
            signal:       Analysis result with direction, confidence, session, indicators
            tick:         Current bid/ask tick
            candles:      DataFrame (used for ATR fallback if atr not provided)
            account_info: Account info object (balance, equity, etc.)
            atr:          Current ATR value in price units (e.g. 3.50 for gold)
        """
        direction = signal["direction"]
        symbol    = self.config["symbol"]

        # ── Symbol metadata (digits, pip, contract size) ─────────────────
        meta = _SYMBOL_META.get(symbol, {})
        digits = meta.get("digits", 2)
        pip    = meta.get("pip", self._pip_size(symbol))
        contract_size = meta.get("contract_size", 100)

        # ── Price ────────────────────────────────────────────────────────
        price = tick.ask if direction == "BUY" else tick.bid

        # ── ATR-based dynamic SL/TP (preferred over fixed pips) ──────────
        sl_atr_mult = self.config.get("sl_atr_mult", 1.5)
        tp_atr_mult = self.config.get("tp_atr_mult", 4.5)

        if atr > 0:
            # ATR is in price units (e.g. 3.50 for gold)
            # Convert to pips: atr_pips = atr / pip
            atr_pips = atr / pip
            sl_pips = round(atr_pips * sl_atr_mult, 1)
            tp_pips = round(atr_pips * tp_atr_mult, 1)

            # Clamp SL/TP to sensible bounds
            sl_pips = max(15, min(sl_pips, 80))   # gold: 15-80 pip SL
            tp_pips = max(30, min(tp_pips, 200))   # gold: 30-200 pip TP

            log.info(f"[RISK] ATR-based stops: ATR={atr:.2f} ({atr_pips:.1f}p) "
                     f"→ SL={sl_pips:.0f}p TP={tp_pips:.0f}p (R:R={tp_pips/sl_pips:.1f})")
        else:
            # Fallback to fixed pips from config
            sl_pips = self.config["sl_pips"]
            tp_pips = self.config["tp_pips"]
            log.debug(f"[RISK] Fixed stops: SL={sl_pips}p TP={tp_pips}p")

        # ── Dynamic position sizing from account balance ─────────────────
        lot = self._compute_lot(symbol, account_info, sl_pips, pip, contract_size)

        # ── Session-aware sizing multiplier ──────────────────────────────
        session    = signal.get("session", "LONDON")
        multiplier = self.config.get("session_size_multipliers", {}).get(session, 1.0)
        lot = round(max(lot * multiplier, 0.01), 2)

        # ── Compute SL/TP prices ─────────────────────────────────────────
        if direction == "BUY":
            sl = round(price - sl_pips * pip, digits)
            tp = round(price + tp_pips * pip, digits)
        else:
            sl = round(price + sl_pips * pip, digits)
            tp = round(price - tp_pips * pip, digits)

        return {
            "symbol":    symbol,
            "direction": direction,
            "lot":       lot,
            "price":     round(price, digits),
            "sl":        sl,
            "tp":        tp,
            "sl_pips":   sl_pips,
            "tp_pips":   tp_pips,
            "comment":   f"AI-{signal['confidence']:.0%}",
        }

    def _compute_lot(self, symbol: str, account_info, sl_pips: float,
                     pip: float, contract_size: float) -> float:
        """Calculate position size from account balance and risk %.

        Formula: lot = risk_usd / (sl_pips × pip_value_per_lot)
        Where:   pip_value_per_lot = pip × contract_size
                 Gold:   0.10 × 100  = $10/pip/lot
                 Silver: 0.01 × 5000 = $50/pip/lot
        """
        if account_info is None:
            log.debug("[RISK] No account info — using config lot_size")
            return self.config.get("lot_size", 0.01)

        balance = getattr(account_info, "balance", 0)
        if balance <= 0:
            return self.config.get("lot_size", 0.01)

        risk_pct = self.config.get("max_risk_pct", 0.5)
        risk_usd = balance * (risk_pct / 100)

        pip_value_per_lot = pip * contract_size  # Gold: $10, Silver: $50

        if pip_value_per_lot <= 0 or sl_pips <= 0:
            return self.config.get("lot_size", 0.01)

        raw_lot = risk_usd / (sl_pips * pip_value_per_lot)

        # Clamp to broker limits
        meta = _SYMBOL_META.get(symbol, {})
        vol_min  = meta.get("volume_min", 0.01)
        vol_max  = meta.get("volume_max", 10.0)
        vol_step = meta.get("volume_step", 0.01)

        lot = max(vol_min, min(raw_lot, vol_max))
        lot = round(round(lot / vol_step) * vol_step, 2)

        log.info(f"[RISK] Position sizing: balance=${balance:.2f} risk={risk_pct}% "
                 f"risk_usd=${risk_usd:.2f} SL={sl_pips:.0f}p pipVal=${pip_value_per_lot}/lot "
                 f"→ lot={lot}")
        return lot

    def check_daily_drawdown(self, bridge) -> bool:
        """
        Returns True if trading should HALT due to daily drawdown limit.
        Resets at midnight UTC.
        """
        today = date.today()
        if today != self._drawdown_date:
            self._drawdown_date          = today
            self._drawdown_start_balance = None

        try:
            info = bridge.get_account_info()
        except Exception:
            return False
        if info is None:
            return False

        if self._drawdown_start_balance is None:
            self._drawdown_start_balance = info.balance
            return False

        if self._drawdown_start_balance <= 0:
            return False

        equity      = getattr(info, "equity", info.balance)
        drawdown_pct = (self._drawdown_start_balance - equity) / self._drawdown_start_balance * 100
        limit        = self.config.get("max_daily_drawdown_pct", 2.0)
        if drawdown_pct >= limit:
            log.warning(f"DRAWDOWN LIMIT HIT: {drawdown_pct:.2f}% (limit {limit}%). "
                        f"Trading halted for today.")
            return True
        return False

    def manage_open_positions(self, bridge, symbol: str) -> list:
        """
        Move SL to breakeven for open positions that have reached +1R (sl_pips profit).
        Returns list of tickets modified.
        """
        try:
            positions = bridge.get_open_positions(symbol)
        except Exception:
            return []

        if not positions:
            return []

        pip     = self._pip_size(symbol)
        sl_pips = self.config["sl_pips"]
        digits  = _SYMBOL_META.get(symbol, {}).get("digits", 2)
        modified = []

        for pos in positions:
            try:
                price_open = getattr(pos, "price_open", None) or getattr(pos, "openPrice", None)
                current_sl = getattr(pos, "sl", None) or getattr(pos, "stopLoss", None)
                pos_type   = getattr(pos, "type", None)   # 0=BUY, 1=SELL
                ticket     = getattr(pos, "ticket", None) or getattr(pos, "id", None)

                if price_open is None or ticket is None:
                    continue

                tick = bridge.get_tick(symbol)
                if tick is None:
                    continue

                if pos_type == 0:   # BUY
                    current_price = tick.bid
                    profit_pip    = (current_price - price_open) / pip
                    be_sl         = round(price_open + (2 * pip), digits)
                    if profit_pip >= sl_pips and (current_sl is None or current_sl < be_sl):
                        if bridge.modify_position(ticket, sl=be_sl):
                            modified.append(ticket)
                elif pos_type == 1:  # SELL
                    current_price = tick.ask
                    profit_pip    = (price_open - current_price) / pip
                    be_sl         = round(price_open - (2 * pip), digits)
                    if profit_pip >= sl_pips and (current_sl is None or current_sl > be_sl):
                        if bridge.modify_position(ticket, sl=be_sl):
                            modified.append(ticket)
            except Exception:
                continue

        return modified

    def _pip_size(self, symbol: str) -> float:
        """Return pip size for a given symbol."""
        sym_upper = symbol.upper()
        if "GOLD" in sym_upper or "XAU" in sym_upper:
            return 0.10
        if "SILVER" in sym_upper or "XAG" in sym_upper:
            return 0.01
        if "JPY" in sym_upper:
            return 0.01
        return 0.0001
