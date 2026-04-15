"""
memory.py — Trade Memory System (Hermes-inspired)

Persistent memory of all trading decisions and outcomes.
Prefetches context before decisions, syncs outcomes after trades close.
Feeds the self-improvement engine with performance data.
"""

import json
import sqlite3
import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("memory")

DB_PATH = Path(__file__).parent / "trade_memory.db"


class TradeMemory:
    """SQLite-backed trade memory with prefetch/sync pattern."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trade_outcomes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts              TEXT NOT NULL,
                    ticket          TEXT,
                    symbol          TEXT NOT NULL,
                    direction       TEXT NOT NULL,
                    entry_price     REAL,
                    exit_price      REAL,
                    pips_result     REAL,
                    confidence      REAL,
                    factors_json    TEXT,
                    conditions_json TEXT,
                    duration_min    REAL,
                    outcome         TEXT,
                    skills_used     TEXT
                );

                CREATE TABLE IF NOT EXISTS trade_entries (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts              TEXT NOT NULL,
                    ticket          TEXT,
                    symbol          TEXT NOT NULL,
                    direction       TEXT NOT NULL,
                    entry_price     REAL,
                    confidence      REAL,
                    factors_json    TEXT,
                    conditions_json TEXT,
                    skills_used     TEXT,
                    closed          INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS market_patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT NOT NULL,
                    hour_utc    INTEGER,
                    day_of_week INTEGER,
                    session     TEXT,
                    direction   TEXT,
                    outcome     TEXT,
                    pips        REAL,
                    ts          TEXT
                );

                CREATE TABLE IF NOT EXISTS learning_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    insight_type TEXT NOT NULL,
                    insight_text TEXT NOT NULL,
                    data_json   TEXT,
                    applied     INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS filtered_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    direction   TEXT NOT NULL,
                    confidence  REAL,
                    filter_reasons TEXT,
                    factors_json TEXT
                );
            """)

    # ── Record trade entry ───────────────────────────────────────────────────

    def record_entry(self, ticket: str, symbol: str, direction: str,
                     entry_price: float, confidence: float,
                     factors: dict = None, conditions: dict = None,
                     skills_used: list = None):
        """Record when a new trade is opened."""
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            def _safe_json(obj):
                """Convert numpy/bool types to JSON-serializable Python types."""
                import math
                if isinstance(obj, dict):
                    return {k: _safe_json(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)):
                    return [_safe_json(i) for i in obj]
                if isinstance(obj, bool):
                    return bool(obj)
                try:
                    import numpy as np
                    if isinstance(obj, (np.integer,)):
                        return int(obj)
                    if isinstance(obj, (np.floating,)):
                        return float(obj)
                    if isinstance(obj, (np.bool_,)):
                        return bool(obj)
                except ImportError:
                    pass
                if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                    return None
                return obj

            conn.execute("""
                INSERT INTO trade_entries
                (ts, ticket, symbol, direction, entry_price, confidence,
                 factors_json, conditions_json, skills_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, str(ticket), symbol, direction, entry_price, confidence,
                  json.dumps(_safe_json(factors or {})),
                  json.dumps(_safe_json(conditions or {})),
                  json.dumps(skills_used or [])))
        log.info(f"[MEMORY] Recorded entry: {symbol} {direction} #{ticket} conf={confidence:.0%}")

    # ── Record trade outcome ─────────────────────────────────────────────────

    def record_outcome(self, ticket: str, exit_price: float,
                       pips_result: float, outcome: str):
        """Record when a trade closes. Links back to entry data."""
        ts = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Find the entry record
            row = conn.execute(
                "SELECT * FROM trade_entries WHERE ticket=? AND closed=0 ORDER BY id DESC LIMIT 1",
                (str(ticket),)
            ).fetchone()

            if row:
                entry_ts = row[1]
                symbol = row[3]
                direction = row[4]
                entry_price = row[5]
                confidence = row[6]
                factors_json = row[7]
                conditions_json = row[8]
                skills_used = row[9]

                # Calculate duration
                try:
                    entry_dt = datetime.fromisoformat(entry_ts)
                    duration = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
                except:
                    duration = 0

                conn.execute("""
                    INSERT INTO trade_outcomes
                    (ts, ticket, symbol, direction, entry_price, exit_price,
                     pips_result, confidence, factors_json, conditions_json,
                     duration_min, outcome, skills_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ts, str(ticket), symbol, direction, entry_price, exit_price,
                      pips_result, confidence, factors_json, conditions_json,
                      duration, outcome, skills_used))

                # Mark entry as closed
                conn.execute("UPDATE trade_entries SET closed=1 WHERE id=?", (row[0],))

                # Record pattern
                now = datetime.now(timezone.utc)
                conn.execute("""
                    INSERT INTO market_patterns
                    (symbol, hour_utc, day_of_week, session, direction, outcome, pips, ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, now.hour, now.weekday(),
                      self._get_session_name(now.hour), direction, outcome,
                      pips_result, ts))

                log.info(f"[MEMORY] Recorded outcome: {symbol} {direction} #{ticket} "
                         f"{outcome} {pips_result:+.1f} pips (duration: {duration:.0f}min)")
            else:
                # No entry found — record outcome anyway
                conn.execute("""
                    INSERT INTO trade_outcomes
                    (ts, ticket, symbol, direction, entry_price, exit_price,
                     pips_result, confidence, factors_json, conditions_json,
                     duration_min, outcome, skills_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ts, str(ticket), "UNKNOWN", "UNKNOWN", 0, exit_price,
                      pips_result, 0, "{}", "{}", 0, outcome, "[]"))

    # ── Record filtered trade ────────────────────────────────────────────────

    def record_filtered(self, symbol: str, direction: str, confidence: float,
                        reasons: list, factors: dict = None):
        """Record when a trade was filtered out (for self-improvement tracking)."""
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO filtered_trades
                (ts, symbol, direction, confidence, filter_reasons, factors_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ts, symbol, direction, confidence,
                  json.dumps(reasons), json.dumps(factors or {})))

    # ── Prefetch context for AI reasoning ────────────────────────────────────

    def prefetch_context(self, symbol: str, current_conditions: dict = None) -> str:
        """
        Returns memory context block for AI reasoning.
        Inspired by Hermes prefetch_all() pattern.
        """
        parts = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Last 5 trade outcomes for this symbol
            recent = conn.execute("""
                SELECT direction, outcome, pips_result, confidence, duration_min
                FROM trade_outcomes
                WHERE symbol=?
                ORDER BY id DESC LIMIT 5
            """, (symbol,)).fetchall()

            if recent:
                parts.append("Recent trades for this symbol:")
                for r in recent:
                    parts.append(f"  {r['direction']} → {r['outcome']} "
                                 f"{r['pips_result']:+.1f}pips conf={r['confidence']:.0%} "
                                 f"({r['duration_min']:.0f}min)")

            # Win rate by current session + hour
            now = datetime.now(timezone.utc)
            session = self._get_session_name(now.hour)
            pattern_stats = conn.execute("""
                SELECT direction, outcome, COUNT(*) as cnt, AVG(pips) as avg_pips
                FROM market_patterns
                WHERE symbol=? AND session=?
                GROUP BY direction, outcome
            """, (symbol, session)).fetchall()

            if pattern_stats:
                parts.append(f"\nPerformance in {session} session:")
                for ps in pattern_stats:
                    parts.append(f"  {ps['direction']} {ps['outcome']}: "
                                 f"{ps['cnt']} trades, avg {ps['avg_pips']:+.1f}pips")

            # Best/worst factor combinations from wins vs losses
            wins = conn.execute("""
                SELECT factors_json FROM trade_outcomes
                WHERE symbol=? AND outcome='WIN'
                ORDER BY id DESC LIMIT 10
            """, (symbol,)).fetchall()

            losses = conn.execute("""
                SELECT factors_json FROM trade_outcomes
                WHERE symbol=? AND outcome='LOSS'
                ORDER BY id DESC LIMIT 10
            """, (symbol,)).fetchall()

            if wins or losses:
                parts.append(f"\nFactor analysis ({len(wins)} wins, {len(losses)} losses in memory)")

            # Recent learning insights
            insights = conn.execute("""
                SELECT insight_type, insight_text FROM learning_log
                WHERE applied=1
                ORDER BY id DESC LIMIT 3
            """).fetchall()

            if insights:
                parts.append("\nActive learning insights:")
                for ins in insights:
                    parts.append(f"  [{ins['insight_type']}] {ins['insight_text']}")

        if not parts:
            return ""  # No memory yet

        return "\n".join(parts)

    # ── Statistics for self-improvement ───────────────────────────────────────

    def get_recent_outcomes(self, hours: int = 24) -> list:
        """Get all trade outcomes from the last N hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM trade_outcomes WHERE ts >= ? ORDER BY id
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    def get_all_outcomes(self, symbol: str = None, limit: int = 100) -> list:
        """Get trade outcomes, optionally filtered by symbol."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if symbol:
                rows = conn.execute("""
                    SELECT * FROM trade_outcomes WHERE symbol=?
                    ORDER BY id DESC LIMIT ?
                """, (symbol, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM trade_outcomes ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_factor_stats(self) -> dict:
        """Compute win rate per factor value range."""
        stats = {}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT factors_json, outcome FROM trade_outcomes
                WHERE factors_json IS NOT NULL AND factors_json != '{}'
            """).fetchall()

            for row in rows:
                try:
                    factors = json.loads(row['factors_json'])
                    outcome = row['outcome']
                    for factor_name, value in factors.items():
                        if factor_name == 'adx_regime' or factor_name == 'bb_squeeze':
                            continue
                        if factor_name not in stats:
                            stats[factor_name] = {'win_vals': [], 'loss_vals': []}
                        if outcome == 'WIN':
                            stats[factor_name]['win_vals'].append(value)
                        elif outcome == 'LOSS':
                            stats[factor_name]['loss_vals'].append(value)
                except:
                    continue

        # Compute averages
        result = {}
        for name, data in stats.items():
            wins = data['win_vals']
            losses = data['loss_vals']
            total = len(wins) + len(losses)
            result[name] = {
                'win_rate': len(wins) / total if total > 0 else 0,
                'avg_when_win': sum(wins) / len(wins) if wins else 0,
                'avg_when_loss': sum(losses) / len(losses) if losses else 0,
                'sample_size': total,
            }
        return result

    def log_learning(self, insight_type: str, insight_text: str,
                     data: dict = None, applied: bool = False):
        """Record a learning insight from the self-improvement engine."""
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO learning_log (ts, insight_type, insight_text, data_json, applied)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, insight_type, insight_text, json.dumps(data or {}), int(applied)))
        log.info(f"[MEMORY] Learning: [{insight_type}] {insight_text}")

    def get_pattern_summary(self, symbol: str) -> dict:
        """Get hourly and daily pattern summary for a symbol."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            hourly = {}
            rows = conn.execute("""
                SELECT hour_utc, outcome, COUNT(*) as cnt, AVG(pips) as avg_pips
                FROM market_patterns WHERE symbol=?
                GROUP BY hour_utc, outcome
            """, (symbol,)).fetchall()
            for r in rows:
                h = r['hour_utc']
                if h not in hourly:
                    hourly[h] = {'wins': 0, 'losses': 0, 'total_pips': 0}
                if r['outcome'] == 'WIN':
                    hourly[h]['wins'] = r['cnt']
                else:
                    hourly[h]['losses'] = r['cnt']
                hourly[h]['total_pips'] += r['avg_pips'] * r['cnt']

            daily = {}
            rows = conn.execute("""
                SELECT day_of_week, outcome, COUNT(*) as cnt, AVG(pips) as avg_pips
                FROM market_patterns WHERE symbol=?
                GROUP BY day_of_week, outcome
            """, (symbol,)).fetchall()
            for r in rows:
                d = r['day_of_week']
                if d not in daily:
                    daily[d] = {'wins': 0, 'losses': 0, 'total_pips': 0}
                if r['outcome'] == 'WIN':
                    daily[d]['wins'] = r['cnt']
                else:
                    daily[d]['losses'] = r['cnt']
                daily[d]['total_pips'] += r['avg_pips'] * r['cnt']

        return {'hourly': hourly, 'daily': daily}

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_session_name(hour: int) -> str:
        if 7 <= hour < 13:  return "LONDON"
        if 13 <= hour < 16: return "LONDON_NY_OVERLAP"
        if 16 <= hour < 22: return "NEW_YORK"
        return "ASIAN"


if __name__ == "__main__":
    # Quick test
    mem = TradeMemory()
    print(f"Trade memory initialized at {DB_PATH}")
    print(f"Recent outcomes: {len(mem.get_all_outcomes())}")
    ctx = mem.prefetch_context("XAUUSD")
    print(f"Prefetch context: {ctx[:200] if ctx else '(empty - no trades yet)'}")
