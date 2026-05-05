import time
import sys
import logging
from continuous_trader import make_bridge, connect_with_retry, build_order_params, SYMBOLS

# Configure light logging so we can see what's happening
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test")

def run_test_trade(bridge, direction):
    sym_cfg = SYMBOLS[0]  # GOLD.i#
    
    log.info(f"Fetching tick for GOLD.i#...")
    tick = bridge.get_tick("GOLD.i#")
    if not tick:
        log.warning("No tick available immediately, waiting 2 seconds...")
        time.sleep(2)
        tick = bridge.get_tick("GOLD.i#")
        if not tick:
            log.error("Still no tick. Proceeding with dummy tick.")
            class DummyTick:
                ask = 2500.0
                bid = 2499.5
            tick = DummyTick()

    order = build_order_params(
        sym_cfg=sym_cfg,
        tick=tick,
        direction=direction,
        confidence=0.99,
        atr=2.0,
        lot_reduction=1.0
    )
    
    # ── Force absolute minimum risk for the test ──
    order["lot"] = 0.01

    log.info(f"Sending {direction} request: {order}")
    result = bridge.place_order(order)
    
    if result and hasattr(result, "order"):
        ticket = str(result.order)
        log.info(f"✅ {direction} SUCCESS! Ticket ID: {ticket}")
        
        log.info("Holding position for 4 seconds...")
        time.sleep(4)
        
        log.info(f"Closing position {ticket}...")
        close_ok = bridge.close_position(ticket)
        if close_ok:
            log.info(f"✅ Position {ticket} closed successfully.")
        else:
            log.error(f"❌ Failed to close position {ticket}. You may need to close it manually.")
    else:
        log.error(f"❌ Failed to place {direction} order. Check broker connection or market hours.")

def main():
    log.info("Connecting to Windows bridge...")
    bridge = make_bridge()
    if not connect_with_retry(bridge):
        log.error("Could not connect to broker.")
        sys.exit(1)
        
    try:
        print("\n" + "="*40)
        print("   🧪 INITIATING BUY TEST")
        print("="*40)
        run_test_trade(bridge, "BUY")
        
        print("\n" + "="*40)
        print("   🧪 INITIATING SELL TEST")
        print("="*40)
        run_test_trade(bridge, "SELL")
        
    finally:
        log.info("Disconnecting from Windows bridge...")
        bridge.disconnect()

if __name__ == "__main__":
    main()
