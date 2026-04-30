"""
Fetch large historical candle history via the MetaApi SDK (auto region-resolving).
Saves a CSV file for the backtester.

Usage:
    venv/bin/python -m tools.fetch_history --symbol GOLD.i# --tf 15m --count 5000
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from metaapi_cloud_sdk import MetaApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Map UI labels → MT5 timeframe strings the SDK expects
TF_MAP = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
          "H1": "1h", "H4": "4h", "D1": "1d"}
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


async def fetch_history_async(symbol: str, tf_label: str, total: int) -> pd.DataFrame:
    token      = os.environ["METAAPI_TOKEN"]
    account_id = os.environ["METAAPI_ACCOUNT_ID"]
    tf = TF_MAP.get(tf_label, tf_label)  # accept "M15" or "15m"

    api = MetaApi(token)
    print(f"  Connecting to MetaApi account {account_id[:8]}…")
    account = await api.metatrader_account_api.get_account(account_id)

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        print("  Deploying account…")
        await account.deploy()
    print(f"  Account region: {account.region}, state: {account.state}")

    print(f"\n  Fetching up to {total} {tf_label} candles for {symbol}…")
    rows: list[dict] = []
    seen: set[str] = set()
    cursor: datetime | None = None  # None = "latest"
    empty_streak = 0

    while len(rows) < total:
        chunk = await account.get_historical_candles(symbol, tf, cursor, 1000)
        if not chunk:
            empty_streak += 1
            if empty_streak >= 3:
                print(f"  ⚠️  3 empty pages — stopping at {len(rows)} candles")
                break
            cursor = (cursor or datetime.now(timezone.utc)) - timedelta(minutes=TF_MIN[tf] * 1000)
            continue
        empty_streak = 0

        new = 0
        for c in chunk:
            t = c["time"]
            key = t.isoformat() if hasattr(t, "isoformat") else str(t)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "time": pd.to_datetime(t, utc=True),
                "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"],
                "vol": c.get("tickVolume", 0),
            })
            new += 1
        if new == 0:
            print(f"  ⚠️  No new candles — stopping at {len(rows)}")
            break

        oldest = min(pd.to_datetime(c["time"], utc=True) for c in chunk)
        cursor = oldest.to_pydatetime() - timedelta(minutes=TF_MIN[tf])
        print(f"    progress: {len(rows)}/{total}  (oldest={oldest})")
        await asyncio.sleep(0.25)

    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    if len(df) > total:
        df = df.tail(total).reset_index(drop=True)
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--tf", default="M15")
    p.add_argument("--count", type=int, default=5000)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    df = asyncio.run(fetch_history_async(args.symbol, args.tf, args.count))

    safe_sym = args.symbol.replace("#", "").replace(".", "_")
    out = args.out or str(ROOT / f"data/history_{safe_sym}_{args.tf}.csv")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\n  ✅ Saved {len(df)} candles → {out}")
    if len(df):
        print(f"     Range: {df['time'].iloc[0]}  →  {df['time'].iloc[-1]}")


if __name__ == "__main__":
    main()
