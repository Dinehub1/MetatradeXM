#!/usr/bin/env python3
"""
migrate_to_supabase.py — One-time migration from SQLite to Supabase

This script reads your local trade_memory.db SQLite database and transfers
all data to Supabase PostgreSQL. Run this once to migrate your trade history.

Usage:
    python3 migrate_to_supabase.py
"""

import sys
import json
import sqlite3
import os
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.logger_factory import get_logger
from core.supabase_db import SupabaseDB
from core.paths import DATA_DIR

log = get_logger("migrate_to_supabase")


def migrate_trade_outcomes(db_path: str, supabase_client):
    """Migrate trade_outcomes table."""
    log.info("Migrating trade_outcomes...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trade_outcomes").fetchall()

    records = []
    for row in rows:
        record = {
            "ts": row["ts"],
            "ticket": row["ticket"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "entry_price": row["entry_price"],
            "exit_price": row["exit_price"],
            "pips_result": row["pips_result"],
            "confidence": row["confidence"],
            "factors_json": json.loads(row["factors_json"] or "{}"),
            "conditions_json": json.loads(row["conditions_json"] or "{}"),
            "duration_min": row["duration_min"],
            "outcome": row["outcome"],
            "skills_used": json.loads(row["skills_used"] or "[]"),
        }
        records.append(record)

    if records:
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            supabase_client.client.table("trade_outcomes").insert(batch).execute()
            log.info(f"  Inserted {min(batch_size, len(records) - i)} records")

    log.info(f"✅ Migrated {len(records)} trade_outcomes")


def migrate_trade_entries(db_path: str, supabase_client):
    """Migrate trade_entries table."""
    log.info("Migrating trade_entries...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trade_entries").fetchall()

    records = []
    for row in rows:
        record = {
            "ts": row["ts"],
            "ticket": row["ticket"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "entry_price": row["entry_price"],
            "confidence": row["confidence"],
            "factors_json": json.loads(row["factors_json"] or "{}"),
            "conditions_json": json.loads(row["conditions_json"] or "{}"),
            "skills_used": json.loads(row["skills_used"] or "[]"),
            "closed": row["closed"],
        }
        records.append(record)

    if records:
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            supabase_client.client.table("trade_entries").insert(batch).execute()
            log.info(f"  Inserted {min(batch_size, len(records) - i)} records")

    log.info(f"✅ Migrated {len(records)} trade_entries")


def migrate_market_patterns(db_path: str, supabase_client):
    """Migrate market_patterns table."""
    log.info("Migrating market_patterns...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM market_patterns").fetchall()

    records = []
    for row in rows:
        record = {
            "symbol": row["symbol"],
            "hour_utc": row["hour_utc"],
            "day_of_week": row["day_of_week"],
            "session": row["session"],
            "direction": row["direction"],
            "outcome": row["outcome"],
            "pips": row["pips"],
            "ts": row["ts"],
        }
        records.append(record)

    if records:
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            supabase_client.client.table("market_patterns").insert(batch).execute()
            log.info(f"  Inserted {min(batch_size, len(records) - i)} records")

    log.info(f"✅ Migrated {len(records)} market_patterns")


def migrate_learning_log(db_path: str, supabase_client):
    """Migrate learning_log table."""
    log.info("Migrating learning_log...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM learning_log").fetchall()

    records = []
    for row in rows:
        record = {
            "ts": row["ts"],
            "insight_type": row["insight_type"],
            "insight_text": row["insight_text"],
            "data_json": json.loads(row["data_json"] or "{}"),
            "applied": row["applied"],
        }
        records.append(record)

    if records:
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            supabase_client.client.table("learning_log").insert(batch).execute()
            log.info(f"  Inserted {min(batch_size, len(records) - i)} records")

    log.info(f"✅ Migrated {len(records)} learning_log entries")


def migrate_filtered_trades(db_path: str, supabase_client):
    """Migrate filtered_trades table."""
    log.info("Migrating filtered_trades...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM filtered_trades").fetchall()

    records = []
    for row in rows:
        record = {
            "ts": row["ts"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "confidence": row["confidence"],
            "filter_reasons": json.loads(row["filter_reasons"] or "[]"),
            "factors_json": json.loads(row["factors_json"] or "{}"),
        }
        records.append(record)

    if records:
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            supabase_client.client.table("filtered_trades").insert(batch).execute()
            log.info(f"  Inserted {min(batch_size, len(records) - i)} records")

    log.info(f"✅ Migrated {len(records)} filtered_trades")


def main():
    """Run the migration."""
    # Load environment
    from pathlib import Path
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

    db_path = DATA_DIR / "trade_memory.db"

    if not db_path.exists():
        log.error(f"SQLite database not found: {db_path}")
        sys.exit(1)

    log.info(f"Starting migration from {db_path} to Supabase...")

    try:
        supabase = SupabaseDB()
    except ValueError as e:
        log.error(f"Failed to connect to Supabase: {e}")
        log.error("Make sure SUPABASE_URL and SUPABASE_ANON_KEY are set in .env")
        sys.exit(1)

    try:
        migrate_trade_outcomes(str(db_path), supabase)
        migrate_trade_entries(str(db_path), supabase)
        migrate_market_patterns(str(db_path), supabase)
        migrate_learning_log(str(db_path), supabase)
        migrate_filtered_trades(str(db_path), supabase)

        log.info("\n✅ Migration complete! Your data is now in Supabase.")
        log.info("Next steps:")
        log.info("1. Update your code to use Supabase (already done in memory.py)")
        log.info("2. Delete or backup your local trade_memory.db")
        log.info("3. Restart your trading bot")

    except Exception as e:
        log.error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
