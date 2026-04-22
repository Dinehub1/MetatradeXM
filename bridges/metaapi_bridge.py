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
from __future__ import annotations

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
        """Fetch historical candles — fresh data via RPC, REST API as fallback.

        Primary: account.get_historical_candles() — goes through the broker
                 connection, returns data that's typically <1 hour old.
        Fallback: REST historical API — can lag by 24-60+ hours.
        """
        try:
            tf  = _TF_MAP.get(timeframe, "15m")

            # ── Primary: RPC method (freshest available data) ─────────────
            try:
                df = self._run(self._async_get_candles_rpc(symbol, tf, count), timeout=30)
                if df is not None and len(df) >= 30:
                    self._check_freshness(df, symbol, timeframe)
                    return df
            except Exception as e:
                print(f"  ⚠️  RPC candles failed for {symbol}/{timeframe}: {e} — trying REST API")

            # ── Fallback: REST API with smart pagination ──────────────────
            return self._get_candles_rest(symbol, tf, count)
        except Exception as e:
            print(f"  ⚠️  get_candles error: {e}")
            return None

    async def _async_get_candles_rpc(self, symbol: str, tf: str, count: int) -> pd.DataFrame | None:
        """Fetch candles via the account's direct broker connection (freshest).

        Uses a narrow recent window to ensure we get the LATEST bars rather
        than old ones (the API returns forward from startTime, capped at 1000).
        """
        # Use narrow windows that guarantee we reach "now" within the 1000-bar cap
        recent_hours = {
            "1m": 4, "5m": 12, "15m": 48, "30m": 72,
            "1h": 168, "4h": 480, "1d": 2400,
        }
        hours = recent_hours.get(tf, 48)
        start = datetime.now(timezone.utc) - timedelta(hours=hours)

        candles = await self._account.get_historical_candles(symbol, tf, start)
        if not candles:
            return None

        rows = [{"time": pd.to_datetime(c["time"]) if isinstance(c["time"], str) else c["time"],
                 "o": c["open"], "h": c["high"],
                 "l": c["low"],  "c": c["close"],
                 "vol": c.get("tickVolume", 0)} for c in candles]
        df = pd.DataFrame(rows)
        # Take the last `count` bars
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)
        return df

    def _get_candles_rest(self, symbol: str, tf: str, count: int) -> pd.DataFrame | None:
        """REST API fallback with two-pass pagination."""
        tf_minutes = {"1m":1,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}
        bar_mins = tf_minutes.get(tf, 15)

        sym_enc = quote(symbol, safe="")
        base_url = (f"https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
                    f"/users/current/accounts/{self.account_id}"
                    f"/historical-market-data/symbols/{sym_enc}/timeframes/{tf}/candles")

        def _fetch(start_dt, limit):
            s = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            url = f"{base_url}?startTime={s}&limit={limit}"
            r = requests.get(url, headers={"auth-token": self.token}, timeout=30)
            if not r.ok:
                print(f"  ⚠️  Candles API error {r.status_code}: {r.text[:100]}")
                return []
            return r.json() or []

        # Pass 1: recent window
        recent_window = {"1m": 60*4, "5m": 60*12, "15m": 60*48, "30m": 60*72,
                         "1h": 60*168, "4h": 60*480, "1d": 60*2400}
        window = recent_window.get(tf, 60*48)
        start_recent = datetime.now(timezone.utc) - timedelta(minutes=window)
        recent_data = _fetch(start_recent, min(count, 500))

        # Pass 2: older bars if needed
        all_data = []
        if len(recent_data) < count:
            older_needed = count - len(recent_data)
            start_older = start_recent - timedelta(minutes=bar_mins * (older_needed + 50))
            older_data = _fetch(start_older, min(older_needed, 500))
            if older_data and recent_data:
                oldest_recent = recent_data[0].get("time", "")
                older_data = [c for c in older_data if c.get("time", "") < oldest_recent]
            all_data = older_data + recent_data
        else:
            all_data = recent_data[-count:]

        if not all_data:
            return None

        rows = [{"time": pd.to_datetime(c["time"]),
                 "o": c["open"], "h": c["high"],
                 "l": c["low"],  "c": c["close"],
                 "vol": c.get("tickVolume", 0)} for c in all_data]
        df = pd.DataFrame(rows)
        self._check_freshness(df, symbol, tf)
        return df

    def _check_freshness(self, df: pd.DataFrame, symbol: str, tf: str):
        """Log a warning if candle data is more than 2 hours old."""
        if len(df) == 0:
            return
        last_candle_time = df.iloc[-1]["time"]
        if last_candle_time.tzinfo is None:
            last_candle_time = last_candle_time.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_candle_time
        age_hours = age.total_seconds() / 3600
        if age_hours > 2:
            print(f"  ⚠️  {symbol}/{tf}: candle data is {age_hours:.1f}h old! "
                  f"Last bar: {df.iloc[-1]['time']}  close={df.iloc[-1]['c']}")

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

    def close_position(self, ticket, volume=None):
        """Close a position. If volume is specified, partially close that amount."""
        try:
            if volume is not None:
                self._run(self._conn.close_position(str(ticket), {"volume": volume}))
            else:
                self._run(self._conn.close_position(str(ticket)))
            return True
        except Exception as e:
            print(f"  ❌  Close error: {e}")
            return False

    def close_position_partial(self, ticket, volume):
        """Partially close a position by closing the specified volume."""
        return self.close_position(ticket, volume=volume)

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
        try:
            result = await self._conn.modify_position(str(ticket), opts)
            # MetaApi result can be dict, None, or raise internally
            if result is None:
                return True   # no exception = success
            if isinstance(result, dict):
                return result.get("id") is not None or "error" not in result
            return True
        except KeyError as e:
            # MetaApi SDK sometimes throws KeyError('value') on success responses.
            # Log at WARNING so we can detect if this is masking a real failure.
            import logging as _lg
            _lg.getLogger(__name__).warning(
                f"modify_position KeyError for ticket {ticket}: {e} — "
                "treating as failure; verify position in broker terminal"
            )
            return False
        except Exception:
            raise
