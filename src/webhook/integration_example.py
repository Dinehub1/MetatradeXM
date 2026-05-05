"""
integration_example.py — Example of how to integrate RealtimeSyncManager

This shows how to connect real-time webhooks into the trading bot.
Copy these patterns into your continuous_trader.py main loop.
"""

from webhook.realtime_sync import RealtimeSyncManager
from core.logger_factory import get_logger

log = get_logger("integration_example")


def setup_realtime_sync():
    """
    Initialize and configure real-time sync.
    Call this at the start of continuous_trader.py
    """
    sync_manager = RealtimeSyncManager()

    # ── Register callbacks for trade entry events ──
    def on_trade_entry(event_data):
        """Called when a new trade is opened."""
        log.info(f"🔔 Trade Opened: {event_data['symbol']} {event_data['direction']} "
                f"@ {event_data['entry_price']} | Confidence: {event_data['confidence']:.0%}")

        # Example: Alert on high-confidence entries
        if event_data['confidence'] > 0.85:
            log.warning(f"🎯 HIGH CONFIDENCE TRADE DETECTED: {event_data['ticket']}")

    sync_manager.register_callback("trade_entry", on_trade_entry)

    # ── Register callbacks for trade outcome events ──
    def on_trade_outcome(event_data):
        """Called when a trade closes."""
        outcome_emoji = "✅" if event_data['outcome'] == "WIN" else "❌"
        log.info(f"{outcome_emoji} Trade Closed: {event_data['symbol']} "
                f"{data['outcome']} {event_data['pips_result']:+.1f}p | "
                f"Type: {event_data['event_type']}")

        # Example: Log losing streak
        if event_data['outcome'] == "LOSS":
            log.warning(f"📉 Loss on {event_data['ticket']}: {event_data['pips_result']:+.1f}p")

    sync_manager.register_callback("trade_outcome", on_trade_outcome)

    # ── Register callbacks for market pattern events ──
    def on_market_pattern(event_data):
        """Called when market pattern is recorded."""
        log.debug(f"📊 Pattern: {event_data['symbol']} {event_data['session']} "
                 f"{event_data['direction']} → {event_data['outcome']} "
                 f"{event_data['pips']:+.1f}p")

    sync_manager.register_callback("market_pattern", on_market_pattern)

    # ── Register callbacks for AI learning events ──
    def on_learning_insight(event_data):
        """Called when AI discovers a learning insight."""
        log.warning(f"🧠 AI Learning: [{event_data['insight_type']}] "
                   f"{event_data['insight_text']}")

        # Example: Log applied insights
        if event_data['applied']:
            log.info(f"✅ Applied insight: {event_data['insight_text'][:80]}")

    sync_manager.register_callback("learning_insight", on_learning_insight)

    # ── Start listening ──
    sync_manager.start()
    log.info("✅ Real-time sync initialized with all callbacks registered")

    return sync_manager


# ─────────────────────────────────────────────────────────────────────────────
# TO INTEGRATE INTO continuous_trader.py:
#
# 1. Add at the top of continuous_trader.py:
#    from webhook.integration_example import setup_realtime_sync
#
# 2. In the Trader.__init__ method, add:
#    self.sync_manager = setup_realtime_sync()
#
# 3. In the Trader.cleanup method, add:
#    self.sync_manager.stop()
#
# 4. That's it! Real-time events will now flow through the bot.
#
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """Test the setup locally."""
    import logging
    logging.basicConfig(level=logging.INFO)

    sync_manager = setup_realtime_sync()

    print("\n✅ Real-time sync ready!")
    print("   Listening for trade events...")
    print("   Press Ctrl+C to stop\n")

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        sync_manager.stop()
        print("\n✅ Shutdown complete")
