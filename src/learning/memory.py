"""
memory.py — Supabase-only trade memory.

Persistent memory for entries, outcomes, filtered trades, learning logs, and
similar-setup recall. SQLite fallback was intentionally removed so live trading
and the dashboard share one database source of truth.
"""

from core.logger_factory import get_logger
from core.supabase_db import SupabaseDB


log = get_logger("memory")


class TradeMemory:
    """Trade memory system backed only by Supabase PostgreSQL."""

    def __init__(self, db_path: str = None):
        if db_path:
            log.warning("[MEMORY] Ignoring db_path=%s; Supabase is the only database backend", db_path)
        self.db_path = None
        self.adapter = SupabaseDB()
        log.info("[MEMORY] Using Supabase PostgreSQL as the only database backend")

    def record_entry(self, ticket: str, symbol: str, direction: str,
                     entry_price: float, confidence: float,
                     factors: dict = None, conditions: dict = None,
                     skills_used: list = None, volume: float = None):
        return self.adapter.record_entry(
            ticket, symbol, direction, entry_price, confidence,
            factors, conditions, skills_used, volume,
        )

    def record_outcome(self, ticket: str, exit_price: float,
                       pips_result: float, outcome: str,
                       symbol: str = "UNKNOWN", direction: str = "UNKNOWN",
                       profit_usd: float = None, volume: float = None):
        return self.adapter.record_outcome(
            ticket, exit_price, pips_result, outcome,
            symbol, direction, profit_usd, volume,
        )

    def record_filtered(self, symbol: str, direction: str, confidence: float,
                        reasons: list, factors: dict = None):
        return self.adapter.record_filtered(symbol, direction, confidence, reasons, factors)

    def prefetch_context(self, symbol: str, current_conditions: dict = None,
                         direction: str = None, adx: float = 0.0,
                         rsi: float = 50.0) -> str:
        return self.adapter.prefetch_context(symbol, current_conditions, direction, adx, rsi)

    def get_similar_setups(self, symbol: str, direction: str,
                           adx: float, rsi: float, session: str,
                           limit: int = 10) -> list:
        return self.adapter.get_similar_setups(symbol, direction, adx, rsi, session, limit)

    def get_session_win_rate(self, symbol: str, session: str,
                             min_trades: int = 5) -> dict | None:
        return self.adapter.get_session_win_rate(symbol, session, min_trades)

    def get_recent_outcomes(self, hours: int = 24) -> list:
        return self.adapter.get_recent_outcomes(hours)

    def get_all_outcomes(self, symbol: str = None, limit: int = 100) -> list:
        return self.adapter.get_all_outcomes(symbol, limit)

    def get_factor_stats(self) -> dict:
        return self.adapter.get_factor_stats()

    def log_learning(self, insight_type: str, insight_text: str,
                     data: dict = None, applied: bool = False):
        return self.adapter.log_learning(insight_type, insight_text, data, applied)

    def get_pattern_summary(self, symbol: str) -> dict:
        return self.adapter.get_pattern_summary(symbol)

    def get_recent_learning(self, limit: int = 5) -> list:
        return self.adapter.get_recent_learning(limit)

    def close(self):
        return self.adapter.close()
