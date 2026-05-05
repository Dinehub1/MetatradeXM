import sys
import logging
from pprint import pprint

# setup logging
logging.basicConfig(level=logging.INFO)

# Load bridge
from bridges.webhook_bridge import make_bridge
bridge = make_bridge()

tick = bridge.get_tick("XAUUSD")
print(f"Tick: {tick}")

tf_data = {}
for tf in ["M15", "H1", "H4", "D1"]:
    df = bridge.get_candles("XAUUSD", tf, 200)
    tf_data[tf] = df
    print(f"Got {tf} candles: {len(df) if df is not None else 0}")

from core.analyzer import MarketAnalyzer
analyzer = MarketAnalyzer(use_ai=False)

res = analyzer.analyze(tf_data, tick, "XAUUSD")
print("\n--- ANALYSIS RESULT ---")
pprint(res)
