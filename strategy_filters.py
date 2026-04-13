"""
strategy_filters.py — Pluggable Trade Filters

Each filter can veto a trade or adjust confidence.
Filters are backed by skills and track their own performance.
"""

import logging
import numpy as np
from datetime import datetime, timezone

log = logging.getLogger("filters")


class StrategyFilter:
    """Base class for trade filters."""
    name = "base"

    def should_trade(self, symbol: str, direction: str, context: dict) -> tuple:
        """Returns (allowed: bool, reason: str)"""
        return True, ""


class TimeOfDayFilter(StrategyFilter):
    """Block/boost trades based on time-of-day patterns."""
    name = "time_of_day"

    def should_trade(self, symbol, direction, context):
        hour = datetime.now(timezone.utc).hour
        session = context.get("session", "")

        # Gold/Silver: avoid BUY during Asian session weakness
        if symbol in ("XAUUSD", "XAGUSD"):
            if direction == "BUY" and session == "ASIAN":
                return False, f"Time bias: avoid BUY during ASIAN session (hour {hour})"

        return True, ""


class DayOfWeekFilter(StrategyFilter):
    """Skip trading on historically weak days."""
    name = "day_of_week"

    def should_trade(self, symbol, direction, context):
        now = datetime.now(timezone.utc)
        weekday = now.weekday()

        # Monday: whipsaw from weekend gaps
        if weekday == 0 and now.hour < 10:
            return False, "Day filter: skip Monday before 10:00 UTC (gap risk)"

        # Friday: reduced liquidity after 18:00 UTC
        if weekday == 4 and now.hour >= 18:
            return False, "Day filter: skip Friday after 18:00 UTC (low liquidity)"

        return True, ""


class VolatilityFilter(StrategyFilter):
    """Filter based on ATR percentile."""
    name = "volatility"

    def should_trade(self, symbol, direction, context):
        indicators = context.get("indicators", {})
        atr = indicators.get("atr", 0)

        if atr == 0:
            return True, ""

        # Get ATR history from candles if available
        atr_history = context.get("atr_history", [])
        if len(atr_history) < 20:
            return True, ""  # not enough data

        percentile = np.searchsorted(sorted(atr_history), atr) / len(atr_history) * 100

        if percentile < 10:
            return False, f"Volatility filter: ATR at {percentile:.0f}th percentile (dead market)"

        if percentile > 95:
            # Don't block, but flag for lot reduction
            context["_lot_reduction"] = 0.5
            return True, f"Volatility warning: ATR at {percentile:.0f}th percentile (chaos)"

        return True, ""


class CorrelationFilter(StrategyFilter):
    """Block trades when XAU/XAG signals diverge in high correlation."""
    name = "correlation"

    def should_trade(self, symbol, direction, context):
        # This filter needs both symbols' signals
        other_signal = context.get("other_symbol_signal", {})
        if not other_signal:
            return True, ""  # can't check correlation without both signals

        other_dir = other_signal.get("direction", "HOLD")
        correlation = context.get("xau_xag_correlation", 0)

        # Only filter when correlation is high
        if correlation < 0.85:
            return True, ""

        # Check for divergence
        if direction != "HOLD" and other_dir != "HOLD" and direction != other_dir:
            return False, (f"Correlation filter: {symbol} {direction} vs "
                          f"other metal {other_dir} (corr={correlation:.2f})")

        return True, ""


class FilterChain:
    """Runs all filters. Trade is blocked if ANY filter vetoes."""

    def __init__(self, filters=None):
        self.filters = filters or [
            TimeOfDayFilter(),
            DayOfWeekFilter(),
            VolatilityFilter(),
            CorrelationFilter(),
        ]

    def evaluate(self, symbol: str, direction: str, context: dict) -> tuple:
        """
        Returns (allowed: bool, veto_reasons: list[str])
        """
        reasons = []
        for f in self.filters:
            try:
                allowed, reason = f.should_trade(symbol, direction, context)
                if not allowed:
                    reasons.append(f"{f.name}: {reason}")
            except Exception as e:
                log.warning(f"Filter {f.name} error: {e}")
                continue  # filter errors should not block trades

        return len(reasons) == 0, reasons
