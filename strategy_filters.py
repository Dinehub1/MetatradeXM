"""
strategy_filters.py — Pluggable Trade Filters

Each filter can veto a trade or adjust confidence.
Filters are backed by skills and track their own performance.
"""

import logging
import numpy as np
from datetime import datetime, timezone

log = logging.getLogger("filters")

# ── Session boundaries (UTC) — must match is_forex_market_open() ─────────────
# ASIAN:              22:00–07:59
# LONDON:             08:00–12:59
# LONDON_NY_OVERLAP:  13:00–16:59  ← peak
# NEW_YORK:           17:00–21:59
def _current_session() -> str:
    h = datetime.now(timezone.utc).hour
    if  8 <= h < 13: return "LONDON"
    if 13 <= h < 17: return "LONDON_NY_OVERLAP"
    if 17 <= h < 22: return "NEW_YORK"
    return "ASIAN"


class StrategyFilter:
    """Base class for trade filters."""
    name = "base"

    def should_trade(self, symbol: str, direction: str, context: dict) -> tuple:
        """Returns (allowed: bool, reason: str)"""
        return True, ""


class TimeOfDayFilter(StrategyFilter):
    """
    Session-aware activity filter for Gold/Silver.

    Gold is active in all three major sessions — but the pre-London dead zone
    (01:00–07:59 UTC) has the lowest volatility and widest spreads.
    We don't block Asian entirely (Shanghai Gold Exchange is active), but we
    require stronger signals during the quietest window.

    Session confidence adjustments are handled here; lot-size multipliers
    are in capital_manager.py (ASIAN=0.3×, LONDON=1.0×, etc.).
    """
    name = "time_of_day"

    # UTC hours where Gold spreads are widest / moves smallest
    # Pre-London dead zone: 01:00–07:59 UTC
    DEAD_ZONE_START = 1
    DEAD_ZONE_END   = 8   # exclusive

    def should_trade(self, symbol, direction, context):
        now     = datetime.now(timezone.utc)
        h       = now.hour
        session = _current_session()

        # ── Pre-London dead zone: require very high confidence ────────────────
        # Between 01:00-07:59 UTC only Sydney/Tokyo active; spreads are wide
        # and fake-outs are common. We don't block entirely (Asian trend moves
        # are real) but raise the bar.
        if self.DEAD_ZONE_START <= h < self.DEAD_ZONE_END:
            conf = context.get("confidence", 1.0)
            if conf < 0.75:
                return False, (
                    f"Session filter: pre-London dead zone ({h:02d}:00 UTC) — "
                    f"confidence {conf:.0%} below 75% threshold"
                )

        # ── First 30 min of London open: gap-fill whipsaw risk ────────────────
        # 08:00-08:29 UTC: initial price discovery, stop hunts common
        if h == 8 and now.minute < 30:
            conf = context.get("confidence", 1.0)
            if conf < 0.70:
                return False, (
                    "Session filter: London open first 30 min (gap/stop-hunt risk) — "
                    f"confidence {conf:.0%} below 70%"
                )

        # ── First 30 min of NY open: volatile price discovery ────────────────
        # 13:00-13:29 UTC: economic data releases, spreads spike
        if h == 13 and now.minute < 30:
            conf = context.get("confidence", 1.0)
            if conf < 0.70:
                return False, (
                    "Session filter: NY open first 30 min (news/data risk) — "
                    f"confidence {conf:.0%} below 70%"
                )

        return True, ""


class DayOfWeekFilter(StrategyFilter):
    """Skip trading on historically weak days."""
    name = "day_of_week"

    def should_trade(self, symbol, direction, context):
        now     = datetime.now(timezone.utc)
        weekday = now.weekday()

        # Monday: whipsaw from weekend gaps — skip first 2 hours
        if weekday == 0 and now.hour < 10:
            return False, "Day filter: skip Monday before 10:00 UTC (gap risk)"

        # Friday after 20:00 UTC: market winds down, wide spreads, thin book
        # (was 18:00 — extended to 20:00 to allow NY afternoon session trades)
        if weekday == 4 and now.hour >= 20:
            return False, "Day filter: skip Friday after 20:00 UTC (market wind-down)"

        return True, ""


class VolatilityFilter(StrategyFilter):
    """Filter based on ATR percentile — uses ATR as liquidity/activity proxy
    since Forex has no centralised volume.  ATR IS the best available proxy:
    active markets produce larger candle ranges, dead markets produce tiny ones."""
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
            return False, f"Volatility filter: ATR at {percentile:.0f}th percentile (dead market — no liquidity)"

        if percentile > 95:
            # Don't block, but flag for lot reduction
            context["_lot_reduction"] = 0.5
            return True, f"Volatility warning: ATR at {percentile:.0f}th percentile (extreme volatility)"

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
