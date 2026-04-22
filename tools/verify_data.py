#!/usr/bin/env python3
"""Quick verification: compare MetaApi candle data vs live tick price."""
import os, sys
from pathlib import Path

ROOT = Path(__file__).parent
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

from bridges.metaapi_bridge import MetaApiBridge

bridge = MetaApiBridge()
print("Connecting to MetaApi...")
if not bridge.connect():
    print("FAILED to connect")
    sys.exit(1)

for sym in ["GOLD.i#", "SILVER.i#"]:
    print(f"\n{'='*60}")
    print(f"  {sym}")
    print(f"{'='*60}")

    # Live tick
    tick = bridge.get_tick(sym)
    if tick:
        print(f"  LIVE TICK: ask={tick.ask:.5f}  bid={tick.bid:.5f}")
    else:
        print(f"  LIVE TICK: FAILED")

    # M15 candles — last 5
    df = bridge.get_candles(sym, "M15", 500)
    if df is not None and len(df) > 0:
        print(f"  CANDLES: {len(df)} bars loaded")
        print(f"  FIRST candle: {df.iloc[0]['time']}  close={df.iloc[0]['c']}")
        print(f"  LAST  candle: {df.iloc[-1]['time']}  close={df.iloc[-1]['c']}")
        print(f"\n  Last 5 candles:")
        for _, row in df.tail(5).iterrows():
            print(f"    {row['time']}  O={row['o']:.2f}  H={row['h']:.2f}  L={row['l']:.2f}  C={row['c']:.2f}  V={row['vol']}")

        gap = abs(tick.ask - df.iloc[-1]['c']) if tick else 0
        print(f"\n  ⚡ GAP: tick={tick.ask:.2f} vs last_candle_close={df.iloc[-1]['c']:.2f} = ${gap:.2f} difference")
        if gap > 10:
            print(f"  🚨 STALE DATA! Gap is ${gap:.2f} — candle data is outdated!")
        else:
            print(f"  ✅ Data looks fresh (gap < $10)")
    else:
        print(f"  CANDLES: FAILED or empty")

bridge.disconnect()
