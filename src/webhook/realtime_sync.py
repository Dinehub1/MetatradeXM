"""
realtime_sync.py — Real-time webhook integration for Supabase

Listens to all database changes (trade outcomes, entries, patterns, learning)
and processes them in real-time for immediate bot response and monitoring.

Features:
  - Real-time trade event processing
  - Automatic multi-instance sync
  - Event replay and audit trail
  - Fallback to polling if websocket fails
"""

import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, List
from pathlib import Path
import sqlite3

from core.logger_factory import get_logger
from core.supabase_realtime import SupabaseRealtimeListener
from core.utils import now_utc

log = get_logger("realtime_sync")


class RealtimeSyncManager:
    """Manages real-time sync from Supabase for all trade events."""

    def __init__(self, webhook_log_path: str = None):
        """
        Initialize real-time sync manager.

        Args:
            webhook_log_path: Path to SQLite DB for webhook event logging/replay
        """
        self.realtime = SupabaseRealtimeListener()
        self.webhook_log_path = webhook_log_path or str(Path.cwd() / "state" / "webhook_events.db")
        self.callbacks: Dict[str, List[Callable]] = {
            "trade_entry": [],
            "trade_outcome": [],
            "market_pattern": [],
            "learning_insight": [],
        }
        self._running = False
        self._init_webhook_log()

        log.info(f"RealtimeSyncManager initialized (webhook log: {self.webhook_log_path})")

    def _init_webhook_log(self):
        """Initialize SQLite database for webhook event audit trail."""
        with sqlite3.connect(self.webhook_log_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    payload JSONB,
                    processed INTEGER DEFAULT 0,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_webhook_ts ON webhook_events(ts);
                CREATE INDEX IF NOT EXISTS idx_webhook_type ON webhook_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_webhook_processed ON webhook_events(processed);
            """)

    def register_callback(self, event_type: str, callback: Callable):
        """
        Register a callback for an event type.

        Args:
            event_type: 'trade_entry', 'trade_outcome', 'market_pattern', 'learning_insight'
            callback: Function(event_data) to call when event occurs
        """
        if event_type not in self.callbacks:
            raise ValueError(f"Unknown event type: {event_type}")
        self.callbacks[event_type].append(callback)
        log.info(f"Registered callback for {event_type}")

    def start(self):
        """Start listening to all real-time events."""
        if self._running:
            log.warning("Realtime sync already running")
            return

        self._running = True
        self._start_listeners()
        log.info("✅ Real-time sync started")

    def stop(self):
        """Stop listening to real-time events."""
        self._running = False
        log.info("Real-time sync stopped")

    def _start_listeners(self):
        """Set up all real-time listeners."""
        # Listen to trade outcomes
        self.realtime.listen_to_trade_outcomes(callback=self._on_trade_outcome)

        # Listen to trade entries
        self.realtime.listen_to_trade_entries(callback=self._on_trade_entry)

        # Listen to market patterns
        self.realtime.listen_to_market_patterns(callback=self._on_market_pattern)

        # Listen to learning log
        self.realtime.listen_to_learning_log(callback=self._on_learning_log)

    def _log_webhook_event(self, event_type: str, event_name: str, payload: dict,
                          error: str = None):
        """Log webhook event for audit trail and replay."""
        ts = now_utc().isoformat()
        with sqlite3.connect(self.webhook_log_path) as conn:
            conn.execute("""
                INSERT INTO webhook_events (ts, event_type, event_name, payload, error)
                VALUES (?, ?, ?, ?, ?)
            """, (ts, event_type, event_name, json.dumps(payload), error))

    def _on_trade_entry(self, event_type: str, data: dict):
        """Handle trade entry events (INSERT/UPDATE)."""
        try:
            log.info(f"📥 [REALTIME] Trade entry {event_type}: "
                    f"#{data.get('ticket')} {data.get('symbol')} "
                    f"{data.get('direction')} @ {data.get('entry_price')}")

            self._log_webhook_event("trade_entry", event_type, data)

            # Call registered callbacks
            for callback in self.callbacks["trade_entry"]:
                callback({
                    "event": event_type,
                    "ticket": data.get("ticket"),
                    "symbol": data.get("symbol"),
                    "direction": data.get("direction"),
                    "entry_price": data.get("entry_price"),
                    "confidence": data.get("confidence"),
                    "timestamp": data.get("ts"),
                })
        except Exception as e:
            log.error(f"Error processing trade entry: {e}")
            self._log_webhook_event("trade_entry", event_type, data, error=str(e))

    def _on_trade_outcome(self, event_type: str, data: dict):
        """Handle trade outcome events (INSERT/UPDATE/DELETE)."""
        try:
            log.info(f"📊 [REALTIME] Trade outcome {event_type}: "
                    f"#{data.get('ticket')} {data.get('outcome')} "
                    f"{data.get('pips_result', 0):+.1f}p")

            self._log_webhook_event("trade_outcome", event_type, data)

            # Call registered callbacks
            for callback in self.callbacks["trade_outcome"]:
                callback({
                    "event": event_type,
                    "ticket": data.get("ticket"),
                    "symbol": data.get("symbol"),
                    "outcome": data.get("outcome"),
                    "pips_result": data.get("pips_result"),
                    "confidence": data.get("confidence"),
                    "event_type": data.get("event_type", "PRIMARY"),
                    "timestamp": data.get("ts"),
                })
        except Exception as e:
            log.error(f"Error processing trade outcome: {e}")
            self._log_webhook_event("trade_outcome", event_type, data, error=str(e))

    def _on_market_pattern(self, event_type: str, data: dict):
        """Handle market pattern events."""
        try:
            log.info(f"🎯 [REALTIME] Market pattern {event_type}: "
                    f"{data.get('symbol')} {data.get('session')} "
                    f"{data.get('direction')} → {data.get('outcome')} "
                    f"{data.get('pips', 0):+.1f}p")

            self._log_webhook_event("market_pattern", event_type, data)

            # Call registered callbacks
            for callback in self.callbacks["market_pattern"]:
                callback({
                    "event": event_type,
                    "symbol": data.get("symbol"),
                    "session": data.get("session"),
                    "direction": data.get("direction"),
                    "outcome": data.get("outcome"),
                    "pips": data.get("pips"),
                    "hour_utc": data.get("hour_utc"),
                    "timestamp": data.get("ts"),
                })
        except Exception as e:
            log.error(f"Error processing market pattern: {e}")
            self._log_webhook_event("market_pattern", event_type, data, error=str(e))

    def _on_learning_log(self, event_type: str, data: dict):
        """Handle learning log events (AI insights)."""
        try:
            log.info(f"🧠 [REALTIME] Learning insight {event_type}: "
                    f"[{data.get('insight_type')}] {data.get('insight_text', '')[:60]}")

            self._log_webhook_event("learning_insight", event_type, data)

            # Call registered callbacks
            for callback in self.callbacks["learning_insight"]:
                callback({
                    "event": event_type,
                    "insight_type": data.get("insight_type"),
                    "insight_text": data.get("insight_text"),
                    "applied": data.get("applied", 0),
                    "timestamp": data.get("ts"),
                })
        except Exception as e:
            log.error(f"Error processing learning insight: {e}")
            self._log_webhook_event("learning_insight", event_type, data, error=str(e))

    def get_webhook_history(self, limit: int = 100) -> list:
        """Get recent webhook events for monitoring/debugging."""
        with sqlite3.connect(self.webhook_log_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM webhook_events
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def replay_unprocessed_events(self):
        """Replay any webhook events that failed processing."""
        with sqlite3.connect(self.webhook_log_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, event_type, event_name, payload FROM webhook_events
                WHERE error IS NOT NULL AND processed = 0
                ORDER BY id ASC
            """).fetchall()

        replayed = 0
        for row in rows:
            try:
                payload = json.loads(row["payload"])

                if row["event_type"] == "trade_outcome":
                    self._on_trade_outcome(row["event_name"], payload)
                elif row["event_type"] == "trade_entry":
                    self._on_trade_entry(row["event_name"], payload)
                elif row["event_type"] == "market_pattern":
                    self._on_market_pattern(row["event_name"], payload)
                elif row["event_type"] == "learning_insight":
                    self._on_learning_log(row["event_name"], payload)

                # Mark as processed
                with sqlite3.connect(self.webhook_log_path) as conn:
                    conn.execute(
                        "UPDATE webhook_events SET processed = 1, error = NULL WHERE id = ?",
                        (row["id"],)
                    )
                replayed += 1
            except Exception as e:
                log.error(f"Failed to replay event {row['id']}: {e}")

        if replayed > 0:
            log.info(f"✅ Replayed {replayed} failed webhook events")
