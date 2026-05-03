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
    2026-05-03 FIX: BLOCKING during proven-losing sessions.
    30-day live data:
      - 17:00-21:59 UTC (NY Late): 35% WR, -$827/month
      - 22:00-00:59 UTC (Asian Late): 35% WR, -$707/month
    These windows LOSE money at any R:R. Hard block.
    Premium hours (06-16 UTC) get full lot size.
    """
    name = "time_of_day"
    # Best hours from 30-day P&L data (less-negative to neutral)
    PREMIUM_HOURS = {6, 7, 14}           # London open + NY afternoon
    # HARD BLOCK: 17:00-00:59 UTC — 35% WR, burns $1,534/month
    BLOCKED_HOURS = {17, 18, 19, 20, 21, 22, 23, 0}

    def should_trade(self, symbol, direction, context):
        h = datetime.now(timezone.utc).hour

        # HARD BLOCK: NY Late + Asian Late (proven 35% WR)
        if h in self.BLOCKED_HOURS:
            return False, (
                f"Session block: {h:02d}:00 UTC is in blocked window (17-01 UTC). "
                f"30-day data: 35% WR, -$1,534/month. No trades allowed."
            )

        # Premium window — full lot, no reduction
        if h in self.PREMIUM_HOURS:
            return True, ""

        # Standard hours (01-16 UTC excl premium) — slight lot reduction
        context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.80)
        return True, f"Standard hour {h:02d} — lot reduced 20%"


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
    2026-05-03: REDUNDANT — TimeOfDayFilter now hard-blocks 17-01 UTC.
    Kept as defence-in-depth in case TimeOfDayFilter is bypassed.

    30-day LIVE data (not backtest):
      17:00-21:59 UTC: 361 trades, 35% WR, -$827
      This is a HARD BLOCK — no amount of ADX/confidence makes this profitable.
    """
    name = "ny_late_session"

    NY_LATE_START = 17   # UTC
    NY_LATE_END   = 22   # UTC (exclusive)

    def should_trade(self, symbol, direction, context):
        h = datetime.now(timezone.utc).hour
        if not (self.NY_LATE_START <= h < self.NY_LATE_END):
            return True, ""

        # HARD BLOCK — no exceptions
        return False, (
            f"NY-late HARD BLOCK: {h:02d}:00 UTC — 30-day live WR=35%, "
            f"-$827/month. No trading 17-22 UTC."
        )


class SilverADXFilter(StrategyFilter):
    """
    2026-05-03: Silver EFFECTIVELY DISABLED.
    30-day live data: 33% WR, -$2,698 P&L on 425 trades.
    Below random — taking the OPPOSITE of every signal would have profited.

    ADX min raised to 30 (was 23). At ADX 23, WR was 33% — catastrophic.
    Combined with score_threshold=30 in continuous_trader.py, silver
    will only trade in the strongest possible trends.
    """
    name = "silver_adx"

    SILVER_ADX_MIN  = 30   # 23→30: only strongest trends (33% WR at 23)
    SILVER_CONF_MIN = 0.80 # 0.72→0.80: very high bar

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
                f"(silver 30-day WR: 33% at ADX 23 — only trade ADX 30+)"
            )

        if conf < self.SILVER_CONF_MIN:
            return False, (
                f"Silver confidence filter: {conf:.0%} < {self.SILVER_CONF_MIN:.0%} "
                f"(silver 30-day P&L: -$2,698 — very high bar required)"
            )

        # Quarter silver lot size until WR improves
        context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.25)
        return True, "Silver — lot quartered until WR improves"


class SpreadFilter(StrategyFilter):
    """
    2026-04-30: Spread-aware filter (replaces hour-based blocking).

    Real cost of trading 18-04 UTC isn't the time — it's the spread.
    Gold spread normally ~20¢ (2 pips); during off-hours it can be $1.00 (10 pips).
    A 35 pip TP with 10 pip spread = your effective TP is only 25 pips
    while SL still costs full 35 = R:R compresses to 0.71 (loses money).

    Rule: if current spread > 30% of M15 ATR, require very high conviction.
    """
    name = "spread_aware"

    def should_trade(self, symbol, direction, context):
        spread = context.get("spread_pips", 0)
        atr_pips = context.get("atr_pips", 0)
        if atr_pips <= 0 or spread <= 0:
            return True, ""  # no data = pass

        spread_pct = spread / atr_pips
        if spread_pct > 0.30:
            conf = context.get("confidence", 0)
            if conf < 0.78:
                return False, (
                    f"Spread filter: {spread:.1f}p ({spread_pct:.0%} of ATR) — "
                    f"need 78%+ conf to overcome spread cost (got {conf:.0%})"
                )
            # High-conviction trades still go through but with lot reduction
            context["_lot_reduction"] = min(context.get("_lot_reduction", 1.0), 0.7)
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
        # 2026-04-30: SpreadFilter replaces hour-based blocking
        # Order: cheapest checks first, expensive last
        self.filters = filters or [
            DayOfWeekFilter(),       # only blocks 30min Mon open / Fri close
            VolatilityFilter(),      # ATR percentile (size adjustment)
            TimeOfDayFilter(),       # NON-BLOCKING — premium hours boost
            SpreadFilter(),          # NEW: real cost of off-hours
            NYLateSessionFilter(),   # ADX-based check during NY late
            SilverADXFilter(),       # silver-specific (ADX 28+)
            CorrelationFilter(),     # XAU/XAG divergence
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
