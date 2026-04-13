"""
MT5Bridge — wraps MetaTrader 5 Python API
Handles: connect/disconnect, price feeds, candles, orders, positions.
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime

if sys.platform == "win32":
    import MetaTrader5 as mt5
else:
    import mock_mt5 as mt5   # Linux/Mac: synthetic data mock

TIMEFRAME_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

class MT5Bridge:
    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        """Initialize MT5 connection."""
        if not mt5.initialize():
            print(f"  MT5 init error: {mt5.last_error()}")
            return False
        self.connected = True
        return True

    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame | None:
        """Fetch OHLCV candles as a DataFrame."""
        tf = TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M15)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.rename(columns={"open": "o", "high": "h", "low": "l",
                                 "close": "c", "tick_volume": "vol"})
        return df

    def get_tick(self, symbol: str):
        """Get latest bid/ask tick."""
        return mt5.symbol_info_tick(symbol)

    def get_symbol_info(self, symbol: str):
        return mt5.symbol_info(symbol)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_info(self):
        return mt5.account_info()

    def print_account_info(self):
        info = self.get_account_info()
        if not info:
            print("  Could not fetch account info.")
            return
        print(f"  Account:  #{info.login}  ({info.server})")
        print(f"  Balance:  {info.balance:.2f} {info.currency}")
        print(f"  Equity:   {info.equity:.2f}")
        print(f"  Margin:   {info.margin:.2f}  |  Free: {info.margin_free:.2f}")
        print(f"  Leverage: 1:{info.leverage}")

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: str | None = None):
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        return list(positions) if positions else []

    def print_open_positions(self):
        positions = self.get_open_positions()
        if not positions:
            print("\n  No open positions.")
            return
        print(f"\n  Open positions ({len(positions)}):")
        for p in positions:
            direction = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
            print(f"  #{p.ticket}  {p.symbol}  {direction}  {p.volume} lots  "
                  f"open@{p.price_open:.5f}  profit:{p.profit:.2f}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, params: dict):
        """
        params keys: symbol, direction, lot, sl, tp, comment
        direction: 'BUY' or 'SELL'
        """
        symbol = params["symbol"]
        info   = self.get_symbol_info(symbol)
        if not info:
            print(f"  Symbol {symbol} not found.")
            return None

        order_type = mt5.ORDER_TYPE_BUY if params["direction"] == "BUY" else mt5.ORDER_TYPE_SELL
        price      = mt5.symbol_info_tick(symbol).ask if params["direction"] == "BUY" \
                     else mt5.symbol_info_tick(symbol).bid

        request = {
            "action":        mt5.TRADE_ACTION_DEAL,
            "symbol":        symbol,
            "volume":        params["lot"],
            "type":          order_type,
            "price":         price,
            "sl":            params["sl"],
            "tp":            params["tp"],
            "deviation":     10,
            "magic":         202600,
            "comment":       params.get("comment", "MT5-AI-Bot"),
            "type_time":     mt5.ORDER_TIME_GTC,
            "type_filling":  mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"  Order error: retcode={result.retcode}  {result.comment}")
            return None
        return result

    def close_position(self, ticket: int):
        """Close a position by ticket number."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            print(f"  Position #{ticket} not found.")
            return False
        pos = position[0]
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price      = mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.ORDER_TYPE_BUY \
                     else mt5.symbol_info_tick(pos.symbol).ask
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         order_type,
            "position":     ticket,
            "price":        price,
            "deviation":    10,
            "magic":        202600,
            "comment":      "MT5-AI-Bot-Close",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
