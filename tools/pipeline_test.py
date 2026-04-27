"""Pipeline end-to-end test."""
import os, sys, asyncio, time, requests
sys.path.insert(0, ".")
for line in open(".env"):
    if "=" in line and not line.startswith("#"):
        k,v = line.strip().split("=",1); os.environ[k]=v

from bridges.metaapi_bridge import MetaApiBridge

async def run():
    # 1. MetaApi → broker
    bridge = MetaApiBridge(os.environ["METAAPI_TOKEN"], os.environ["METAAPI_ACCOUNT_ID"])
    bridge.connect()
    info = bridge.get_account_info()
    print(f"[1/4] MetaApi broker: OK | Acct #{info.login} | ${info.balance:.2f}")

    tick = bridge.get_tick("GOLD.i#")
    if tick:
        print(f"[2/4] Live GOLD tick: bid={tick.bid} ask={tick.ask}")
    else:
        print("[2/4] Live GOLD tick: MARKET CLOSED (no tick)")

    # 2. TradingView WS (expect down)
    from bridges.tv_client import get_tv_client
    tv = get_tv_client()
    time.sleep(2)
    tv_data = tv.get("XAUUSD")
    print(f"[3/4] TradingView WS: {'OK' if tv_data else 'DOWN (8887 — external server not running)'}")

    # 3. Gemini AI
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ['GEMINI_FLASH_MODEL']}:generateContent?key={os.environ['GEMINI_API_KEY']}",
        json={"contents":[{"parts":[{"text":"Reply with exactly: OK"}]}]},
        timeout=15)
    if r.status_code == 200:
        reply = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"[4/4] Gemini AI    : OK ({reply})")
    else:
        print(f"[4/4] Gemini AI    : FAIL {r.status_code}")

asyncio.run(run())
