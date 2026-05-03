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
from core.paths import DATA_DIR


log = logging.getLogger("memory")

DB_PATH = DATA_DIR / "trade_memory.db"


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
            conn.execute("""
                INSERT INTO trade_entries
                (ts, ticket, symbol, direction, entry_price, confidence,
                 factors_json, conditions_json, skills_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, str(ticket), symbol, direction, entry_price, confidence,
                  json.dumps(self._safe_json(factors or {})),
                  json.dumps(self._safe_json(conditions or {})),
                  json.dumps(skills_used or [])))
        log.info(f"[MEMORY] Recorded entry: {symbol} {direction} #{ticket} conf={confidence:.0%}")

    # ── Record trade outcome ─────────────────────────────────────────────────

    def record_outcome(self, ticket: str, exit_price: float,
                       pips_result: float, outcome: str,
                       symbol: str = "UNKNOWN", direction: str = "UNKNOWN"):
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
                """, (ts, str(ticket), symbol, direction, 0, exit_price,
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
                  json.dumps(self._safe_json(reasons)), json.dumps(self._safe_json(factors or {}))))

    # ── Prefetch context for AI reasoning ────────────────────────────────────

    def prefetch_context(self, symbol: str, current_conditions: dict = None,
                         direction: str = None, adx: float = 0.0,
                         rsi: float = 50.0) -> str:
        """
        Returns memory context block for AI reasoning.
        Inspired by Hermes prefetch_all() pattern.
        Includes: aggregate performance, per-direction WR, current streak,
        recent trades, session stats, and learning insights.
        """
        parts = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # ── AGGREGATE PERFORMANCE (NEW — AI sees its own track record) ──
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                    AVG(pips_result) as avg_pips
                FROM trade_outcomes WHERE symbol=?
            """, (symbol,)).fetchone()

            if stats and stats['total'] and stats['total'] > 0:
                total = stats['total']
                wins = stats['wins'] or 0
                losses = stats['losses'] or 0
                wr = wins / total * 100
                avg_pips = stats['avg_pips'] or 0
                parts.append(f"📊 PERFORMANCE ON {symbol} (informational — strategy was recalibrated 2026-05-01):")
                parts.append(f"  Total: {total} trades | Win Rate: {wr:.0f}% | Avg: {avg_pips:+.1f} pips")
                # Note: old losses are from prior strategy (broken Fibonacci factor).
                # New strategy is fundamentally different — do NOT let old losses stop trades
                # if technical setup is strong (ADX > 22, MACD aligned, score >= threshold).

            # ── WIN RATE BY DIRECTION (NEW — blocks weak directions) ──
            dir_stats = conn.execute("""
                SELECT direction,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins
                FROM trade_outcomes WHERE symbol=?
                GROUP BY direction
            """, (symbol,)).fetchall()

            for ds in dir_stats:
                ds_total = ds['total'] or 0
                ds_wins = ds['wins'] or 0
                if ds_total >= 5 and ds_total > 0:  # need >=5 trades to be statistically meaningful
                    wr = ds_wins / ds_total * 100
                    if wr < 30:
                        parts.append(f"  Note: {ds['direction']} historical WR {wr:.0f}% ({ds_wins}/{ds_total}) — proceed only with strong confluence")

            # ── CURRENT STREAK (NEW — AI knows if edge is broken) ──
            recent_outcomes = conn.execute("""
                SELECT outcome FROM trade_outcomes
                WHERE symbol=? ORDER BY id DESC LIMIT 10
            """, (symbol,)).fetchall()

            if recent_outcomes:
                streak = 0
                streak_type = recent_outcomes[0]['outcome']
                for r in recent_outcomes:
                    if r['outcome'] == streak_type:
                        streak += 1
                    else:
                        break
                if streak >= 5:  # only flag streaks of 5+, not 3
                    icon = '🔴' if streak_type == 'LOSS' else '🟢'
                    parts.append(f"  {icon} Recent streak: {streak} consecutive {streak_type}s (informational only)")

            # Last 5 trade outcomes for this symbol
            recent = conn.execute("""
                SELECT direction, outcome, pips_result, confidence, duration_min
                FROM trade_outcomes
                WHERE symbol=?
                ORDER BY id DESC LIMIT 5
            """, (symbol,)).fetchall()

            if recent:
                parts.append("\nRecent trades for this symbol:")
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

            # ── CONFIDENCE CALIBRATION (AI sees if its confidence predicts outcomes) ──
            conf_bands = conn.execute("""
                SELECT
                    CASE
                        WHEN confidence >= 0.80 THEN 'HIGH (80%+)'
                        WHEN confidence >= 0.65 THEN 'MEDIUM (65-80%)'
                        WHEN confidence >= 0.55 THEN 'LOW (55-65%)'
                        ELSE 'MARGINAL (<55%)'
                    END as band,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                    AVG(pips_result) as avg_pips
                FROM trade_outcomes WHERE symbol=?
                GROUP BY band
                ORDER BY confidence DESC
            """, (symbol,)).fetchall()

            if conf_bands and any(cb['total'] >= 3 for cb in conf_bands):
                parts.append("\n🎯 CONFIDENCE CALIBRATION (is your confidence accurate?):")
                for cb in conf_bands:
                    if cb['total'] >= 3:
                        wr = (cb['wins'] or 0) / cb['total'] * 100
                        icon = '✅' if wr >= 55 else '⚠️' if wr >= 40 else '❌'
                        parts.append(f"  {icon} {cb['band']}: {wr:.0f}% WR ({cb['wins']}/{cb['total']}) "
                                     f"avg {(cb['avg_pips'] or 0):+.1f}p")
                        if cb['band'] == 'HIGH (80%+)' and wr < 55:
                            parts.append(f"     ⛔ High confidence signals are NOT performing — reduce confidence or add more filters")
                        elif cb['band'] == 'LOW (55-65%)' and wr >= 55:
                            parts.append(f"     💡 Low confidence signals ARE winning — consider raising confidence for these setups")

            # ── FACTOR FINGERPRINT (which factors correlate with wins vs losses) ──
            wins = conn.execute("""
                SELECT factors_json FROM trade_outcomes
                WHERE symbol=? AND outcome='WIN' AND factors_json IS NOT NULL
                ORDER BY id DESC LIMIT 20
            """, (symbol,)).fetchall()

            losses = conn.execute("""
                SELECT factors_json FROM trade_outcomes
                WHERE symbol=? AND outcome='LOSS' AND factors_json IS NOT NULL
                ORDER BY id DESC LIMIT 20
            """, (symbol,)).fetchall()

            if len(wins) >= 5 and len(losses) >= 5:
                # Parse factor values and compute average for each factor
                win_factors = {}
                loss_factors = {}
                for w in wins:
                    try:
                        fj = json.loads(w['factors_json'] or '{}')
                        for k, v in fj.items():
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                win_factors.setdefault(k, []).append(v)
                    except: pass
                for l in losses:
                    try:
                        fj = json.loads(l['factors_json'] or '{}')
                        for k, v in fj.items():
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                loss_factors.setdefault(k, []).append(v)
                    except: pass

                parts.append(f"\n📊 FACTOR FINGERPRINT ({len(wins)} wins vs {len(losses)} losses):")
                for factor in sorted(set(win_factors.keys()) & set(loss_factors.keys())):
                    w_avg = sum(win_factors[factor]) / len(win_factors[factor])
                    l_avg = sum(loss_factors[factor]) / len(loss_factors[factor])
                    diff = abs(w_avg - l_avg)
                    if diff >= 2.0:  # only show factors with meaningful difference
                        better = "wins" if abs(w_avg) > abs(l_avg) else "losses"
                        parts.append(f"  {factor}: win_avg={w_avg:+.1f} vs loss_avg={l_avg:+.1f} "
                                     f"(stronger in {better})")
            elif wins or losses:
                parts.append(f"\nFactor analysis ({len(wins)} wins, {len(losses)} losses — need 5+ each for fingerprinting)")

            # ── HOURLY EDGE MAP (which hours make/lose money) ──
            hourly = conn.execute("""
                SELECT hour_utc,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                    SUM(pips) as total_pips
                FROM market_patterns WHERE symbol=?
                GROUP BY hour_utc
                HAVING total >= 3
                ORDER BY total_pips ASC
            """, (symbol,)).fetchall()

            if hourly:
                worst = [h for h in hourly if (h['total_pips'] or 0) < -5]
                best  = [h for h in hourly if (h['total_pips'] or 0) > 5]
                if worst:
                    parts.append("\n⏰ LOSING HOURS (avoid these):")
                    for h in worst[:3]:
                        wr = ((h['wins'] or 0) / h['total'] * 100) if h['total'] > 0 else 0
                        parts.append(f"  {h['hour_utc']:02d}:00 UTC: {wr:.0f}% WR, {(h['total_pips'] or 0):+.1f} total pips ({h['total']} trades)")
                if best:
                    parts.append("⏰ WINNING HOURS (prefer these):")
                    for h in sorted(best, key=lambda x: x['total_pips'] or 0, reverse=True)[:3]:
                        wr = ((h['wins'] or 0) / h['total'] * 100) if h['total'] > 0 else 0
                        parts.append(f"  {h['hour_utc']:02d}:00 UTC: {wr:.0f}% WR, {(h['total_pips'] or 0):+.1f} total pips ({h['total']} trades)")

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

        # ── SIMILAR SETUP RECALL (new — AI sees past outcomes for this exact setup) ──
        if direction and adx > 0:
            now_session = self._get_session_name(datetime.now(timezone.utc).hour)
            similar = self.get_similar_setups(symbol, direction, adx, rsi, now_session, limit=10)
            if similar:
                wins   = sum(1 for s in similar if s['outcome'] == 'WIN')
                losses = sum(1 for s in similar if s['outcome'] == 'LOSS')
                avg_p  = sum(s['pips'] or 0 for s in similar) / len(similar)
                wr = wins / len(similar) * 100
                parts.append(f"\n⚡ SIMILAR PAST SETUPS ({direction}, ADX≈{adx:.0f}, RSI≈{rsi:.0f}, {now_session}):")
                parts.append(f"  Found {len(similar)} matches → {wins}W/{losses}L ({wr:.0f}% WR) | avg {avg_p:+.1f} pips")
                for s in similar[:5]:  # show top 5 details, stats from all 10
                    icon = '✅' if s['outcome'] == 'WIN' else '❌'
                    parts.append(f"  {icon} {s['direction']} → {s['outcome']} "
                                 f"{(s['pips'] or 0):+.1f}p conf={s['confidence'] or 0:.0%} "
                                 f"({s['duration_min'] or 0:.0f}min)")
                if wins == 0 and len(similar) >= 3:
                    parts.append(f"  ⛔ 0/{len(similar)} similar {direction} setups won — strongly consider HOLD or opposite direction")
                elif wr >= 65:
                    parts.append(f"  ✅ {wr:.0f}% similar setups WON — this pattern has edge, increase confidence")
                elif wr <= 35:
                    parts.append(f"  ⚠️ Only {wr:.0f}% WR on similar setups — reduce confidence or HOLD")

        if not parts:
            return ""  # No memory yet

        return "\n".join(parts)

    # ── Similar-setup recall ─────────────────────────────────────────────────

    def get_similar_setups(self, symbol: str, direction: str,
                           adx: float, rsi: float, session: str,
                           limit: int = 10) -> list:
        """
        Find past trades with similar market conditions to give the AI
        concrete pattern memory ("last time this happened, X occurred").
        Matches: same symbol + direction + session, ADX within ±8, RSI within ±12.
        Searches last 100 trades (wider pool = more matches).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT o.direction, o.outcome, o.pips_result, o.confidence,
                       o.duration_min, o.conditions_json, o.factors_json
                FROM trade_outcomes o
                WHERE o.symbol=? AND o.direction=?
                ORDER BY o.id DESC LIMIT 100
            """, (symbol, direction)).fetchall()

        matches = []
        for r in rows:
            try:
                cond = json.loads(r['conditions_json'] or '{}')
                fac  = json.loads(r['factors_json']   or '{}')
                r_adx = cond.get('adx') or fac.get('f5_adx_strength', 0)
                r_rsi = cond.get('rsi') or 50
                r_sess = cond.get('session', '')

                adx_match  = abs((r_adx or 0) - adx)  < 8
                rsi_match  = abs((r_rsi or 50) - rsi) < 12
                sess_match = (not r_sess) or r_sess == session

                if adx_match and rsi_match and sess_match:
                    matches.append({
                        "direction":    r['direction'],
                        "outcome":      r['outcome'],
                        "pips":         r['pips_result'],
                        "confidence":   r['confidence'],
                        "duration_min": r['duration_min'],
                    })
                    if len(matches) >= limit:
                        break
            except Exception:
                continue
        return matches

    def get_session_win_rate(self, symbol: str, session: str,
                             min_trades: int = 5) -> dict | None:
        """
        Return win-rate stats for this symbol in the current session.
        Returns None if fewer than min_trades recorded (not enough data).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                    AVG(pips_result) as avg_pips
                FROM trade_outcomes
                WHERE symbol=? AND conditions_json LIKE ?
            """, (symbol, f'%{session}%')).fetchone()

        if not row or (row['total'] or 0) < min_trades:
            return None
        total = row['total']
        wins  = row['wins'] or 0
        return {
            "total":    total,
            "win_rate": round(wins / total, 3),
            "avg_pips": round(row['avg_pips'] or 0, 1),
        }

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
                        if not isinstance(value, (int, float)) or isinstance(value, bool):
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
    def _safe_json(obj):
        """Convert numpy/bool types to JSON-serializable Python types."""
        import math
        if isinstance(obj, dict):
            return {k: TradeMemory._safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [TradeMemory._safe_json(i) for i in obj]
        if type(obj).__name__ == 'bool':
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
