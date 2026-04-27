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

        # Gold trades 24/5 — Asian session (Shanghai Gold Exchange) is legitimate.
        # Only block truly dead hours: 23:30-00:30 UTC (venue crossover, widest spreads).
        if h == 23 and now.minute >= 30:
            conf = context.get("confidence", 1.0)
            if conf < 0.65:
                return False, f"Session filter: venue crossover ({h:02d}:{now.minute:02d} UTC) — conf {conf:.0%} < 65%"
        if h == 0 and now.minute < 30:
            conf = context.get("confidence", 1.0)
            if conf < 0.65:
                return False, f"Session filter: venue crossover ({h:02d}:{now.minute:02d} UTC) — conf {conf:.0%} < 65%"

        return True, ""


class DayOfWeekFilter(StrategyFilter):
    """Skip trading on historically weak days."""
    name = "day_of_week"

    def should_trade(self, symbol, direction, context):
        now     = datetime.now(timezone.utc)
        weekday = now.weekday()

        # Monday: allow after 30 min (reduced from 2 hours — gap fills fast for Gold)
        if weekday == 0 and now.hour == 0 and now.minute < 30:
            return False, "Day filter: skip Monday first 30 min UTC (gap risk)"

        # Friday after 21:00 UTC: market fully winds down, spreads too wide
        if weekday == 4 and now.hour >= 21:
            return False, "Day filter: skip Friday after 21:00 UTC (market close)"

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


class NYLateSessionFilter(StrategyFilter):
    """
    New York late session (17:00–21:59 UTC) is the weakest trading window.
    Backtest over 2.4 years shows this period has only 47% win rate vs
    56% overall — well below break-even at current R:R.

    Rules:
      - In NY-late, require confidence ≥ 0.75 (vs normal 0.70 gate)
      - Allow trades only when ADX ≥ 22 (trending market confirmed)
      - Reduce position size 30% via lot_reduction if signal is marginal
    """
    name = "ny_late_session"

    NY_LATE_START = 17   # UTC
    NY_LATE_END   = 22   # UTC (exclusive)

    def should_trade(self, symbol, direction, context):
        h = datetime.now(timezone.utc).hour
        if not (self.NY_LATE_START <= h < self.NY_LATE_END):
            return True, ""

        indicators = context.get("indicators", {})
        adx        = indicators.get("adx", 0)
        confidence = context.get("confidence", 1.0)

        # Require trend confirmation during this weaker session
        if adx < 22:
            return False, (
                f"NY-late filter: ADX={adx:.1f} < 22 (ranging market in weak session "
                f"{h:02d}:00 UTC — wait for London/NY_Overlap)"
            )

        # Allow but reduce size if confidence is marginal for this session
        if confidence < 0.75:
            context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.7)
            return True, f"NY-late: lot reduced 30% (conf={confidence:.0%} in off-peak session)"

        return True, ""


class SilverADXFilter(StrategyFilter):
    """
    Silver (XAGUSD) is significantly choppier than Gold.
    Backtest showed 33% win rate for silver vs 56% for gold in the same period.
    Silver needs a stronger trend signal (ADX ≥ 22 vs gold's 18) to trade.
    Only applies to XAGUSD / SILVER symbols.
    """
    name = "silver_adx"

    SILVER_ADX_MIN = 22   # gold threshold is 18 (in ADX_regime in analyzer)

    def should_trade(self, symbol, direction, context):
        sym_upper = symbol.upper()
        if "SILVER" not in sym_upper and "XAG" not in sym_upper:
            return True, ""

        indicators = context.get("indicators", {})
        adx        = indicators.get("adx", 0)

        if adx < self.SILVER_ADX_MIN:
            return False, (
                f"Silver ADX filter: ADX={adx:.1f} < {self.SILVER_ADX_MIN} "
                "(Silver requires stronger trend than Gold — too choppy to trade)"
            )
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
            NYLateSessionFilter(),
            SilverADXFilter(),
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
