#!/usr/bin/env python3
"""Legacy filename kept for compatibility. Tests the current Windows bridge instead."""
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
        print("Bridge connection test FAILED")
        raise SystemExit(1)

    account = bridge.get_account_info()
    print(f"Bridge connection OK: account={getattr(account, 'login', '?')} balance={getattr(account, 'balance', 0)}")
    for symbol in ["GOLD.i#", "SILVER.i#"]:
        tick = bridge.get_tick(symbol)
        print(f"{symbol}: bid={getattr(tick, 'bid', 0)} ask={getattr(tick, 'ask', 0)}")
    bridge.disconnect()


if __name__ == "__main__":
    main()
