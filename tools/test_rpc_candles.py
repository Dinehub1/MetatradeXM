#!/usr/bin/env python3
"""Test MetaApi RPC candle method vs REST API to see if we can get fresher data."""
import os, sys, asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

TOKEN = os.environ.get("METAAPI_TOKEN", "")
ACCT  = os.environ.get("METAAPI_ACCOUNT_ID", "")

async def main():
    from metaapi_cloud_sdk import MetaApi
    api = MetaApi(TOKEN)
    account = await api.metatrader_account_api.get_account(ACCT)
    await account.wait_connected()
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    print("✅ RPC connected\n")

    sym = "GOLD.i#"

    # Method 1: RPC get_candle (real-time, direct from broker)
    print("=== Method 1: RPC get_candle ===")
    try:
        # Check if there's a get_candles or similar RPC method
        methods = [m for m in dir(conn) if 'candle' in m.lower() or 'history' in m.lower() or 'chart' in m.lower()]
        print(f"  Available candle/history methods: {methods}")
    except Exception as e:
        print(f"  Error: {e}")

    # Method 2: Try streaming API / subscribe to candles
    print("\n=== Method 2: Account get_historical_candles ===")
    try:
        start = datetime.now(timezone.utc) - timedelta(hours=4)
        candles = await account.get_historical_candles(sym, "15m", start)
        print(f"  Got {len(candles) if candles else 0} candles via account method")
        if candles:
            print(f"  First: {candles[0].get('time')}  close={candles[0].get('close')}")
            print(f"  Last:  {candles[-1].get('time')}  close={candles[-1].get('close')}")
    except AttributeError:
        print("  Method not available")
    except Exception as e:
        print(f"  Error: {e}")

    # Method 3: Try market data streaming API
    print("\n=== Method 3: Market data API ===")
    try:
        market = api.market_data_client if hasattr(api, 'market_data_client') else None
        if market:
            candles = await market.get_candles(ACCT, sym, "15m", datetime.now(timezone.utc) - timedelta(hours=4))
            print(f"  Got {len(candles)} candles")
        else:
            print("  No market_data_client available")
    except Exception as e:
        print(f"  Error: {e}")

    # Method 4: Try symbol_price which gives real-time data
    print("\n=== Method 4: Current price (always fresh) ===")
    try:
        price = await conn.get_symbol_price(sym)
        print(f"  GOLD.i# Price: ask={price.get('ask')} bid={price.get('bid')}")
        print(f"  Time: {price.get('time', 'N/A')}")
    except Exception as e:
        print(f"  Error: {e}")

    await conn.close()

asyncio.run(main())
