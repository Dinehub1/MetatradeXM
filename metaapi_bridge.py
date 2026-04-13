"""
MetaApiBridge — connects to a real MT5/MT4 demo or live account via MetaApi.cloud
Works on Linux/Mac/Windows. Replaces the Windows-only MetaTrader5 package.

Setup:
  1. Go to https://app.metaapi.cloud  (free account)
  2. Connect your MT5 demo account under "Accounts"
  3. Copy your API token from the top-right profile menu
  4. Set environment variables or pass them directly:
       export METAAPI_TOKEN="your-token-here"
       export METAAPI_ACCOUNT_ID="your-account-id-here"
"""

import asyncio
import os
import threading
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from urllib.parse import quote

try:
    from metaapi_cloud_sdk import MetaApi
    HAS_METAAPI = True
except ImportError:
    HAS_METAAPI = False

# MetaApi timeframe strings
_TF_MAP = {
    "M1":  "1m",  "M5":  "5m",  "M15": "15m",
    "M30": "30m", "H1":  "1h",  "H4":  "4h",  "D1": "1d",
}


class MetaApiBridge:
    """
    Synchronous wrapper around the async MetaApi SDK.
    Drop-in replacement for MT5Bridge — same public method signatures.
    """

    def __init__(self, token: str = None, account_id: str = None):
        self.token      = token      or os.environ.get("METAAPI_TOKEN", "")
        self.account_id = account_id or os.environ.get("METAAPI_ACCOUNT_ID", "")
        self.connected  = False
        self._api       = None
        self._account   = None
        self._conn      = None

        # Dedicated event loop in a background thread
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro, timeout: int = 60):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not HAS_METAAPI:
            print("  ❌  metaapi-cloud-sdk not installed. Run: pip install metaapi-cloud-sdk")
            return False
        if not self.token or not self.account_id:
            print("  ❌  METAAPI_TOKEN or METAAPI_ACCOUNT_ID not set.")
            print("      Export them or edit bot_config.env")
            return False
        try:
            return self._run(self._async_connect(), timeout=120)
        except Exception as e:
            print(f"  ❌  MetaApi connect failed: {e}")
            return False

    async def _async_connect(self) -> bool:
        self._api     = MetaApi(self.token)
        self._account = await self._api.metatrader_account_api.get_account(self.account_id)

        state = self._account.state
        print(f"  Account state: {state}")
        if state not in ("DEPLOYING", "DEPLOYED"):
            print("  Deploying account...")
            await self._account.deploy()

        print("  Waiting for broker connection...")
        await self._account.wait_connected()

        self._conn = self._account.get_rpc_connection()
        await self._conn.connect()
        await self._conn.wait_synchronized()

        self.connected = True
        print("  ✅  MetaApi connected to MT5 account")
        return True

    def disconnect(self):
        if self._conn:
            try:
                self._run(self._conn.close(), timeout=10)
            except Exception:
                pass
        self.connected = False

    # ── Market data ───────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame | None:
        """Fetch historical candles via MetaApi REST API."""
        try:
            tf  = _TF_MAP.get(timeframe, "15m")
            # Calculate start time based on timeframe and count needed
            tf_minutes = {"1m":1,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}
            minutes    = tf_minutes.get(tf, 15) * (count + 20)
            start      = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            start_str  = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

            sym_enc = quote(symbol, safe="")   # encode # → %23 etc.
            url = (f"https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
                   f"/users/current/accounts/{self.account_id}"
                   f"/historical-market-data/symbols/{sym_enc}/timeframes/{tf}/candles"
                   f"?startTime={start_str}&limit={count}")

            r = requests.get(url, headers={"auth-token": self.token}, timeout=30)
            if not r.ok:
                print(f"  ⚠️  Candles API error {r.status_code}: {r.text[:100]}")
                return None

            data = r.json()
            if not data:
                return None

            rows = [{"time": pd.to_datetime(c["time"]),
                     "o": c["open"], "h": c["high"],
                     "l": c["low"],  "c": c["close"],
                     "vol": c.get("tickVolume", 0)} for c in data]
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"  ⚠️  get_candles error: {e}")
            return None

    def get_tick(self, symbol: str):
        try:
            return self._run(self._async_get_tick(symbol))
        except Exception as e:
            print(f"  ⚠️  get_tick error: {e}")
            return None

    async def _async_get_tick(self, symbol: str):
        price = await self._conn.get_symbol_price(symbol)
        return SimpleNamespace(
            ask=price["ask"],
            bid=price["bid"],
            last=price.get("last", price["ask"]),
            time=int(datetime.now(timezone.utc).timestamp()),
        )

    def get_symbol_info(self, symbol: str):
        try:
            return self._run(self._async_symbol_info(symbol))
        except Exception:
            return None

    async def _async_symbol_info(self, symbol: str):
        specs = await self._conn.get_symbol_specification(symbol)
        if not specs:
            return None
        is_jpy = "JPY" in symbol
        return SimpleNamespace(
            name=symbol,
            digits=specs.get("digits", 3 if is_jpy else 5),
            trade_contract_size=specs.get("contractSize", 100_000),
            volume_min=specs.get("minVolume", 0.01),
            volume_max=specs.get("maxVolume", 100.0),
            volume_step=specs.get("volumeStep", 0.01),
            point=10 ** -(specs.get("digits", 3 if is_jpy else 5)),
        )

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_info(self):
        try:
            return self._run(self._async_account_info())
        except Exception:
            return None

    async def _async_account_info(self):
        info = await self._conn.get_account_information()
        return SimpleNamespace(
            login=info.get("login", ""),
            server=info.get("server", ""),
            balance=info.get("balance", 0),
            equity=info.get("equity", 0),
            margin=info.get("margin", 0),
            margin_free=info.get("freeMargin", 0),
            leverage=info.get("leverage", 100),
            currency=info.get("currency", "USD"),
        )

    def print_account_info(self):
        info = self.get_account_info()
        if not info:
            print("  Could not fetch account info.")
            return
        print(f"  Account:  #{info.login}  ({info.server})")
        print(f"  Balance:  {info.balance:.2f} {info.currency}")
        print(f"  Equity:   {info.equity:.2f}")
        print(f"  Margin:   {info.margin:.2f}  |  Free: {info.margin_free:.2f}")
        print(f"  Leverage: 1:{info.leverage}")

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: str = None):
        try:
            return self._run(self._async_positions(symbol))
        except Exception:
            return []

    async def _async_positions(self, symbol: str = None):
        positions = await self._conn.get_positions()
        if not positions:
            return []
        result = []
        for p in positions:
            if symbol and p.get("symbol") != symbol:
                continue
            result.append(SimpleNamespace(
                ticket    = p.get("id", 0),
                symbol    = p.get("symbol", ""),
                type      = 0 if p.get("type") == "POSITION_TYPE_BUY" else 1,
                volume    = p.get("volume", 0),
                price_open= p.get("openPrice", 0),
                sl        = p.get("stopLoss", 0),
                tp        = p.get("takeProfit", 0),
                profit    = p.get("profit", 0),
                comment   = p.get("comment", ""),
            ))
        return result

    def print_open_positions(self):
        positions = self.get_open_positions()
        if not positions:
            print("\n  No open positions.")
            return
        print(f"\n  Open positions ({len(positions)}):")
        for p in positions:
            direction = "BUY" if p.type == 0 else "SELL"
            print(f"  #{p.ticket}  {p.symbol}  {direction}  {p.volume} lots  "
                  f"open@{p.price_open:.5f}  profit:{p.profit:.2f}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, params: dict):
        try:
            return self._run(self._async_place_order(params))
        except Exception as e:
            print(f"  ❌  Order error: {e}")
            return None

    async def _async_place_order(self, params: dict):
        symbol    = params["symbol"]
        direction = params["direction"]
        comment   = params.get("comment", "AI-Bot")
        sl        = params.get("sl")   # None = broker manages stops
        tp        = params.get("tp")

        # opts must NOT duplicate sl/tp — pass them only as positional args
        opts = {"comment": comment}

        if direction == "BUY":
            result = await self._conn.create_market_buy_order(
                symbol, params["lot"], sl, tp, opts)
        else:
            result = await self._conn.create_market_sell_order(
                symbol, params["lot"], sl, tp, opts)

        if result and result.get("orderId"):
            return SimpleNamespace(order=result["orderId"], retcode=10009)
        print(f"  ⚠️  Unexpected order result: {result}")
        return None

    def close_position(self, ticket):
        try:
            self._run(self._conn.close_position(str(ticket)))
            return True
        except Exception as e:
            print(f"  ❌  Close error: {e}")
            return False

    def modify_position(self, ticket, sl=None, tp=None) -> bool:
        """Modify SL/TP of an open position (used for trailing stop / breakeven)."""
        try:
            return self._run(self._async_modify_position(ticket, sl, tp))
        except Exception as e:
            print(f"  ⚠️  Modify position error: {e}")
            return False

    async def _async_modify_position(self, ticket, sl, tp) -> bool:
        opts = {}
        if sl is not None:
            opts["stopLoss"] = sl
        if tp is not None:
            opts["takeProfit"] = tp
        result = await self._conn.modify_position(str(ticket), opts)
        return bool(result)
