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
    rows = (
        SupabaseDB()
        .client.table("trade_outcomes")
        .select("ts,ticket,symbol,direction,entry_price,exit_price,profit_usd,pips_result,outcome")
        .eq("symbol", "UNKNOWN")
        .order("ts", desc=True)
        .limit(5)
        .execute()
        .data
        or []
    )
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
