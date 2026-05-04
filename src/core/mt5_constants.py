"""
mt5_constants.py — Centralized MT5 constant definitions.
Single source of truth for MetaTrader5 constants used across bridges and mock implementations.
"""

# TimeFrame constants (match real MetaTrader5 package values)
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 60
TIMEFRAME_H4 = 240
TIMEFRAME_D1 = 1440
TIMEFRAME_W1 = 10080

# Order type constants
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

# Trade action constants
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6

# Order/Trade constants
ORDER_TIME_GTC = 0
ORDER_FILLING_IOC = 1
TRADE_RETCODE_DONE = 10009

# Internal mapping: timeframe integer → string representation
_TF_INT_TO_STR = {
    1: "M1",
    5: "M5",
    15: "M15",
    30: "M30",
    60: "H1",
    240: "H4",
    1440: "D1",
    10080: "W1",
}


def get_timeframe_str(tf_int: int) -> str:
    """Convert timeframe integer to string representation.

    Args:
        tf_int: Timeframe integer (e.g., 1, 5, 15, 60, 240, 1440)

    Returns:
        String representation (e.g., "M1", "H1", "D1") or "UNKNOWN"
    """
    return _TF_INT_TO_STR.get(tf_int, "UNKNOWN")
