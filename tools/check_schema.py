#!/usr/bin/env python3
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
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from core.supabase_db import SupabaseDB


def main():
    db = SupabaseDB()
    client = db.client
    tables = [
        "trade_entries",
        "trade_outcomes",
        "market_patterns",
        "learning_log",
        "filtered_trades",
        "live_market_snapshots",
        "live_account_snapshots",
        "live_positions",
        "live_events",
    ]
    for table in tables:
        resp = client.table(table).select("*", count="exact").limit(1).execute()
        sample = resp.data[0] if resp.data else {}
        print(f"{table}: count={resp.count} columns={sorted(sample.keys())}")


if __name__ == "__main__":
    main()
