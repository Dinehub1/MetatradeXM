"""
Core Utilities — Shared formatting, validation, and sanitization functions.
"""
from datetime import datetime, timezone
import math

__all__ = [
    "now_utc",
    "utcnow",
    "sanitize",
    "fmt_profit",
    "compact_text",
    "adx_regime",
    "format_factor_summary",
]

def now_utc() -> datetime:
    """Return current UTC datetime object (timezone-aware)."""
    return datetime.now(timezone.utc)

def utcnow() -> str:
    """Return current UTC time as formatted string."""
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

def sanitize(obj):
    """Make data JSON-safe (numpy types, NaN, Inf → Python builtins / None)."""
    try:
        import numpy as np
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(obj, np.ndarray):
            return [sanitize(x) for x in obj.tolist()]
    except ImportError:
        pass
    
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj

def fmt_profit(p: float) -> str:
    """Format profit/loss with arrows and terminal colors."""
    arrow = "▲" if p >= 0 else "▼"
    color = "\033[92m" if p >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}{arrow} ${p:+.2f}{reset}"

def compact_text(text: str, limit: int = 88) -> str:
    """Normalize whitespace and trim long text for fast scanning."""
    text = " ".join(str(text or "").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

def adx_regime(adx: float) -> str:
    """Determine market regime based on ADX value."""
    if adx > 25:
        return "TRENDING"
    if adx < 18:
        return "RANGING"
    return "DEVELOPING"

def format_factor_summary(factor_scores: dict) -> str:
    """Format AI factor scores into a compact string."""
    parts = []
    for key, label in [
        ("f1_h4_trend", "H4"),
        ("f2_h1_trend", "H1"),
        ("f3_rsi_zone", "RSI"),
        ("f4_macd_momentum", "MACD"),
        ("f5_adx_strength", "ADX"),
        ("f6_stoch_confirm", "Stoch"),
        ("f7_bb_action", "BB"),
        ("f10_d1_trend", "D1"),
    ]:
        value = factor_scores.get(key, 0)
        if value != 0:
            parts.append(f"{label}={value:+.1f}")
    return " ".join(parts) if parts else "all neutral"
