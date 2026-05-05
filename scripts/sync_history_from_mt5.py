#!/usr/bin/env python3
"""
sync_history_from_mt5.py — Pull ALL trade history from MetaTrader webhook → Supabase

Fetches every closed deal from the MT5 webhook server, pairs entry/exit deals
by position_id, computes pips & P&L, then upserts into Supabase.

Usage:
    python3 scripts/sync_history_from_mt5.py          # last 30 days
    python3 scripts/sync_history_from_mt5.py --days 90
    python3 scripts/sync_history_from_mt5.py --days 365
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import requests
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────

WEBHOOK_URL   = os.getenv("WIN_WEBHOOK_URL", "http://206.72.198.54:5001").rstrip("/")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_ANON_KEY")

# Symbol mapping: MT5 broker name → standard name
SYMBOL_MAP = {
    "GOLD.i#":   "XAUUSD",
    "SILVER.i#": "XAGUSD",
    "GOLD.i":    "XAUUSD",
    "SILVER.i":  "XAGUSD",
    "XAUUSD":    "XAUUSD",
    "XAGUSD":    "XAGUSD",
}

# Pip size per symbol (price change = 1 pip)
PIP_SIZE = {
    "XAUUSD": 0.01,   # Gold: 1 pip = $0.01
    "XAGUSD": 0.001,  # Silver: 1 pip = $0.001
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_history(days: int) -> list:
    """Fetch all deals from MT5 webhook."""
    print(f"📡 Fetching {days}-day history from {WEBHOOK_URL}...")
    resp = requests.get(
        f"{WEBHOOK_URL}/history",
        params={"days": days},
        headers={"Authorization": f"Bearer {WEBHOOK_TOKEN}"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    deals = data.get("deals", [])
    print(f"   ✓ Got {len(deals)} deals ({data.get('closed_trades', 0)} closed trades)")
    return deals


def parse_direction(deal_type: int) -> str:
    """MT5 type: 0=BUY, 1=SELL."""
    return "BUY" if deal_type == 0 else "SELL"


def calc_pips(symbol: str, direction: str, entry_price: float, exit_price: float) -> float:
    """Calculate pips: positive = profit, negative = loss."""
    pip = PIP_SIZE.get(symbol, 0.01)
    if direction == "BUY":
        return round((exit_price - entry_price) / pip, 1)
    else:
        return round((entry_price - exit_price) / pip, 1)


def ts_to_iso(unix_ts: int) -> str:
    """Convert Unix timestamp to ISO string."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def pair_deals(deals: list) -> tuple[list, list]:
    """
    Pair entry deals (entry=0) with exit deals (entry=1) by position_id.

    Returns:
        (entries_to_insert, outcomes_to_insert)
    """
    opens  = {d["position_id"]: d for d in deals if d.get("entry") == 0}
    closes = [d for d in deals if d.get("entry") == 1]

    entries  = []
    outcomes = []
    unmatched = 0

    for close_deal in closes:
        pos_id = close_deal["position_id"]
        raw_symbol = close_deal.get("symbol", "")
        symbol = SYMBOL_MAP.get(raw_symbol, raw_symbol)
        ticket = str(close_deal["ticket"])
        exit_price = close_deal["price"]
        profit = close_deal.get("profit", 0.0)
        commission = close_deal.get("commission", 0.0)
        swap = close_deal.get("swap", 0.0)
        net_profit = profit + commission + swap
        close_ts = ts_to_iso(close_deal["time"])

        # Try to find the matching open deal
        open_deal = opens.get(pos_id)
        if open_deal:
            direction = parse_direction(open_deal["type"])
            entry_price = open_deal["price"]
            open_ts = ts_to_iso(open_deal["time"])

            # Calculate duration
            open_dt  = datetime.fromtimestamp(open_deal["time"], tz=timezone.utc)
            close_dt = datetime.fromtimestamp(close_deal["time"], tz=timezone.utc)
            duration_min = round((close_dt - open_dt).total_seconds() / 60, 1)

            # Calculate pips
            pips = calc_pips(symbol, direction, entry_price, exit_price)

            # Determine outcome
            outcome = "WIN" if net_profit > 0 else ("LOSS" if net_profit < 0 else "BREAK_EVEN")

            # Extract confidence from comment (e.g. "CT-SELL-c72%")
            comment = open_deal.get("comment", "")
            confidence = 0.0
            if "-c" in comment:
                try:
                    conf_str = comment.split("-c")[-1].replace("%", "")
                    confidence = float(conf_str) / 100.0
                except Exception:
                    confidence = 0.0

            # Build entry record
            entries.append({
                "ts":             open_ts,
                "ticket":         str(pos_id),
                "symbol":         symbol,
                "direction":      direction,
                "entry_price":    entry_price,
                "confidence":     confidence,
                "factors_json":   {},
                "conditions_json": {"comment": comment},
                "skills_used":    [],
                "closed":         1,
            })

            # Build outcome record
            outcomes.append({
                "ts":              close_ts,
                "ticket":          str(pos_id),
                "symbol":          symbol,
                "direction":       direction,
                "entry_price":     entry_price,
                "exit_price":      exit_price,
                "pips_result":     pips,
                "confidence":      confidence,
                "factors_json":    {},
                "conditions_json": {"comment": comment, "profit": net_profit},
                "duration_min":    duration_min,
                "outcome":         outcome,
                "skills_used":     [],
                "event_type":      "PRIMARY",
            })
        else:
            # No matching entry — insert as GHOST outcome
            direction = parse_direction(close_deal["type"])
            pips = 0.0
            outcome = "WIN" if profit > 0 else ("LOSS" if profit < 0 else "BREAK_EVEN")

            outcomes.append({
                "ts":              close_ts,
                "ticket":          ticket,
                "symbol":          symbol,
                "direction":       direction,
                "entry_price":     0.0,
                "exit_price":      exit_price,
                "pips_result":     pips,
                "confidence":      0.0,
                "factors_json":    {},
                "conditions_json": {"comment": close_deal.get("comment", "")},
                "duration_min":    0.0,
                "outcome":         outcome,
                "skills_used":     [],
                "event_type":      "GHOST",
            })
            unmatched += 1

    print(f"   ✓ Paired {len(outcomes) - unmatched} trades  |  {unmatched} ghost (no entry found)")
    return entries, outcomes


def get_existing_tickets(client, table: str) -> set:
    """Fetch all ticket values already in Supabase to avoid duplicates."""
    existing = set()
    page = 0
    page_size = 1000
    while True:
        start = page * page_size
        end   = start + page_size - 1
        resp = client.table(table).select("ticket").range(start, end).execute()
        for row in resp.data:
            if row.get("ticket"):
                existing.add(str(row["ticket"]))
        fetched = len(resp.data)
        if fetched < page_size:
            break
        page += 1
    return existing


def upsert_batch(client, table: str, records: list, existing_tickets: set,
                 label: str) -> int:
    """Insert records not already in Supabase. Returns count inserted."""
    new_records = [r for r in records if str(r["ticket"]) not in existing_tickets]

    if not new_records:
        print(f"   ⏭  {label}: all {len(records)} records already in Supabase")
        return 0

    # Probe which columns actually exist on first batch
    probe = new_records[:1]
    try:
        client.table(table).insert(probe).execute()
        inserted = 1
        start = 1
    except Exception as e:
        err = str(e)
        # Drop unknown columns and retry
        if "column" in err.lower() and "schema cache" in err.lower():
            import re
            bad_col = re.search(r"find the '(.+?)' column", err)
            if bad_col:
                drop = bad_col.group(1)
                print(f"   ⚠  Dropping unknown column '{drop}' from {label}")
                new_records = [{k: v for k, v in r.items() if k != drop} for r in new_records]
                client.table(table).insert(new_records[:1]).execute()
                inserted = 1
                start = 1
            else:
                raise
        else:
            raise

    batch_size = 200
    for i in range(start, len(new_records), batch_size):
        batch = new_records[i : i + batch_size]
        client.table(table).insert(batch).execute()
        inserted += len(batch)
        print(f"   ✓ {label}: inserted {inserted}/{len(new_records)}...")

    if inserted == len(new_records):
        print(f"   ✓ {label}: inserted {inserted}/{len(new_records)}...")

    return inserted


# ── Market patterns builder ───────────────────────────────────────────────────

def build_market_patterns(outcomes: list) -> list:
    """Derive market_patterns rows from matched outcomes."""
    patterns = []
    for o in outcomes:
        if o["event_type"] != "PRIMARY":
            continue
        try:
            dt = datetime.fromisoformat(o["ts"])
        except Exception:
            continue

        hour = dt.hour
        if   7  <= hour < 13: session = "LONDON"
        elif 13 <= hour < 16: session = "LONDON_NY_OVERLAP"
        elif 16 <= hour < 22: session = "NEW_YORK"
        else:                 session = "ASIAN"

        patterns.append({
            "symbol":      o["symbol"],
            "hour_utc":    hour,
            "day_of_week": dt.weekday(),
            "session":     session,
            "direction":   o["direction"],
            "outcome":     o["outcome"],
            "pips":        o["pips_result"],
            "ts":          o["ts"],
        })
    return patterns


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync MT5 trade history to Supabase")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of history to sync (default: 30)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL / SUPABASE_ANON_KEY not set in .env")
        sys.exit(1)

    print(f"\n🚀 MetatradeXM → Supabase Full History Sync")
    print(f"   Webhook : {WEBHOOK_URL}")
    print(f"   Supabase: {SUPABASE_URL}")
    print(f"   Period  : last {args.days} days\n")

    # ── 1. Fetch from MetaTrader ──
    deals = fetch_history(args.days)

    # ── 2. Pair entry/exit ──
    print("\n📊 Pairing entry/exit deals...")
    entries, outcomes = pair_deals(deals)
    patterns = build_market_patterns(outcomes)

    # Quick stats
    wins   = sum(1 for o in outcomes if o["outcome"] == "WIN")
    losses = sum(1 for o in outcomes if o["outcome"] == "LOSS")
    total_pips = sum(o["pips_result"] for o in outcomes if o["event_type"] == "PRIMARY")
    print(f"   Total trades : {len(outcomes)}")
    print(f"   Wins/Losses  : {wins}W / {losses}L")
    print(f"   Total pips   : {total_pips:+.1f}")
    print(f"   Patterns     : {len(patterns)}")

    # ── 3. Connect Supabase ──
    print("\n🔗 Connecting to Supabase...")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ── 4. Get existing tickets (deduplication) ──
    print("🔍 Checking for existing records...")
    existing_entries  = get_existing_tickets(client, "trade_entries")
    existing_outcomes = get_existing_tickets(client, "trade_outcomes")
    print(f"   Already in Supabase: {len(existing_entries)} entries, {len(existing_outcomes)} outcomes")

    # ── 5. Upsert everything ──
    print("\n📤 Uploading to Supabase...")
    n_entries  = upsert_batch(client, "trade_entries",  entries,  existing_entries,  "trade_entries")
    n_outcomes = upsert_batch(client, "trade_outcomes", outcomes, existing_outcomes, "trade_outcomes")

    # Patterns: just insert all new ones (no dedup by ticket)
    print(f"   Inserting {len(patterns)} market patterns...")
    for i in range(0, len(patterns), 500):
        client.table("market_patterns").insert(patterns[i : i + 500]).execute()
    print(f"   ✓ market_patterns: {len(patterns)} inserted")

    # ── 6. Final counts ──
    print("\n✅ SYNC COMPLETE!\n")
    print("📊 Final Supabase counts:")
    for table in ["trade_entries", "trade_outcomes", "market_patterns"]:
        count = client.table(table).select("id", count="exact").execute().count
        print(f"   • {table}: {count} records")

    print(f"\n🎯 Inserted this run:")
    print(f"   • trade_entries  : {n_entries} new records")
    print(f"   • trade_outcomes : {n_outcomes} new records")
    print(f"   • market_patterns: {len(patterns)} new records")


if __name__ == "__main__":
    main()
