"""
RiskManager
  - Calculates position size from account risk %
  - Builds order parameters with SL/TP
  - Session-aware lot sizing
  - Daily drawdown protection
  - Trailing stop / breakeven management
"""

import sys
from datetime import date
if sys.platform == "win32":
    import MetaTrader5 as mt5
else:
    import mock_mt5 as mt5   # Linux/Mac mock


class RiskManager:
    def __init__(self, config: dict):
        self.config = config
        # Drawdown tracking (resets each UTC day)
        self._drawdown_date          = date.today()
        self._drawdown_start_balance = None

    def build_order(self, signal: dict, tick, candles) -> dict:
        direction = signal["direction"]
        symbol    = self.config["symbol"]

        info = mt5.symbol_info(symbol)
        if not info:
            lot = self.config["lot_size"]
        else:
            lot = self._position_size(symbol, info, tick)

        # Session-aware sizing multiplier
        session    = signal.get("session", "LONDON")
        multiplier = self.config.get("session_size_multipliers", {}).get(session, 1.0)
        lot        = round(max(lot * multiplier, 0.01), 2)

        pip   = self._pip_size(symbol)
        price = tick.ask if direction == "BUY" else tick.bid
        digits = info.digits if info else 5

        if direction == "BUY":
            sl = round(price - self.config["sl_pips"] * pip, digits)
            tp = round(price + self.config["tp_pips"] * pip, digits)
        else:
            sl = round(price + self.config["sl_pips"] * pip, digits)
            tp = round(price - self.config["tp_pips"] * pip, digits)

        return {
            "symbol":    symbol,
            "direction": direction,
            "lot":       lot,
            "price":     price,
            "sl":        sl,
            "tp":        tp,
            "comment":   f"AI-{signal['confidence']:.0%}",
        }

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
            print(f"  DRAWDOWN LIMIT HIT: {drawdown_pct:.2f}% (limit {limit}%). Trading halted for today.")
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
                    be_sl         = round(price_open + (2 * pip), 5)   # 2-pip buffer above entry
                    if profit_pip >= sl_pips and (current_sl is None or current_sl < be_sl):
                        if bridge.modify_position(ticket, sl=be_sl):
                            modified.append(ticket)
                elif pos_type == 1:  # SELL
                    current_price = tick.ask
                    profit_pip    = (price_open - current_price) / pip
                    be_sl         = round(price_open - (2 * pip), 5)   # 2-pip buffer below entry
                    if profit_pip >= sl_pips and (current_sl is None or current_sl > be_sl):
                        if bridge.modify_position(ticket, sl=be_sl):
                            modified.append(ticket)
            except Exception:
                continue

        return modified

    def _position_size(self, symbol: str, info, tick) -> float:
        account = mt5.account_info()
        if not account:
            return self.config["lot_size"]
        risk_amount = account.balance * (self.config["max_risk_pct"] / 100)
        pip         = self._pip_size(symbol)
        sl_pips     = self.config["sl_pips"]

        # Pip value per lot in quote currency
        pip_value_quote = pip * info.trade_contract_size

        # Convert quote currency pip value to account currency
        quote_currency   = symbol[3:6] if len(symbol) >= 6 else ""
        account_currency = account.currency

        if quote_currency and quote_currency != account_currency:
            conv_symbol = quote_currency + account_currency
            conv_tick   = mt5.symbol_info_tick(conv_symbol)
            if conv_tick is None:
                conv_symbol = account_currency + quote_currency
                conv_tick   = mt5.symbol_info_tick(conv_symbol)
                if conv_tick:
                    pip_value_account = pip_value_quote / conv_tick.ask
                else:
                    pip_value_account = pip_value_quote
            else:
                pip_value_account = pip_value_quote * conv_tick.bid
        else:
            pip_value_account = pip_value_quote

        if pip_value_account == 0:
            return self.config["lot_size"]

        lots = risk_amount / (sl_pips * pip_value_account)
        lots = max(info.volume_min, min(lots, info.volume_max))
        step = info.volume_step
        lots = round(round(lots / step) * step, 2)
        return lots

    def _pip_size(self, symbol: str) -> float:
        """Return pip size for a given symbol."""
        # Gold / Silver (2-digit quotes like 4714.57) → pip = 0.10
        sym_upper = symbol.upper()
        if "GOLD" in sym_upper or "XAU" in sym_upper:
            return 0.10
        if "SILVER" in sym_upper or "XAG" in sym_upper:
            return 0.01
        # JPY pairs and 3-digit Forex → pip = 0.01
        info = mt5.symbol_info(symbol)
        if info and info.digits in (3, 5):
            return 10 ** -(info.digits - 1)
        # For unknown symbols, use mock_mt5 or default
        if "JPY" in sym_upper:
            return 0.01
        return 0.0001
