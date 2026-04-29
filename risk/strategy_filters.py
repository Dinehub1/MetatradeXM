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
    Session-aware activity filter — DATA-DRIVEN from 30-day live trade analysis.

    Hourly WR/P&L breakdown (UTC) showed:
      05–07: 71% WR  +$48    ✅ London open — best window
      14:    62% WR  +$102   ✅ NY morning
      08–13: 47–64% WR -$1,206 ⚠️  Mixed (allow with conf gate)
      15–17: 36–46% WR -$835  ❌ NY mid — block
      18–04: 23–32% WR -$2,007 ❌ NY late + Asian — block (worst)

    Total: 18:00–04:59 UTC accounts for $1,963 of the $3,707 30-day loss.
    """
    name = "time_of_day"

    # Hours where the bot historically loses money (UTC)
    # 2026-04-30: Relaxed to require high confidence instead of hard block
    # 18-04 UTC had 23-32% WR but new 0.65 conf gate + smart exit may help
    # premium window:  5–7, 14 (trade freely)
    # caution window:  8–13, 15–17, 18–04 (need 72%+ conf)
    PREMIUM_HOURS = {5, 6, 7, 14}
    CAUTION_HOURS = set(range(8, 18)) | {18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4}

    def should_trade(self, symbol, direction, context):
        now = datetime.now(timezone.utc)
        h   = now.hour

        # Premium hours: trade freely
        if h in self.PREMIUM_HOURS:
            return True, ""

        # Caution hours (8-13, 15-17, 18-04): require higher confidence
        # Previous hard block at 18-04 removed. Rely on confidence gate instead.
        if h in self.CAUTION_HOURS:
            conf = context.get("confidence", 1.0)
            if conf < 0.72:
                return False, (
                    f"Session filter: hour {h:02d} UTC needs 72%+ confidence "
                    f"(got {conf:.0%}) — caution window (hist 23-64% WR)"
                )

        # Reduce lot 30% during mixed hours
        context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.7)
        return True, f"Mixed hour {h:02d} — lot reduced 30%"


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
            context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.5)
            return True, f"Volatility warning: ATR at {percentile:.0f}th percentile (thin market)"

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
    Silver (XAGUSD) is a graveyard.
    30-day live data: 27% WR, -$2,262 P&L on 284 trades.
    Below random — taking the OPPOSITE of every signal would have profited.

    Until the silver signal generation is rebuilt, we require:
      - ADX ≥ 30 (only trade strongest silver trends)
      - Confidence ≥ 80% (vs 75% for gold)
      - Half lot size
    """
    name = "silver_adx"

    SILVER_ADX_MIN  = 30   # was 22 — raised after 27% WR
    SILVER_CONF_MIN = 0.80 # raise from 0.75

    def should_trade(self, symbol, direction, context):
        sym_upper = symbol.upper()
        if "SILVER" not in sym_upper and "XAG" not in sym_upper:
            return True, ""

        indicators = context.get("indicators", {})
        adx        = indicators.get("adx", 0)
        conf       = context.get("confidence", 1.0)

        if adx < self.SILVER_ADX_MIN:
            return False, (
                f"Silver ADX filter: ADX={adx:.1f} < {self.SILVER_ADX_MIN} "
                "(silver 30-day WR: 27% — only trade strongest trends)"
            )

        if conf < self.SILVER_CONF_MIN:
            return False, (
                f"Silver confidence filter: {conf:.0%} < {self.SILVER_CONF_MIN:.0%} "
                "(silver 30-day P&L: -$2,262 — very high bar required)"
            )

        # Always halve silver lot size until WR improves
        context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.5)
        return True, "Silver — lot halved until WR improves"


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
