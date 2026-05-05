#!/usr/bin/env python3
"""Inspect current bridge candle freshness against latest tick prices."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

from continuous_trader import make_bridge, connect_with_retry


def main():
    bridge = make_bridge()
    if not connect_with_retry(bridge, max_attempts=2):
        raise SystemExit("Bridge connection failed")

    symbol = "GOLD.i#"
    tick = bridge.get_tick(symbol)
    candles = bridge.get_candles(symbol, "M15", 50)
    print("Bridge connected")
    print(f"Tick: bid={getattr(tick, 'bid', 0)} ask={getattr(tick, 'ask', 0)} time={getattr(tick, 'time', 0)}")
    if candles is not None and len(candles) > 0:
        last = candles.iloc[-1]
        print(f"Last M15 candle: time={last['time']} close={last['c']}")
    else:
        print("No candle data returned")
    bridge.disconnect()


if __name__ == "__main__":
    main()
