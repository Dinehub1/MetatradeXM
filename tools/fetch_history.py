#!/usr/bin/env python3
"""Fetch historical candles through the current Windows bridge and save to CSV."""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--tf", default="M15")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    bridge = make_bridge()
    if not connect_with_retry(bridge, max_attempts=2):
        raise SystemExit("Bridge connection failed")

    df = bridge.get_candles(args.symbol, args.tf, args.count)
    if df is None or len(df) == 0:
        bridge.disconnect()
        raise SystemExit("No candle data returned")

    safe_sym = args.symbol.replace("#", "").replace(".", "_")
    out = Path(args.out) if args.out else ROOT / f"data/history_{safe_sym}_{args.tf}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} candles -> {out}")
    print(f"Range: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    bridge.disconnect()


if __name__ == "__main__":
    main()
