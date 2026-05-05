#!/usr/bin/env python3
"""Quick verification: compare Windows bridge ticks with Supabase live snapshots."""
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
from core.supabase_db import SupabaseDB


def main():
    bridge = make_bridge()
    if not connect_with_retry(bridge, max_attempts=2):
        raise SystemExit("FAILED to connect to Windows bridge")

    db = SupabaseDB()
    rows = {row["symbol"]: row for row in db.get_live_market_snapshots()}

    for broker_symbol, display_symbol in [("GOLD.i#", "XAUUSD"), ("SILVER.i#", "XAGUSD")]:
        tick = bridge.get_tick(broker_symbol)
        snap = rows.get(display_symbol, {})
        print(f"\n{'='*60}")
        print(f"  {display_symbol} / {broker_symbol}")
        print(f"{'='*60}")
        print(f"  BRIDGE TICK: bid={getattr(tick, 'bid', 0)} ask={getattr(tick, 'ask', 0)}")
        print(f"  SUPABASE  : price={snap.get('price')} updated_at={snap.get('updated_at')}")

    bridge.disconnect()


if __name__ == "__main__":
    main()
