"""
mock_mt5.py — Linux/Mac stand-in for the MetaTrader5 Python package.
Generates realistic synthetic forex data so the bot can run and be tested
on any OS without a live MT5 terminal.
"""
import time
import random
import numpy as np
from types import SimpleNamespace

# ── MT5 Constants (match real values) ────────────────────────────────────────
TIMEFRAME_M1  = 1
TIMEFRAME_M5  = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1  = 60
TIMEFRAME_H4  = 240
TIMEFRAME_D1  = 1440

ORDER_TYPE_BUY   = 0
ORDER_TYPE_SELL  = 1
TRADE_ACTION_DEAL   = 1
ORDER_TIME_GTC      = 0
ORDER_FILLING_IOC   = 1
TRADE_RETCODE_DONE  = 10009

# ── Internal state ────────────────────────────────────────────────────────────
_initialized     = False
_ticket_counter  = 1000
_open_positions  = []

_BASE_PRICES = {
    "EURUSD": 1.08500, "GBPUSD": 1.27300, "USDJPY": 149.500,
    "USDCHF": 0.90200, "AUDUSD": 0.65100, "NZDUSD": 0.59800,
    "USDCAD": 1.36500, "EURJPY": 162.100, "GBPJPY": 190.800,
    "EURGBP": 0.85200,
}
_price_state = {}   # symbol → current mid


def _mid(symbol: str) -> float:
    """Random-walk price around a realistic base."""
    if symbol not in _price_state:
        _price_state[symbol] = _BASE_PRICES.get(symbol, 1.0)
    _price_state[symbol] *= (1 + random.gauss(0, 0.00015))
    return _price_state[symbol]


def _spread(symbol: str) -> float:
    return 0.020 if "JPY" in symbol else 0.00015


# ── Connection ────────────────────────────────────────────────────────────────
def initialize(**kwargs) -> bool:
    global _initialized
    _initialized = True
    return True


def shutdown():
    global _initialized
    _initialized = False


def last_error():
    return (0, "No error (mock)")


# ── Symbol info ───────────────────────────────────────────────────────────────
def symbol_info(symbol: str):
    is_jpy = "JPY" in symbol
    return SimpleNamespace(
        name=symbol,
        digits=3 if is_jpy else 5,
        trade_contract_size=100_000,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        point=0.001 if is_jpy else 0.00001,
    )


def symbol_info_tick(symbol: str):
    mid = _mid(symbol)
    sp  = _spread(symbol)
    return SimpleNamespace(
        ask=round(mid + sp / 2, 5),
        bid=round(mid - sp / 2, 5),
        last=round(mid, 5),
        time=int(time.time()),
        volume=random.randint(100, 5000),
    )


# ── Historical candles ────────────────────────────────────────────────────────
def copy_rates_from_pos(symbol: str, timeframe: int, pos: int, count: int):
    """Return synthetic OHLCV numpy array matching real MT5 dtype."""
    base   = _BASE_PRICES.get(symbol, 1.0)
    is_jpy = "JPY" in symbol
    tf_vol = {1: 0.0003, 5: 0.0006, 15: 0.001, 30: 0.0015,
              60: 0.002, 240: 0.004, 1440: 0.008}
    bar_vol = tf_vol.get(timeframe, 0.001) * (100 if is_jpy else 1)

    # Brownian-motion price series with a gentle trend component
    closes = [base]
    trend  = random.gauss(0, bar_vol * 0.05)
    for _ in range(count):
        closes.append(max(closes[-1] + random.gauss(trend, bar_vol), base * 0.3))

    now        = int(time.time())
    tf_seconds = timeframe * 60
    records    = []
    for i in range(count):
        o  = closes[i]
        c  = closes[i + 1]
        h  = max(o, c) + abs(random.gauss(0, bar_vol * 0.4))
        l  = min(o, c) - abs(random.gauss(0, bar_vol * 0.4))
        t  = now - (count - i) * tf_seconds
        records.append((t, o, h, l, c, random.randint(50, 2000), 1, 0))

    dtype = [("time","i8"),("open","f8"),("high","f8"),("low","f8"),
             ("close","f8"),("tick_volume","i8"),("spread","i4"),("real_volume","i8")]
    return np.array(records, dtype=dtype)


# ── Account ───────────────────────────────────────────────────────────────────
def account_info():
    open_profit = sum(getattr(p, "profit", 0) for p in _open_positions)
    return SimpleNamespace(
        login=88888888,
        server="MockBroker-Demo",
        balance=10_000.00,
        equity=round(10_000.00 + open_profit, 2),
        margin=round(sum(p.volume * 100 for p in _open_positions), 2),
        margin_free=round(10_000.00 - sum(p.volume * 100 for p in _open_positions), 2),
        leverage=100,
        currency="USD",
    )


# ── Positions ─────────────────────────────────────────────────────────────────
def positions_get(symbol: str = None, ticket: int = None):
    if ticket is not None:
        return [p for p in _open_positions if p.ticket == ticket] or None
    if symbol:
        return [p for p in _open_positions if p.symbol == symbol] or None
    return list(_open_positions) or None


TRADE_ACTION_SLTP = 6  # modify SL/TP (matches real MT5 constant)


def modify_position_sltp(ticket: int, sl: float, tp: float):
    """Modify SL/TP of a mock position."""
    for p in _open_positions:
        if p.ticket == ticket:
            if sl is not None:
                p.sl = sl
            if tp is not None:
                p.tp = tp
            return SimpleNamespace(retcode=TRADE_RETCODE_DONE, comment="Mock SL/TP updated")
    return SimpleNamespace(retcode=10004, comment="Position not found")


# ── Orders ────────────────────────────────────────────────────────────────────
def order_send(request: dict):
    global _ticket_counter, _open_positions

    if "position" in request:
        # Close position
        _open_positions = [p for p in _open_positions if p.ticket != request["position"]]
    else:
        # Open position
        _ticket_counter += 1
        pos = SimpleNamespace(
            ticket   = _ticket_counter,
            symbol   = request["symbol"],
            type     = request["type"],
            volume   = request["volume"],
            price_open = request["price"],
            sl       = request.get("sl", 0),
            tp       = request.get("tp", 0),
            profit   = round(random.gauss(0, 8), 2),
            comment  = request.get("comment", ""),
            time     = int(time.time()),
        )
        _open_positions.append(pos)

    return SimpleNamespace(
        retcode = TRADE_RETCODE_DONE,
        order   = _ticket_counter,
        comment = "Request completed (mock)",
    )
