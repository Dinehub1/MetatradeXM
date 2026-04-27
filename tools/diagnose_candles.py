#!/usr/bin/env python3
"""Check exactly what the bot's get_candles produces vs what we should get."""
import os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests

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
SYM_ENC = "GOLD.i%23"

# Simulate what the bot does: tf=M15, count=500
count = 500
tf_minutes = 15
minutes = tf_minutes * (count + 20)
start = datetime.now(timezone.utc) - timedelta(minutes=minutes)
start_str = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

print(f"Bot's calculation:")
print(f"  count={count}, tf=M15 (15 min)")
print(f"  minutes = 15 * 520 = {minutes} = {minutes/60:.0f} hours = {minutes/60/24:.1f} days back")
print(f"  startTime = {start_str}")
print(f"  now = {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')}")

# This is the URL the bot constructs
url = (f"https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
       f"/users/current/accounts/{ACCT}"
       f"/historical-market-data/symbols/{SYM_ENC}/timeframes/15m/candles"
       f"?startTime={start_str}&limit={count}")
print(f"\n  API URL: {url[:80]}...")

r = requests.get(url, headers={"auth-token": TOKEN}, timeout=30)
data = r.json() if r.ok else []
print(f"  Status: {r.status_code}")
print(f"  Candles returned: {len(data)}")
if data:
    print(f"  First: {data[0]['time']}  close={data[0]['close']}")
    print(f"  Last:  {data[-1]['time']}  close={data[-1]['close']}")

# Now try with a narrower window — last 2 days only
print(f"\n\nNarrower request: last 3 days, limit=500")
start2 = datetime.now(timezone.utc) - timedelta(days=3)
start2_str = start2.strftime("%Y-%m-%dT%H:%M:%S.000Z")
url2 = url.replace(start_str, start2_str)
r2 = requests.get(url2, headers={"auth-token": TOKEN}, timeout=30)
data2 = r2.json() if r2.ok else []
print(f"  startTime = {start2_str}")
print(f"  Candles returned: {len(data2)}")
if data2:
    print(f"  First: {data2[0]['time']}  close={data2[0]['close']}")
    print(f"  Last:  {data2[-1]['time']}  close={data2[-1]['close']}")

# Now try with just 1 day
print(f"\n\nEven narrower: last 1 day, limit=200")
start3 = datetime.now(timezone.utc) - timedelta(days=1)
start3_str = start3.strftime("%Y-%m-%dT%H:%M:%S.000Z")
url3 = (f"https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
        f"/users/current/accounts/{ACCT}"
        f"/historical-market-data/symbols/{SYM_ENC}/timeframes/15m/candles"
        f"?startTime={start3_str}&limit=200")
r3 = requests.get(url3, headers={"auth-token": TOKEN}, timeout=30)
data3 = r3.json() if r3.ok else []
print(f"  startTime = {start3_str}")
print(f"  Candles returned: {len(data3)}")
if data3:
    print(f"  First: {data3[0]['time']}  close={data3[0]['close']}")
    print(f"  Last:  {data3[-1]['time']}  close={data3[-1]['close']}")
    # Check the timedelta between last candle and now
    from dateutil.parser import parse as dtparse
    last_ts = dtparse(data3[-1]['time'])
    now = datetime.now(timezone.utc)
    delta = now - last_ts
    print(f"  Last candle age: {delta}")
    if delta > timedelta(hours=2):
        print(f"  🚨 Data is {delta.total_seconds()/3600:.1f} hours stale!")
    else:
        print(f"  ✅ Fresh data")
