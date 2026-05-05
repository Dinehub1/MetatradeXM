"""
supabase_db.py — Supabase PostgreSQL implementation of DatabaseAdapter

Connects to Supabase cloud PostgreSQL with real-time RealtimeAPI support.
Handles all trade memory operations with JSONB columns for factors/conditions.
"""

import json
import os
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from core.db_adapter import DatabaseAdapter
from core.logger_factory import get_logger
from core.utils import now_utc


log = get_logger("supabase_db")


class SupabaseDB(DatabaseAdapter):
    """Supabase PostgreSQL implementation with real-time listeners."""

    def __init__(self, url: str = None, key: str = None):
        """
        Initialize Supabase client.
        Args:
            url: Supabase project URL (e.g., https://xxxx.supabase.co)
            key: Supabase anon/service role key
        """
        self.url = url or os.getenv("SUPABASE_URL")
        self.key = key or os.getenv("SUPABASE_ANON_KEY")

        if not self.url or not self.key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_ANON_KEY required. "
                "Set in .env or pass as arguments."
            )

        self.client: Client = create_client(self.url, self.key)
        log.info(f"Connected to Supabase: {self.url}")

    # ── Record trade entry ───────────────────────────────────────────────────

    def record_entry(self, ticket: str, symbol: str, direction: str,
                     entry_price: float, confidence: float,
                     factors: dict = None, conditions: dict = None,
                     skills_used: list = None, volume: float = None):
        """Record when a new trade is opened."""
        ts = now_utc()
        data = {
            "ts": ts.isoformat(),
            "ticket": str(ticket),
            "symbol": symbol,
            "broker_symbol": (conditions or {}).get("broker_symbol"),
            "direction": direction,
            "entry_price": entry_price,
            "confidence": confidence,
            "factors_json": self._safe_json(factors or {}),
            "conditions_json": self._safe_json(conditions or {}),
            "skills_used": skills_used or [],
            "volume": volume,
            "closed": 0,
        }
        data = {k: v for k, v in data.items() if v is not None}

        response = self.client.table("trade_entries").insert(data).execute()
        if response.data:
            log.info(f"[MEMORY] Recorded entry: {symbol} {direction} #{ticket} conf={confidence:.0%}")
        else:
            log.error(f"Failed to record entry: {response}")

    # ── Record trade outcome ─────────────────────────────────────────────────

    def record_outcome(self, ticket: str, exit_price: float,
                       pips_result: float, outcome: str,
                       symbol: str = "UNKNOWN", direction: str = "UNKNOWN",
                       profit_usd: float = None, volume: float = None):
        """Record when a trade closes. Links back to entry data."""
        ts = now_utc()

        # Find the entry record
        entry_response = (
            self.client.table("trade_entries")
            .select("*")
            .eq("ticket", str(ticket))
            .eq("closed", 0)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )

        if entry_response.data and len(entry_response.data) > 0:
            entry = entry_response.data[0]
            entry_ts = entry["ts"]
            symbol = entry["symbol"]
            direction = entry["direction"]
            entry_price = entry["entry_price"]
            confidence = entry["confidence"]
            factors_json = entry["factors_json"] or {}
            conditions_json = entry["conditions_json"] or {}
            skills_used = entry["skills_used"] or []
            broker_symbol = entry.get("broker_symbol") or conditions_json.get("broker_symbol")
            if volume is None:
                volume = entry.get("volume")

            # Calculate duration
            try:
                entry_dt = datetime.fromisoformat(entry_ts)
                duration = (now_utc() - entry_dt).total_seconds() / 60
            except (ValueError, TypeError):
                duration = 0

            outcome_data = {
                "ts": ts.isoformat(),
                "ticket": str(ticket),
                "symbol": symbol,
                "broker_symbol": broker_symbol,
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pips_result": pips_result,
                "profit_usd": profit_usd,
                "volume": volume,
                "confidence": confidence,
                "factors_json": factors_json,
                "conditions_json": conditions_json,
                "duration_min": duration,
                "outcome": outcome,
                "skills_used": skills_used,
            }
            self._upsert_trade_outcome(str(ticket), outcome_data)

            # Mark entry as closed
            self.client.table("trade_entries").update({"closed": 1}).eq(
                "id", entry["id"]
            ).execute()

            # Record pattern
            now = now_utc()
            pattern_data = {
                "symbol": symbol,
                "hour_utc": now.hour,
                "day_of_week": now.weekday(),
                "session": self._get_session_name(now.hour),
                "direction": direction,
                "outcome": outcome,
                "pips": pips_result,
                "ts": ts.isoformat(),
            }
            self.client.table("market_patterns").insert(pattern_data).execute()

            log.info(
                f"[MEMORY] Recorded outcome: {symbol} {direction} #{ticket} "
                f"{outcome} {pips_result:+.1f} pips (duration: {duration:.0f}min)"
            )
        else:
            # No entry found — record outcome anyway
            outcome_data = {
                "ts": ts.isoformat(),
                "ticket": str(ticket),
                "symbol": symbol,
                "broker_symbol": None,
                "direction": direction,
                "entry_price": 0,
                "exit_price": exit_price,
                "pips_result": pips_result,
                "profit_usd": profit_usd,
                "volume": volume,
                "confidence": 0,
                "factors_json": {},
                "conditions_json": {},
                "duration_min": 0,
                "outcome": outcome,
                "skills_used": [],
            }
            self._upsert_trade_outcome(str(ticket), outcome_data)

    def sync_broker_history(self, deals: list):
        """Reconcile recent MT5 deal history into Supabase by position id."""
        if not deals:
            return 0

        opened = {}
        closed = {}
        for deal in deals:
            try:
                position_id = str(deal.get("position_id") or deal.get("order") or "")
                if not position_id:
                    continue
                if deal.get("entry") == 0:
                    opened[position_id] = deal
                elif deal.get("entry") == 1:
                    bucket = closed.setdefault(position_id, {
                        "profit_usd": 0.0,
                        "exit_price": deal.get("price", 0),
                        "volume": 0.0,
                        "closed_at": deal.get("time"),
                        "comment": deal.get("comment", ""),
                    })
                    bucket["profit_usd"] += float(deal.get("profit", 0) or 0)
                    bucket["volume"] += float(deal.get("volume", 0) or 0)
                    bucket["exit_price"] = deal.get("price", bucket["exit_price"])
                    bucket["closed_at"] = max(bucket["closed_at"] or 0, deal.get("time") or 0)
                    bucket["comment"] = deal.get("comment") or bucket["comment"]
            except Exception as e:
                log.debug(f"Broker history parse error: {e}")

        synced = 0
        for ticket, close_info in closed.items():
            entry = opened.get(ticket)
            if not entry:
                continue

            entry_price = float(entry.get("price", 0) or 0)
            volume = float(entry.get("volume", 0) or close_info.get("volume") or 0)
            profit_usd = float(close_info.get("profit_usd", 0) or 0)
            exit_price = float(close_info.get("exit_price", 0) or 0)
            symbol = self._normalize_symbol(entry.get("symbol", ""))
            broker_symbol = entry.get("symbol")
            direction = "BUY" if int(entry.get("type", 0)) == 0 else "SELL"
            ts = datetime.fromtimestamp(int(close_info.get("closed_at") or now_utc().timestamp()), tz=timezone.utc).isoformat()

            pips_result = None
            pip_size = self._pip_size_for_symbol(symbol)
            contract_size = 100 if symbol == "XAUUSD" else 5000 if symbol == "XAGUSD" else 100
            pip_value = pip_size * contract_size * volume if volume else 0
            if pip_value:
                pips_result = round(profit_usd / pip_value, 1)

            entry_update = {
                "closed": 1,
                "volume": volume,
                "broker_symbol": broker_symbol,
            }
            self.client.table("trade_entries").update(entry_update).eq("ticket", ticket).execute()

            existing = (
                self.client.table("trade_outcomes")
                .select("id")
                .eq("ticket", ticket)
                .order("id", desc=True)
                .limit(1)
                .execute()
            )
            outcome_data = {
                "ts": ts,
                "ticket": ticket,
                "symbol": symbol,
                "broker_symbol": broker_symbol,
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pips_result": pips_result,
                "profit_usd": profit_usd,
                "volume": volume,
                "outcome": "WIN" if profit_usd > 0 else "LOSS" if profit_usd < 0 else "FLAT",
            }
            outcome_data = {k: v for k, v in outcome_data.items() if v is not None}
            self._upsert_trade_outcome(ticket, outcome_data)
            synced += 1

        return synced

    def _upsert_trade_outcome(self, ticket: str, outcome_data: dict):
        """Keep a single latest outcome row per ticket to avoid duplicate learning rows."""
        existing = (
            self.client.table("trade_outcomes")
            .select("id")
            .eq("ticket", str(ticket))
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            self.client.table("trade_outcomes").update(outcome_data).eq(
                "id", existing.data[0]["id"]
            ).execute()
        else:
            self.client.table("trade_outcomes").insert(outcome_data).execute()

    # ── Record filtered trade ────────────────────────────────────────────────

    def record_filtered(self, symbol: str, direction: str, confidence: float,
                        reasons: list, factors: dict = None):
        """Record when a trade was filtered out (for self-improvement tracking)."""
        ts = now_utc()
        data = {
            "ts": ts.isoformat(),
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "filter_reasons": self._safe_json(reasons),
            "factors_json": self._safe_json(factors or {}),
        }
        self.client.table("filtered_trades").insert(data).execute()

    # ── Prefetch context for AI reasoning ────────────────────────────────────

    def prefetch_context(self, symbol: str, current_conditions: dict = None,
                         direction: str = None, adx: float = 0.0,
                         rsi: float = 50.0) -> str:
        """
        Returns memory context block for AI reasoning.
        Includes: aggregate performance, per-direction WR, current streak,
        recent trades, session stats, and learning insights.
        """
        parts = []

        # ── AGGREGATE PERFORMANCE ──
        stats_response = (
            self.client.table("trade_outcomes")
            .select("*")
            .eq("symbol", symbol)
            .execute()
        )
        outcomes = stats_response.data or []

        if outcomes:
            total = len(outcomes)
            wins = sum(1 for o in outcomes if o["outcome"] == "WIN")
            losses = sum(1 for o in outcomes if o["outcome"] == "LOSS")
            avg_pips = sum(o["pips_result"] or 0 for o in outcomes) / total if total > 0 else 0
            wr = wins / total * 100 if total > 0 else 0

            parts.append(f"📊 PERFORMANCE ON {symbol} (informational — strategy was recalibrated 2026-05-01):")
            parts.append(f"  Total: {total} trades | Win Rate: {wr:.0f}% | Avg: {avg_pips:+.1f} pips")

        # ── WIN RATE BY DIRECTION ──
        direction_stats = {}
        for o in outcomes:
            d = o["direction"]
            if d not in direction_stats:
                direction_stats[d] = {"total": 0, "wins": 0}
            direction_stats[d]["total"] += 1
            if o["outcome"] == "WIN":
                direction_stats[d]["wins"] += 1

        for d, stats in direction_stats.items():
            ds_total = stats["total"]
            ds_wins = stats["wins"]
            if ds_total >= 5:
                wr = ds_wins / ds_total * 100
                if wr < 30:
                    parts.append(f"  Note: {d} historical WR {wr:.0f}% ({ds_wins}/{ds_total}) — proceed only with strong confluence")

        # ── CURRENT STREAK ──
        if outcomes:
            streak = 0
            streak_type = outcomes[0]["outcome"]
            for o in outcomes:
                if o["outcome"] == streak_type:
                    streak += 1
                else:
                    break
            if streak >= 5:
                icon = "🔴" if streak_type == "LOSS" else "🟢"
                parts.append(f"  {icon} Recent streak: {streak} consecutive {streak_type}s (informational only)")

        # Last 5 trade outcomes for this symbol
        recent = sorted(outcomes, key=lambda x: x["ts"], reverse=True)[:5]
        if recent:
            parts.append("\nRecent trades for this symbol:")
            for r in recent:
                parts.append(
                    f"  {r['direction']} → {r['outcome']} "
                    f"{r['pips_result']:+.1f}pips conf={r['confidence']:.0%} "
                    f"({r['duration_min']:.0f}min)"
                )

        # Win rate by current session + hour
        patterns_response = (
            self.client.table("market_patterns")
            .select("*")
            .eq("symbol", symbol)
            .execute()
        )
        patterns = patterns_response.data or []

        now = now_utc()
        session = self._get_session_name(now.hour)
        session_patterns = [p for p in patterns if p["session"] == session]

        if session_patterns:
            parts.append(f"\nPerformance in {session} session:")
            for d in ["BUY", "SELL"]:
                d_patterns = [p for p in session_patterns if p["direction"] == d]
                if d_patterns:
                    wins = sum(1 for p in d_patterns if p["outcome"] == "WIN")
                    avg_pips = sum(p["pips"] or 0 for p in d_patterns) / len(d_patterns)
                    parts.append(f"  {d} {session_patterns[0]['outcome']}: {len(d_patterns)} trades, avg {avg_pips:+.1f}pips")

        # Recent learning insights
        learning_response = (
            self.client.table("learning_log")
            .select("*")
            .eq("applied", 1)
            .order("id", desc=True)
            .limit(3)
            .execute()
        )
        insights = learning_response.data or []

        if insights:
            parts.append("\nActive learning insights:")
            for ins in insights:
                parts.append(f"  [{ins['insight_type']}] {ins['insight_text']}")

        # ── SIMILAR SETUP RECALL ──
        if direction and adx > 0:
            similar = self.get_similar_setups(symbol, direction, adx, rsi, session, limit=10)
            if similar:
                wins = sum(1 for s in similar if s["outcome"] == "WIN")
                losses = sum(1 for s in similar if s["outcome"] == "LOSS")
                avg_p = sum(s["pips"] or 0 for s in similar) / len(similar)
                wr = wins / len(similar) * 100 if similar else 0

                parts.append(f"\n⚡ SIMILAR PAST SETUPS ({direction}, ADX≈{adx:.0f}, RSI≈{rsi:.0f}, {session}):")
                parts.append(f"  Found {len(similar)} matches → {wins}W/{losses}L ({wr:.0f}% WR) | avg {avg_p:+.1f} pips")

                for s in similar[:5]:
                    icon = "✅" if s["outcome"] == "WIN" else "❌"
                    parts.append(
                        f"  {icon} {s['direction']} → {s['outcome']} "
                        f"{(s['pips'] or 0):+.1f}p conf={s['confidence'] or 0:.0%} "
                        f"({s['duration_min'] or 0:.0f}min)"
                    )

                if wins == 0 and len(similar) >= 3:
                    parts.append(f"  ⛔ 0/{len(similar)} similar {direction} setups won — strongly consider HOLD or opposite direction")
                elif wr >= 65:
                    parts.append(f"  ✅ {wr:.0f}% similar setups WON — this pattern has edge, increase confidence")
                elif wr <= 35:
                    parts.append(f"  ⚠️ Only {wr:.0f}% WR on similar setups — reduce confidence or HOLD")

        if not parts:
            return ""

        return "\n".join(parts)

    # ── Similar-setup recall ─────────────────────────────────────────────────

    def get_similar_setups(self, symbol: str, direction: str,
                           adx: float, rsi: float, session: str,
                           limit: int = 10) -> list:
        """
        Find past trades with similar market conditions.
        Matches: same symbol + direction + session, ADX within ±8, RSI within ±12.
        """
        response = (
            self.client.table("trade_outcomes")
            .select("*")
            .eq("symbol", symbol)
            .eq("direction", direction)
            .order("id", desc=True)
            .limit(100)
            .execute()
        )

        rows = response.data or []
        matches = []

        for r in rows:
            try:
                cond = r.get("conditions_json") or {}
                fac = r.get("factors_json") or {}
                r_adx = cond.get("adx") or fac.get("f5_adx_strength", 0)
                r_rsi = cond.get("rsi") or 50
                r_sess = cond.get("session", "")

                adx_match = abs((r_adx or 0) - adx) < 8
                rsi_match = abs((r_rsi or 50) - rsi) < 12
                sess_match = (not r_sess) or r_sess == session

                if adx_match and rsi_match and sess_match:
                    matches.append({
                        "direction": r["direction"],
                        "outcome": r["outcome"],
                        "pips": r["pips_result"],
                        "confidence": r["confidence"],
                        "duration_min": r["duration_min"],
                    })
                    if len(matches) >= limit:
                        break
            except Exception as e:
                log.debug(f"Error processing similar setup: {e}")
                continue

        return matches

    def get_session_win_rate(self, symbol: str, session: str,
                             min_trades: int = 5) -> Optional[dict]:
        """Return win-rate stats for this symbol in the current session."""
        response = (
            self.client.table("trade_outcomes")
            .select("*")
            .eq("symbol", symbol)
            .execute()
        )

        outcomes = response.data or []
        session_outcomes = [
            o for o in outcomes
            if session in (o.get("conditions_json") or {}).get("session", "")
        ]

        if len(session_outcomes) < min_trades:
            return None

        total = len(session_outcomes)
        wins = sum(1 for o in session_outcomes if o["outcome"] == "WIN")
        avg_pips = sum(o["pips_result"] or 0 for o in session_outcomes) / total

        return {
            "total": total,
            "win_rate": round(wins / total, 3),
            "avg_pips": round(avg_pips, 1),
        }

    # ── Statistics for self-improvement ───────────────────────────────────────

    def get_recent_outcomes(self, hours: int = 24) -> list:
        """Get all trade outcomes from the last N hours."""
        cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
        response = (
            self.client.table("trade_outcomes")
            .select("*")
            .gte("ts", cutoff)
            .order("ts", desc=False)
            .execute()
        )
        return response.data or []

    def get_all_outcomes(self, symbol: str = None, limit: int = 100) -> list:
        """Get trade outcomes, optionally filtered by symbol."""
        query = self.client.table("trade_outcomes").select("*").order("id", desc=True).limit(limit)
        if symbol:
            query = query.eq("symbol", symbol)
        response = query.execute()
        return response.data or []

    # ── Live dashboard source of truth ───────────────────────────────────────

    def upsert_live_market_snapshot(
        self,
        symbol: str,
        *,
        broker_symbol: str = None,
        payload: dict = None,
        source: str = "bot",
    ):
        """Upsert the latest market/AI snapshot for one dashboard symbol."""
        payload = payload or {}
        existing = {}
        try:
            response = (
                self.client.table("live_market_snapshots")
                .select("indicators_json,timeframes_json,raw_json")
                .eq("symbol", symbol)
                .limit(1)
                .execute()
            )
            existing = (response.data or [{}])[0] if response.data else {}
        except Exception:
            existing = {}

        existing_raw = existing.get("raw_json") or {}
        indicators = payload.get("indicators") or existing.get("indicators_json") or {}
        timeframes = payload.get("timeframes") or existing.get("timeframes_json") or {}
        raw_json = dict(existing_raw)
        raw_json.update(payload)
        if "candles" not in payload and "candles" in existing_raw:
            raw_json["candles"] = existing_raw["candles"]
        if "timeframes" not in payload and "timeframes" in existing_raw:
            raw_json["timeframes"] = existing_raw["timeframes"]
        ts = payload.get("ts") or payload.get("time")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if not ts:
            ts = now_utc().isoformat()

        data = {
            "symbol": symbol,
            "broker_symbol": broker_symbol or payload.get("broker_symbol"),
            "ts": ts,
            "price": payload.get("price") or indicators.get("price"),
            "bid": payload.get("bid"),
            "ask": payload.get("ask"),
            "spread": payload.get("spread") or indicators.get("spread"),
            "digits": payload.get("digits"),
            "atr": payload.get("atr") or indicators.get("atr"),
            "daily_high": payload.get("daily_high"),
            "daily_low": payload.get("daily_low"),
            "signal": payload.get("signal"),
            "confidence": payload.get("confidence"),
            "score": payload.get("score") or indicators.get("score"),
            "session": payload.get("session"),
            "indicators_json": self._safe_json(indicators),
            "timeframes_json": self._safe_json(timeframes),
            "raw_json": self._safe_json(raw_json),
            "source": source,
            "updated_at": now_utc().isoformat(),
        }
        data = {k: v for k, v in data.items() if v is not None}
        self.client.table("live_market_snapshots").upsert(data, on_conflict="symbol").execute()

    def get_live_market_snapshots(self) -> dict:
        """Return latest market/AI snapshots keyed by display symbol."""
        response = (
            self.client.table("live_market_snapshots")
            .select("*")
            .order("updated_at", desc=True)
            .execute()
        )
        rows = response.data or []
        return {row.get("symbol"): row for row in rows if row.get("symbol")}

    def upsert_live_account_snapshot(self, account: dict, source: str = "bot"):
        """Upsert the singleton latest account snapshot."""
        account = account or {}
        data = {
            "id": 1,
            "ts": account.get("ts") or now_utc().isoformat(),
            "balance": account.get("balance"),
            "equity": account.get("equity"),
            "margin": account.get("margin"),
            "margin_free": account.get("margin_free", account.get("free")),
            "currency": account.get("currency", "USD"),
            "leverage": account.get("leverage"),
            "raw_json": self._safe_json(account),
            "source": source,
            "updated_at": now_utc().isoformat(),
        }
        data = {k: v for k, v in data.items() if v is not None}
        self.client.table("live_account_snapshots").upsert(data, on_conflict="id").execute()

    def get_live_account_snapshot(self) -> dict:
        response = (
            self.client.table("live_account_snapshots")
            .select("*")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
        return (response.data or [{}])[0] if response.data else {}

    def replace_live_positions(self, positions: list, source: str = "bot"):
        """Replace the live open-position set with the current broker truth."""
        now = now_utc().isoformat()
        rows = []
        for p in positions or []:
            rows.append({
                "ticket": str(p.get("ticket", "")),
                "symbol": p.get("symbol"),
                "direction": p.get("direction"),
                "volume": p.get("volume", p.get("lot_size")),
                "entry_price": p.get("entry_price", p.get("open_price")),
                "current_price": p.get("current_price"),
                "profit_loss_usd": p.get("profit_loss_usd", p.get("profit")),
                "profit_loss_pct": p.get("profit_loss_pct"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "entry_time": p.get("entry_time"),
                "status": p.get("status", "OPEN"),
                "raw_json": self._safe_json(p),
                "source": source,
                "updated_at": now,
            })

        self.client.table("live_positions").delete().neq("ticket", "__never__").execute()
        if rows:
            self.client.table("live_positions").insert(rows).execute()

    def get_live_positions(self) -> list:
        response = (
            self.client.table("live_positions")
            .select("*")
            .order("updated_at", desc=True)
            .execute()
        )
        return response.data or []

    def log_live_event(
        self,
        event_type: str,
        payload: dict,
        *,
        source: str = "bot",
        symbol: str = None,
        severity: str = "INFO",
    ):
        data = {
            "ts": now_utc().isoformat(),
            "event_type": event_type,
            "source": source,
            "symbol": symbol,
            "severity": severity,
            "payload": self._safe_json(payload or {}),
        }
        self.client.table("live_events").insert(data).execute()

    def log_runtime_event(
        self,
        event_type: str,
        payload: dict,
        *,
        source: str,
        symbol: str = None,
        severity: str = "INFO",
    ):
        """Record non-dashboard runtime events into the Supabase live event stream."""
        return self.log_live_event(
            event_type,
            payload,
            source=source,
            symbol=symbol,
            severity=severity,
        )

    def get_live_events(self, limit: int = 150) -> list:
        response = (
            self.client.table("live_events")
            .select("*")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    def get_factor_stats(self) -> dict:
        """Compute win rate per factor value range."""
        response = (
            self.client.table("trade_outcomes")
            .select("*")
            .neq("factors_json", {})
            .execute()
        )

        outcomes = response.data or []
        stats = {}

        for outcome in outcomes:
            try:
                factors = outcome.get("factors_json") or {}
                outcome_type = outcome["outcome"]

                for factor_name, value in factors.items():
                    if factor_name in ["adx_regime", "bb_squeeze"]:
                        continue
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        continue

                    if factor_name not in stats:
                        stats[factor_name] = {"win_vals": [], "loss_vals": []}

                    if outcome_type == "WIN":
                        stats[factor_name]["win_vals"].append(value)
                    elif outcome_type == "LOSS":
                        stats[factor_name]["loss_vals"].append(value)
            except Exception as e:
                log.debug(f"Error processing factor stats: {e}")
                continue

        result = {}
        for name, data in stats.items():
            wins = data["win_vals"]
            losses = data["loss_vals"]
            total = len(wins) + len(losses)
            result[name] = {
                "win_rate": len(wins) / total if total > 0 else 0,
                "avg_when_win": sum(wins) / len(wins) if wins else 0,
                "avg_when_loss": sum(losses) / len(losses) if losses else 0,
                "sample_size": total,
            }
        return result

    def log_learning(self, insight_type: str, insight_text: str,
                     data: dict = None, applied: bool = False):
        """Record a learning insight from the self-improvement engine."""
        ts = now_utc()
        entry_data = {
            "ts": ts.isoformat(),
            "insight_type": insight_type,
            "insight_text": insight_text,
            "data_json": self._safe_json(data or {}),
            "applied": int(applied),
        }
        self.client.table("learning_log").insert(entry_data).execute()
        log.info(f"[MEMORY] Learning: [{insight_type}] {insight_text}")

    def get_recent_learning(self, limit: int = 5) -> list:
        response = (
            self.client.table("learning_log")
            .select("insight_type,insight_text")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    def get_pattern_summary(self, symbol: str) -> dict:
        """Get hourly and daily pattern summary for a symbol."""
        response = (
            self.client.table("market_patterns")
            .select("*")
            .eq("symbol", symbol)
            .execute()
        )

        patterns = response.data or []
        hourly = {}
        daily = {}

        for p in patterns:
            h = p["hour_utc"]
            d = p["day_of_week"]

            if h not in hourly:
                hourly[h] = {"wins": 0, "losses": 0, "total_pips": 0}
            if p["outcome"] == "WIN":
                hourly[h]["wins"] += 1
            else:
                hourly[h]["losses"] += 1
            hourly[h]["total_pips"] += p["pips"] or 0

            if d not in daily:
                daily[d] = {"wins": 0, "losses": 0, "total_pips": 0}
            if p["outcome"] == "WIN":
                daily[d]["wins"] += 1
            else:
                daily[d]["losses"] += 1
            daily[d]["total_pips"] += p["pips"] or 0

        return {"hourly": hourly, "daily": daily}

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_json(obj):
        """Convert numpy/bool types to JSON-serializable Python types."""
        import math
        if isinstance(obj, dict):
            return {k: SupabaseDB._safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [SupabaseDB._safe_json(i) for i in obj]
        if type(obj).__name__ == "bool":
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
        if 7 <= hour < 13:
            return "LONDON"
        if 13 <= hour < 16:
            return "LONDON_NY_OVERLAP"
        if 16 <= hour < 22:
            return "NEW_YORK"
        return "ASIAN"

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        mapping = {
            "GOLD.i#": "XAUUSD",
            "SILVER.i#": "XAGUSD",
        }
        return mapping.get(symbol, symbol)

    @staticmethod
    def _pip_size_for_symbol(symbol: str) -> float:
        if symbol == "XAUUSD":
            return 0.1
        if symbol == "XAGUSD":
            return 0.01
        return 0.0001

    def close(self):
        """Close database connection (no-op for Supabase)."""
        pass
