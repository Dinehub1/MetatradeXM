"""
db_adapter.py — Abstract database adapter for SQLite and Supabase

Provides a unified interface for all database operations, allowing seamless
switching between local SQLite (development) and Supabase PostgreSQL (production).
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone


class DatabaseAdapter(ABC):
    """Abstract base class for database implementations."""

    @abstractmethod
    def record_entry(self, ticket: str, symbol: str, direction: str,
                     entry_price: float, confidence: float,
                     factors: dict = None, conditions: dict = None,
                     skills_used: list = None):
        """Record when a new trade is opened."""
        pass

    @abstractmethod
    def record_outcome(self, ticket: str, exit_price: float,
                       pips_result: float, outcome: str,
                       symbol: str = "UNKNOWN", direction: str = "UNKNOWN"):
        """Record when a trade closes."""
        pass

    @abstractmethod
    def record_filtered(self, symbol: str, direction: str, confidence: float,
                        reasons: list, factors: dict = None):
        """Record when a trade was filtered out."""
        pass

    @abstractmethod
    def prefetch_context(self, symbol: str, current_conditions: dict = None,
                         direction: str = None, adx: float = 0.0,
                         rsi: float = 50.0) -> str:
        """Returns memory context block for AI reasoning."""
        pass

    @abstractmethod
    def get_similar_setups(self, symbol: str, direction: str,
                           adx: float, rsi: float, session: str,
                           limit: int = 10) -> list:
        """Find past trades with similar market conditions."""
        pass

    @abstractmethod
    def get_session_win_rate(self, symbol: str, session: str,
                             min_trades: int = 5) -> Optional[dict]:
        """Return win-rate stats for this symbol in the current session."""
        pass

    @abstractmethod
    def get_recent_outcomes(self, hours: int = 24) -> list:
        """Get all trade outcomes from the last N hours."""
        pass

    @abstractmethod
    def get_all_outcomes(self, symbol: str = None, limit: int = 100) -> list:
        """Get trade outcomes, optionally filtered by symbol."""
        pass

    @abstractmethod
    def get_factor_stats(self) -> dict:
        """Compute win rate per factor value range."""
        pass

    @abstractmethod
    def log_learning(self, insight_type: str, insight_text: str,
                     data: dict = None, applied: bool = False):
        """Record a learning insight from the self-improvement engine."""
        pass

    @abstractmethod
    def get_pattern_summary(self, symbol: str) -> dict:
        """Get hourly and daily pattern summary for a symbol."""
        pass

    @abstractmethod
    def close(self):
        """Close database connection if needed."""
        pass
