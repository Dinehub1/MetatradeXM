#!/usr/bin/env python3
"""Current end-to-end health check for Windows bridge + NVIDIA + Supabase."""
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
        raise SystemExit("Bridge connection failed")

    account = bridge.get_account_info()
    print(f"[1/4] Broker bridge: OK | Acct #{getattr(account, 'login', '?')} | ${getattr(account, 'balance', 0):.2f}")

    tick = bridge.get_tick("GOLD.i#")
    if tick:
        print(f"[2/4] Live GOLD tick: bid={getattr(tick, 'bid', 0)} ask={getattr(tick, 'ask', 0)}")
    else:
        print("[2/4] Live GOLD tick: unavailable")

    db = SupabaseDB()
    live = db.client.table("live_market_snapshots").select("symbol,price,updated_at").limit(5).execute().data or []
    print(f"[3/4] Supabase live snapshots: OK | rows={len(live)}")

    if os.getenv("NVIDIA_API_KEY"):
        print("[4/4] NVIDIA config: present")
    else:
        print("[4/4] NVIDIA config: missing")

    bridge.disconnect()


if __name__ == "__main__":
    main()
