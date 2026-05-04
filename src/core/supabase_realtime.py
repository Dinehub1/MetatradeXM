"""
supabase_realtime.py — Real-time WebSocket listeners for Supabase

Uses Supabase RealtimeAPI to listen for database changes across all environments.
Automatically syncs trade outcomes, entries, and learning insights in real-time.
"""

import os
import json
import threading
from datetime import datetime
from supabase import create_client, Client
from core.logger_factory import get_logger


log = get_logger("supabase_realtime")


class SupabaseRealtimeListener:
    """Real-time listener for Supabase table changes via WebSocket."""

    def __init__(self, url: str = None, key: str = None):
        """
        Initialize Supabase realtime listener.
        Args:
            url: Supabase project URL
            key: Supabase API key (must have realtime permissions)
        """
        self.url = url or os.getenv("SUPABASE_URL")
        self.key = key or os.getenv("SUPABASE_ANON_KEY")

        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY required")

        self.client: Client = create_client(self.url, self.key)
        self.listeners = {}
        self._running = False
        log.info(f"Initialized Supabase Realtime listener for {self.url}")

    def listen_to_trade_outcomes(self, callback=None):
        """
        Listen for new trade outcomes in real-time.
        Useful for dashboards and monitoring across environments.
        """
        def on_change(payload):
            event_type = payload.get("type")
            new_data = payload.get("new")
            old_data = payload.get("old")

            if event_type == "INSERT":
                log.info(f"[REALTIME] New trade outcome: {new_data}")
                if callback:
                    callback("INSERT", new_data)
            elif event_type == "UPDATE":
                log.info(f"[REALTIME] Trade outcome updated: {new_data}")
                if callback:
                    callback("UPDATE", new_data)
            elif event_type == "DELETE":
                log.info(f"[REALTIME] Trade outcome deleted: {old_data}")
                if callback:
                    callback("DELETE", old_data)

        # Subscribe to trade_outcomes table
        self.listeners["trade_outcomes"] = (
            self.client.realtime.on(
                "postgres_changes",
                {
                    "event": "*",
                    "schema": "public",
                    "table": "trade_outcomes",
                },
                on_change,
            )
            .subscribe()
        )
        log.info("Subscribed to trade_outcomes real-time changes")

    def listen_to_trade_entries(self, callback=None):
        """
        Listen for new trade entries in real-time.
        Useful for tracking live positions across environments.
        """
        def on_change(payload):
            event_type = payload.get("type")
            new_data = payload.get("new")

            if event_type == "INSERT":
                log.info(f"[REALTIME] New trade entry: {new_data}")
                if callback:
                    callback("INSERT", new_data)
            elif event_type == "UPDATE":
                log.info(f"[REALTIME] Trade entry closed/updated: {new_data}")
                if callback:
                    callback("UPDATE", new_data)

        self.listeners["trade_entries"] = (
            self.client.realtime.on(
                "postgres_changes",
                {
                    "event": "*",
                    "schema": "public",
                    "table": "trade_entries",
                },
                on_change,
            )
            .subscribe()
        )
        log.info("Subscribed to trade_entries real-time changes")

    def listen_to_learning_log(self, callback=None):
        """
        Listen for new learning insights in real-time.
        Useful for sharing AI model improvements across environments.
        """
        def on_change(payload):
            event_type = payload.get("type")
            new_data = payload.get("new")

            if event_type == "INSERT":
                insight = new_data.get("insight_text", "")
                log.info(f"[REALTIME] New learning insight: {insight}")
                if callback:
                    callback("INSERT", new_data)

        self.listeners["learning_log"] = (
            self.client.realtime.on(
                "postgres_changes",
                {
                    "event": "INSERT",
                    "schema": "public",
                    "table": "learning_log",
                },
                on_change,
            )
            .subscribe()
        )
        log.info("Subscribed to learning_log real-time changes")

    def listen_to_market_patterns(self, callback=None):
        """
        Listen for new market pattern records in real-time.
        Useful for real-time pattern analysis dashboards.
        """
        def on_change(payload):
            event_type = payload.get("type")
            new_data = payload.get("new")

            if event_type == "INSERT":
                log.debug(f"[REALTIME] New market pattern: {new_data}")
                if callback:
                    callback("INSERT", new_data)

        self.listeners["market_patterns"] = (
            self.client.realtime.on(
                "postgres_changes",
                {
                    "event": "INSERT",
                    "schema": "public",
                    "table": "market_patterns",
                },
                on_change,
            )
            .subscribe()
        )
        log.info("Subscribed to market_patterns real-time changes")

    def start_listening(self):
        """Start all real-time listeners in background thread."""
        self._running = True
        thread = threading.Thread(target=self._listen_loop, daemon=True)
        thread.start()
        log.info("Real-time listeners started in background")

    def _listen_loop(self):
        """Keep listeners alive."""
        import time
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Real-time listeners stopped")

    def stop_listening(self):
        """Stop all real-time listeners."""
        self._running = False
        for listener in self.listeners.values():
            try:
                self.client.realtime.unsubscribe()
            except Exception as e:
                log.debug(f"Error unsubscribing: {e}")
        log.info("Real-time listeners stopped")

    def unsubscribe(self, table_name: str):
        """Unsubscribe from a specific table."""
        if table_name in self.listeners:
            try:
                self.client.realtime.unsubscribe()
                del self.listeners[table_name]
                log.info(f"Unsubscribed from {table_name}")
            except Exception as e:
                log.warning(f"Error unsubscribing from {table_name}: {e}")
