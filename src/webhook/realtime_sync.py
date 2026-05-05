"""
realtime_sync.py — Real-time webhook integration for Supabase

Listens to all database changes (trade outcomes, entries, patterns, learning)
and processes them in real-time for immediate bot response and monitoring.

Features:
  - Real-time trade event processing
  - Automatic multi-instance sync
  - Supabase-backed event audit trail
"""

from typing import Callable, Dict, List

from core.logger_factory import get_logger
from core.supabase_db import SupabaseDB
from core.supabase_realtime import SupabaseRealtimeListener

log = get_logger("realtime_sync")


class RealtimeSyncManager:
    """Manages real-time sync from Supabase for all trade events."""

    def __init__(self, webhook_log_path: str = None):
        """
        Initialize real-time sync manager.
        """
        if webhook_log_path:
            log.warning("Ignoring webhook_log_path=%s; Supabase is the only event log", webhook_log_path)
        self.realtime = SupabaseRealtimeListener()
        self.db = SupabaseDB()
        self.callbacks: Dict[str, List[Callable]] = {
            "trade_entry": [],
            "trade_outcome": [],
            "market_pattern": [],
            "learning_insight": [],
        }
        self._running = False
        log.info("RealtimeSyncManager initialized (Supabase event log)")

    def _init_webhook_log(self):
        return None

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
        self.db.log_runtime_event(
            "realtime_sync",
            {
                "event_name": event_name,
                "payload": payload,
                "error": error,
            },
            source="realtime_sync",
            symbol=payload.get("symbol") if isinstance(payload, dict) else None,
            severity="ERROR" if error else "INFO",
        )

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
        return [
            event for event in self.db.get_live_events(limit=limit * 2)
            if event.get("event_type") == "realtime_sync"
        ][:limit]

    def replay_unprocessed_events(self):
        """Supabase Realtime replays are handled by querying canonical tables."""
        log.info("Supabase-only realtime sync does not use a local replay queue")
