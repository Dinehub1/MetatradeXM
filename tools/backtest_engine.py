from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

import pandas as pd

from continuous_trader import (
    CONFIG as LIVE_CONFIG,
    SESSION_CONFIG,
    SYMBOLS,
    build_order_params,
    connect_with_retry,
    make_bridge,
)
from core.analyzer import MarketAnalyzer
from core.paths import DATA_DIR, LOG_DIR
from risk.pyramid_manager import PYRAMID_CFG, PyramidSession

log = logging.getLogger("backtest")

BACKTEST_DIR = DATA_DIR / "backtests"
CACHE_DIR = BACKTEST_DIR / "cache"
REPORT_DIR = BACKTEST_DIR / "reports"
TRADE_LOG_DIR = BACKTEST_DIR / "trade_logs"
for _dir in (BACKTEST_DIR, CACHE_DIR, REPORT_DIR, TRADE_LOG_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


@dataclass
class ExecutionCosts:
    spread: float
    slippage: float
    commission_per_lot_round_turn: float = 0.0


@dataclass
class SymbolConfig:
    broker: str
    display: str
    pip: float
    contract_size: float
    sl_pips: int
    tp_pips: int
    lot: float
    costs: ExecutionCosts


@dataclass
class StrategyConfig:
    name: str
    buy_threshold: float = 12
    sell_threshold: float = -12
    ranging_penalty: float = 0.80
    use_pyramid: bool = True
    base_timeframe: str = "M1"
    analysis_timeframe: str = "M15"
    starting_balance: float = 10_000.0
    min_warmup_bars: int = 250
    months: int = 6
    bar_count: Optional[int] = None
    export_prefix: str = "backtest"


@dataclass
class Position:
    ticket: str
    pyramid_id: str
    symbol: str
    broker_symbol: str
    direction: str
    volume: float
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp: float
    confidence: float
    score: float
    comment: str
    tranche_num: int
    session: str
    regime: str
    last_mark_price: float = 0.0
    peak_profit_usd: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    realized_pnl_usd: float = 0.0
    realized_pnl_pips: float = 0.0
    commission_usd: float = 0.0
    confidence_band: str = ""


@dataclass
class BacktestResult:
    config: StrategyConfig
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: Dict[str, float | str | int]
    monthly: pd.DataFrame
    report_path: Path
    trade_log_path: Path
    equity_png_path: Path


class HistoricalMarketAnalyzer(MarketAnalyzer):
    def __init__(self, backtest_ts: pd.Timestamp):
        super().__init__(use_claude=False)
        self.backtest_ts = backtest_ts

    def set_backtest_ts(self, backtest_ts: pd.Timestamp) -> None:
        self.backtest_ts = backtest_ts

    def _get_session(self) -> str:
        now = self.backtest_ts.tz_convert("UTC") if self.backtest_ts.tzinfo else self.backtest_ts.tz_localize("UTC")
        weekday = now.weekday()
        hour = now.hour
        if weekday == 5 or (weekday == 4 and hour >= 22) or (weekday == 6 and hour < 22):
            return "MARKET_CLOSED"
        if 8 <= hour < 13:
            return "LONDON"
        if 13 <= hour < 17:
            return "LONDON_NY_OVERLAP"
        if 17 <= hour < 22:
            return "NEW_YORK"
        return "ASIAN"

    def _load_weights(self) -> dict:
        weights = super()._load_weights()
        weights.update(getattr(self, "override_weights", {}))
        return weights


class HistoricalDataStore:
    def __init__(self, bridge, symbol_map: Dict[str, SymbolConfig]):
        self.bridge = bridge
        self.symbol_map = symbol_map

    def fetch_last_months(self, symbol: SymbolConfig, timeframe: str, months: int = 6) -> pd.DataFrame:
        end = pd.Timestamp.now("UTC")
        start = end - pd.DateOffset(months=months)
        cache_name = f"{symbol.display}_{timeframe}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv.gz"
        cache_path = CACHE_DIR / cache_name
        if cache_path.exists():
            df = pd.read_csv(cache_path, parse_dates=["time"])
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.sort_values("time").reset_index(drop=True)
            self._validate_history_span(df, symbol, timeframe, start, end)
            return df

        df = self._fetch_range(symbol.broker, timeframe, start, end)
        if df is None or df.empty:
            raise RuntimeError(f"No historical data returned for {symbol.display}/{timeframe}")
        df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
        self._validate_history_span(df, symbol, timeframe, start, end)
        df.to_csv(cache_path, index=False, compression="gzip")
        return df

    def fetch_recent_count(self, symbol: SymbolConfig, timeframe: str, count: int) -> pd.DataFrame:
        cache_name = f"{symbol.display}_{timeframe}_latest_{count}.csv.gz"
        cache_path = CACHE_DIR / cache_name
        if cache_path.exists():
            df = pd.read_csv(cache_path, parse_dates=["time"])
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

        df = self.bridge.get_candles(symbol.broker, timeframe, count)
        if df is None or df.empty:
            raise RuntimeError(f"No historical data returned for {symbol.display}/{timeframe} count={count}")
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
        df.to_csv(cache_path, index=False, compression="gzip")
        return df

    def _validate_history_span(
        self,
        df: pd.DataFrame,
        symbol: SymbolConfig,
        timeframe: str,
        requested_start: pd.Timestamp,
        requested_end: pd.Timestamp,
    ) -> None:
        actual_start = pd.to_datetime(df["time"].iloc[0], utc=True)
        actual_end = pd.to_datetime(df["time"].iloc[-1], utc=True)
        actual_span = actual_end - actual_start
        requested_span = requested_end - requested_start
        minimum_ok_span = requested_span * 0.75
        if actual_span < minimum_ok_span:
            raise RuntimeError(
                f"Insufficient {symbol.display}/{timeframe} history: got {actual_start} → {actual_end} "
                f"({actual_span.days} days) but requested roughly {requested_span.days} days. "
                "Your current webhook endpoint only serves the latest candles and appears capped, "
                "so this backtest cannot represent the last 6 months yet."
            )

    def _fetch_range(self, broker_symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        try:
            from bridges import mt5_bridge as mt5_bridge_module

            if self.bridge.__class__.__name__ == "MT5Bridge" and hasattr(mt5_bridge_module, "mt5"):
                mt5 = mt5_bridge_module.mt5
                tf = mt5_bridge_module.TIMEFRAME_MAP.get(timeframe)
                if tf is None:
                    raise ValueError(f"Unsupported timeframe: {timeframe}")
                rates = mt5.copy_rates_range(
                    broker_symbol,
                    tf,
                    start.to_pydatetime(),
                    end.to_pydatetime(),
                )
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    return df.rename(
                        columns={"open": "o", "high": "h", "low": "l", "close": "c", "tick_volume": "vol"}
                    )[["time", "o", "h", "l", "c", "vol"]]
        except Exception as exc:
            log.warning("Direct MT5 range fetch failed for %s/%s: %s", broker_symbol, timeframe, exc)

        minutes_per_bar = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}[timeframe]
        total_minutes = max(int((end - start).total_seconds() / 60), 1)
        count = max(int(total_minutes / minutes_per_bar) + 500, 200)
        log.info("Falling back to bridge.get_candles(%s, %s, count=%s)", broker_symbol, timeframe, count)
        df = self.bridge.get_candles(broker_symbol, timeframe, count)
        if df is None or df.empty:
            raise RuntimeError(f"Bridge fallback failed for {broker_symbol}/{timeframe}")
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)


class BacktestEngine:
    def __init__(self, strategy_config: StrategyConfig):
        self.config = strategy_config
        self.bridge = make_bridge()
        self.symbols = self._build_symbol_map()
        self.data_store = HistoricalDataStore(self.bridge, self.symbols)
        self.trade_counter = 0
        self.pyramid_counter = 0
        self.analysis_interval = pd.Timedelta(minutes=1 if self.config.base_timeframe == "M1" else 5)
        self.loss_cooldown = timedelta(minutes=10)
        self.trade_cooldown = timedelta(minutes=5)
        self.symbol_state = self._fresh_symbol_state()
        self.balance = strategy_config.starting_balance
        self.equity_points: list[dict] = []
        self.closed_positions: list[Position] = []
        self.active_positions: dict[str, list[Position]] = defaultdict(list)
        self.active_pyramids: dict[str, PyramidSession] = {}
        self.pyramid_meta: dict[str, dict] = {}

    def _build_symbol_map(self) -> Dict[str, SymbolConfig]:
        out: Dict[str, SymbolConfig] = {}
        for item in SYMBOLS:
            if item["display"] == "XAUUSD":
                costs = ExecutionCosts(spread=0.25, slippage=1.0 * item["pip"], commission_per_lot_round_turn=0.0)
            else:
                costs = ExecutionCosts(spread=0.025, slippage=0.5 * item["pip"], commission_per_lot_round_turn=0.0)
            out[item["display"]] = SymbolConfig(**item, costs=costs)
        return out

    def _fresh_symbol_state(self) -> dict:
        return {
            display: {
                "last_loss_time": None,
                "last_trade_time": None,
                "consecutive_losses": 0,
                "circuit_break_until": None,
            }
            for display in self.symbols.keys()
        }

    def connect(self) -> None:
        if not connect_with_retry(self.bridge, LIVE_CONFIG["max_reconnect_attempts"]):
            raise RuntimeError("Could not connect to MT5 bridge for backtest")

    def disconnect(self) -> None:
        try:
            self.bridge.disconnect()
        except Exception:
            pass

    def load_data(self) -> Dict[str, Dict[str, pd.DataFrame]]:
        data: Dict[str, Dict[str, pd.DataFrame]] = {}
        for symbol in self.symbols.values():
            if self.config.bar_count:
                data[symbol.display] = {
                    tf: self.data_store.fetch_recent_count(symbol, tf, self.config.bar_count)
                    for tf in [self.config.base_timeframe, "M15", "H1", "H4", "D1"]
                }
            else:
                data[symbol.display] = {
                    self.config.base_timeframe: self.data_store.fetch_last_months(
                        symbol, self.config.base_timeframe, self.config.months
                    )
                }
        return data

    def run(self) -> BacktestResult:
        self.connect()
        try:
            raw_data = self.load_data()
            return self._run_on_data(raw_data)
        finally:
            self.disconnect()

    def _run_on_data(self, raw_data: Dict[str, Dict[str, pd.DataFrame]]) -> BacktestResult:
        base_tf = self.config.base_timeframe
        time_index = sorted(set().union(*[set(tf_map[base_tf]["time"].tolist()) for tf_map in raw_data.values()]))
        analyzer = HistoricalMarketAnalyzer(self._ensure_utc_ts(time_index[0]))
        analyzer.override_weights = {
            "buy_threshold": self.config.buy_threshold,
            "sell_threshold": self.config.sell_threshold,
            "ranging_penalty": self.config.ranging_penalty,
        }

        per_symbol_data: dict[str, dict[str, pd.DataFrame]] = {
            sym: {tf: df.set_index("time").sort_index() for tf, df in tf_map.items()} for sym, tf_map in raw_data.items()
        }

        for ts in time_index:
            current_ts = self._ensure_utc_ts(ts)
            analyzer.set_backtest_ts(current_ts)
            self._mark_equity(current_ts)

            for display, symbol in self.symbols.items():
                full_df = per_symbol_data[display][base_tf]
                if current_ts not in full_df.index:
                    continue

                current_bar = full_df.loc[current_ts]
                current_bar = current_bar.iloc[-1] if isinstance(current_bar, pd.DataFrame) else current_bar
                self._update_open_positions(symbol, current_ts, current_bar)

                hist_df = full_df.loc[:current_ts].reset_index()
                if len(hist_df) < self.config.min_warmup_bars:
                    continue

                tf_data = self._build_timeframes(hist_df, per_symbol_data[display], current_ts)
                if not self._tf_ready(tf_data):
                    continue

                tick = self._synthetic_tick(symbol, float(current_bar["c"]))
                signal = analyzer.analyze(tf_data, tick, symbol.display)
                self._attempt_entry(symbol, current_ts, current_bar, signal)
                if self.config.use_pyramid:
                    self._attempt_pyramid_add(symbol, current_ts, current_bar, signal)

            self._mark_equity(current_ts)

        self._force_close_all(self._ensure_utc_ts(time_index[-1]))
        trades_df = self._trades_dataframe()
        equity_df = pd.DataFrame(self.equity_points)
        summary = self._build_summary(trades_df, equity_df)
        monthly = self._build_monthly_breakdown(trades_df)
        return self._export_result(trades_df, equity_df, summary, monthly)

    def _ensure_utc_ts(self, value) -> pd.Timestamp:
        ts = pd.Timestamp(value)
        return ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")

    def _tf_ready(self, tf_data: dict) -> bool:
        return all(
            tf in tf_data and len(tf_data[tf]) >= min_required
            for tf, min_required in (("M15", 200), ("H1", 100), ("H4", 60), ("D1", 30))
        )

    def _build_timeframes(
        self,
        df: pd.DataFrame,
        available_tfs: Optional[dict[str, pd.DataFrame]] = None,
        current_ts: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        if available_tfs and current_ts is not None and {"M15", "H1", "H4", "D1"}.issubset(set(available_tfs.keys())):
            out = {self.config.base_timeframe: df.copy()}
            if self.config.base_timeframe == "M1":
                out["M1"] = df.copy()
            for tf in ["M15", "H1", "H4", "D1"]:
                sliced = available_tfs[tf].loc[:current_ts].reset_index()
                if not sliced.empty:
                    out[tf] = sliced
            return out

        base = df.copy()
        base["time"] = pd.to_datetime(base["time"], utc=True)
        base = base.set_index("time").sort_index()

        def _resample(rule: str) -> pd.DataFrame:
            res = pd.DataFrame(
                {
                    "o": base["o"].resample(rule).first(),
                    "h": base["h"].resample(rule).max(),
                    "l": base["l"].resample(rule).min(),
                    "c": base["c"].resample(rule).last(),
                    "vol": base["vol"].resample(rule).sum(),
                }
            ).dropna().reset_index()
            return res

        out = {self.config.base_timeframe: base.reset_index()}
        if self.config.base_timeframe == "M1":
            out["M1"] = base.reset_index()
        out["M15"] = _resample("15min")
        out["H1"] = _resample("1h")
        out["H4"] = _resample("4h")
        out["D1"] = _resample("1D")
        return out

    def _synthetic_tick(self, symbol: SymbolConfig, mid_price: float) -> SimpleNamespace:
        half_spread = symbol.costs.spread / 2.0
        return SimpleNamespace(ask=mid_price + half_spread, bid=mid_price - half_spread, last=mid_price)

    def _confidence_band(self, confidence: float) -> str:
        pct = confidence * 100
        if pct >= 90:
            return "90%+"
        if pct >= 80:
            return "80-89%"
        if pct >= 70:
            return "70-79%"
        if pct >= 60:
            return "60-69%"
        if pct >= 50:
            return "50-59%"
        return "<50%"

    def _attempt_entry(self, symbol: SymbolConfig, ts: pd.Timestamp, bar: pd.Series, signal: dict) -> None:
        if signal.get("direction") not in ("BUY", "SELL"):
            return
        if self.active_pyramids.get(symbol.display):
            return

        state = self.symbol_state[symbol.display]
        if self._in_cooldown(state, ts):
            return

        direction = signal["direction"]
        confidence = float(signal.get("confidence", 0.0))
        indicators = signal.get("indicators", {})
        adx_val = float(indicators.get("adx", 0))
        rsi_val = float(indicators.get("rsi", 50))
        bb_pos = indicators.get("bb_position", "MID")
        sig_score = abs(float(signal.get("score", 0)))
        is_ranging = str(signal.get("factor_scores", {}).get("adx_regime", "")).startswith("RANGING")
        conf_gate = self._confidence_gate(signal.get("session", "ASIAN"), is_ranging, adx_val, signal.get("reason", ""))
        adx_ok = not (adx_val < 5 and sig_score < 8)
        fade_blocked = self._fade_blocked(direction, adx_val, rsi_val, bb_pos)
        if confidence < conf_gate or not adx_ok or fade_blocked:
            return

        tick = self._synthetic_tick(symbol, float(bar["c"]))
        order = build_order_params(
            asdict(symbol) | {"broker": symbol.broker},
            tick,
            direction,
            confidence=confidence,
            atr=float(indicators.get("atr", 0)),
            lot_reduction=1.0,
            regime_data=signal.get("factor_scores"),
        )
        order["lot"] = 0.01
        self._open_position(symbol, ts, direction, order, signal, tranche_num=1)

    def _attempt_pyramid_add(self, symbol: SymbolConfig, ts: pd.Timestamp, bar: pd.Series, signal: dict) -> None:
        session = self.active_pyramids.get(symbol.display)
        if not session:
            return
        price = float(bar["c"])
        session.started_at = self.pyramid_meta[symbol.display]["started_at"]
        session.last_add_time = self.pyramid_meta[symbol.display]["last_add_time"]
        market_state = {
            "indicators": signal.get("indicators", {}),
            "score": float(signal.get("score", 0.0)),
            "confidence": float(signal.get("confidence", 0.0)),
            "signal_direction": signal.get("direction", "HOLD"),
            "factor_scores": signal.get("factor_scores", {}),
        }
        should_add, _reason = session.should_add_tranche(price, market_state)
        if not should_add:
            return

        new_sl = session.get_sl_for_tranche(price, symbol.pip)
        tp_dist = PYRAMID_CFG["tp_pips"] * symbol.pip
        new_tp = round(session.first_entry_price + tp_dist, 2) if session.direction == "BUY" else round(session.first_entry_price - tp_dist, 2)
        confidence = float(signal.get("confidence", 0.0))
        order = {
            "symbol": symbol.broker,
            "direction": session.direction,
            "lot": PYRAMID_CFG["tranche_lot"],
            "price": price,
            "sl": new_sl,
            "tp": new_tp,
            "comment": f"PYR{session.tranche_count + 1}-{session.direction}",
        }
        position = self._open_position(symbol, ts, session.direction, order, signal, tranche_num=session.tranche_count + 1, pyramid_id_override=self.pyramid_meta[symbol.display]["pyramid_id"])
        session.record_tranche(position.ticket, position.entry_price)
        self.pyramid_meta[symbol.display]["last_add_time"] = ts.timestamp()

        if session.tranche_count >= PYRAMID_CFG["breakeven_at_tranche"]:
            for open_pos in self.active_positions[symbol.display]:
                open_pos.sl = new_sl

    def _open_position(
        self,
        symbol: SymbolConfig,
        ts: pd.Timestamp,
        direction: str,
        order: dict,
        signal: dict,
        tranche_num: int,
        pyramid_id_override: Optional[str] = None,
    ) -> Position:
        self.trade_counter += 1
        half_spread = symbol.costs.spread / 2.0
        slippage = symbol.costs.slippage
        mid = float(order["price"])
        if direction == "BUY":
            entry_price = mid + half_spread + slippage
        else:
            entry_price = mid - half_spread - slippage

        if pyramid_id_override:
            pyramid_id = pyramid_id_override
        else:
            self.pyramid_counter += 1
            pyramid_id = f"{symbol.display}-{self.pyramid_counter}"

        if tranche_num == 1:
            session = PyramidSession(symbol.display, direction, entry_price, str(self.trade_counter), symbol.pip)
            session.started_at = ts.timestamp()
            session.last_add_time = ts.timestamp()
            self.active_pyramids[symbol.display] = session
            self.pyramid_meta[symbol.display] = {
                "pyramid_id": pyramid_id,
                "started_at": ts.timestamp(),
                "last_add_time": ts.timestamp(),
            }

        commission = symbol.costs.commission_per_lot_round_turn * order["lot"]
        pos = Position(
            ticket=str(self.trade_counter),
            pyramid_id=pyramid_id,
            symbol=symbol.display,
            broker_symbol=symbol.broker,
            direction=direction,
            volume=float(order["lot"]),
            entry_time=ts,
            entry_price=entry_price,
            sl=float(order["sl"]),
            tp=float(order["tp"]),
            confidence=float(signal.get("confidence", 0.0)),
            score=float(signal.get("score", 0.0)),
            comment=str(order.get("comment", "")),
            tranche_num=tranche_num,
            session=str(signal.get("session", "UNKNOWN")),
            regime=str(signal.get("factor_scores", {}).get("adx_regime", "UNKNOWN")),
            last_mark_price=entry_price,
            commission_usd=commission,
            confidence_band=self._confidence_band(float(signal.get("confidence", 0.0))),
        )
        self.active_positions[symbol.display].append(pos)
        state = self.symbol_state[symbol.display]
        state["last_trade_time"] = ts
        return pos

    def _in_cooldown(self, state: dict, ts: pd.Timestamp) -> bool:
        loss_time = state["last_loss_time"]
        trade_time = state["last_trade_time"]
        cb_until = state["circuit_break_until"]
        if cb_until is not None and ts < cb_until:
            return True
        if loss_time is not None and ts - loss_time < self.loss_cooldown:
            return True
        if trade_time is not None and ts - trade_time < self.trade_cooldown:
            return True
        return False

    def _fade_blocked(self, direction: str, adx: float, rsi: float, bb_pos: str) -> bool:
        if adx < 20:
            return False
        if direction == "SELL" and adx > 40 and rsi < 30 and bb_pos in ("BELOW_MID", "BELOW_LOW", "BELOW_LOWER"):
            return True
        if direction == "BUY" and adx > 40 and rsi > 70 and bb_pos in ("ABOVE_MID", "ABOVE_HIGH", "ABOVE_UPPER"):
            return True
        return False

    def _confidence_gate(self, session: str, is_ranging: bool, adx: float, reason: str) -> float:
        sess_cfg = SESSION_CONFIG.get(session, {"min_conf": 0.48})
        if "[Score override" in reason:
            base = 0.45 if is_ranging else 0.48
        elif "[Indicator fallback]" in reason:
            base = 0.45
        else:
            base = 0.45 if is_ranging else float(sess_cfg["min_conf"])

        adx_mod = -0.05 if adx > 25 else 0.05 if adx < 15 else 0.0
        return max(0.35, min(0.75, round(base + adx_mod, 3)))

    def _update_open_positions(self, symbol: SymbolConfig, ts: pd.Timestamp, bar: pd.Series) -> None:
        remaining: list[Position] = []
        for pos in self.active_positions[symbol.display]:
            exit_price, exit_reason = self._check_bar_exit(symbol, pos, bar)
            if exit_price is None:
                current_mid = float(bar["c"])
                mark_price = current_mid - symbol.costs.spread / 2.0 if pos.direction == "BUY" else current_mid + symbol.costs.spread / 2.0
                pos.last_mark_price = mark_price
                profit = self._position_profit_usd(symbol, pos, mark_price)
                pos.peak_profit_usd = max(pos.peak_profit_usd, profit)
                if self._trailing_or_balance_exit(pos, symbol, profit, mark_price):
                    self._close_position(pos, ts, mark_price, "trailing_or_balance")
                    continue
                remaining.append(pos)
                continue
            self._close_position(pos, ts, exit_price, exit_reason)

        self.active_positions[symbol.display] = remaining
        if not remaining:
            self.active_pyramids.pop(symbol.display, None)
            self.pyramid_meta.pop(symbol.display, None)

    def _check_bar_exit(self, symbol: SymbolConfig, pos: Position, bar: pd.Series) -> tuple[Optional[float], str]:
        high = float(bar["h"])
        low = float(bar["l"])
        open_ = float(bar["o"])
        close = float(bar["c"])
        bullish_bar = close >= open_

        def _apply_exit_cost(raw_price: float) -> float:
            if pos.direction == "BUY":
                return raw_price - symbol.costs.slippage - symbol.costs.spread / 2.0
            return raw_price + symbol.costs.slippage + symbol.costs.spread / 2.0

        hit_sl = low <= pos.sl if pos.direction == "BUY" else high >= pos.sl
        hit_tp = high >= pos.tp if pos.direction == "BUY" else low <= pos.tp
        if hit_sl and hit_tp:
            return (_apply_exit_cost(pos.sl), "stop_and_target_same_bar")
        if hit_sl:
            return (_apply_exit_cost(pos.sl), "stop_loss")
        if hit_tp:
            return (_apply_exit_cost(pos.tp), "take_profit")

        if pos.direction == "BUY" and bullish_bar and low <= pos.sl and high >= pos.tp:
            return (_apply_exit_cost(pos.sl), "stop_first_bullish")
        if pos.direction == "SELL" and not bullish_bar and high >= pos.sl and low <= pos.tp:
            return (_apply_exit_cost(pos.sl), "stop_first_bearish")
        return (None, "")

    def _trailing_or_balance_exit(self, pos: Position, symbol: SymbolConfig, profit: float, mark_price: float) -> bool:
        profit_target = self.balance * (LIVE_CONFIG["profit_close_pct"] / 100)
        loss_limit = self.balance * (LIVE_CONFIG["loss_close_pct"] / 100)
        trail_trigger = self.balance * 0.01
        trail_lock_pct = 0.40
        if profit >= profit_target or profit <= -loss_limit:
            return True
        return pos.peak_profit_usd >= trail_trigger and profit < pos.peak_profit_usd * trail_lock_pct

    def _close_position(self, pos: Position, ts: pd.Timestamp, exit_price: float, reason: str) -> None:
        symbol = self.symbols[pos.symbol]
        pos.exit_time = ts
        pos.exit_price = exit_price
        pos.exit_reason = reason
        gross_usd = self._position_profit_usd(symbol, pos, exit_price)
        pip_move = self._position_pips(symbol, pos, exit_price)
        pos.realized_pnl_usd = gross_usd - pos.commission_usd
        pos.realized_pnl_pips = pip_move
        self.balance += pos.realized_pnl_usd
        self.closed_positions.append(pos)

        state = self.symbol_state[pos.symbol]
        state["last_trade_time"] = ts
        if pos.realized_pnl_usd > 0:
            state["consecutive_losses"] = 0
        else:
            state["consecutive_losses"] += 1
            state["last_loss_time"] = ts
            if state["consecutive_losses"] >= 4:
                state["circuit_break_until"] = ts + self.analysis_interval
        if state["circuit_break_until"] and state["consecutive_losses"] == 0:
            state["circuit_break_until"] = None

    def _position_pips(self, symbol: SymbolConfig, pos: Position, mark_price: float) -> float:
        move = (mark_price - pos.entry_price) / symbol.pip
        if pos.direction == "SELL":
            move = -move
        return round(move, 2)

    def _position_profit_usd(self, symbol: SymbolConfig, pos: Position, mark_price: float) -> float:
        pips = self._position_pips(symbol, pos, mark_price)
        pip_value = symbol.pip * symbol.contract_size * pos.volume
        return pips * pip_value

    def _mark_equity(self, ts: pd.Timestamp) -> None:
        unrealized = 0.0
        for display, positions in self.active_positions.items():
            symbol = self.symbols[display]
            for pos in positions:
                unrealized += self._position_profit_usd(symbol, pos, pos.last_mark_price or pos.entry_price)
        self.equity_points.append({"time": ts, "balance": self.balance, "equity": self.balance + unrealized})

    def _force_close_all(self, ts: pd.Timestamp) -> None:
        for display, positions in list(self.active_positions.items()):
            symbol = self.symbols[display]
            for pos in list(positions):
                mark_price = pos.last_mark_price or pos.entry_price
                self._close_position(pos, ts, mark_price, "end_of_test")
            self.active_positions[display] = []
            self.active_pyramids.pop(display, None)
            self.pyramid_meta.pop(display, None)
        self._mark_equity(ts)

    def _trades_dataframe(self) -> pd.DataFrame:
        rows = []
        for pos in self.closed_positions:
            rows.append(
                {
                    "ticket": pos.ticket,
                    "pyramid_id": pos.pyramid_id,
                    "symbol": pos.symbol,
                    "broker_symbol": pos.broker_symbol,
                    "direction": pos.direction,
                    "volume": pos.volume,
                    "entry_time": pos.entry_time,
                    "exit_time": pos.exit_time,
                    "hold_minutes": (pos.exit_time - pos.entry_time).total_seconds() / 60 if pos.exit_time else None,
                    "entry_price": pos.entry_price,
                    "exit_price": pos.exit_price,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "tranche_num": pos.tranche_num,
                    "confidence": pos.confidence,
                    "confidence_band": pos.confidence_band,
                    "score": pos.score,
                    "session": pos.session,
                    "regime": pos.regime,
                    "comment": pos.comment,
                    "exit_reason": pos.exit_reason,
                    "pnl_usd": round(pos.realized_pnl_usd, 2),
                    "pnl_pips": round(pos.realized_pnl_pips, 2),
                    "commission_usd": round(pos.commission_usd, 2),
                    "win": pos.realized_pnl_usd > 0,
                }
            )
        if not rows:
            return pd.DataFrame(columns=["ticket", "symbol", "direction", "pnl_usd", "pnl_pips"])
        df = pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
        df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
        return df

    def _build_monthly_breakdown(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        if trades_df.empty:
            return pd.DataFrame(columns=["month", "pnl_usd", "pnl_pips", "trades", "win_rate"])
        monthly = trades_df.copy()
        monthly["month"] = monthly["exit_time"].dt.to_period("M").astype(str)
        out = (
            monthly.groupby("month")
            .agg(
                pnl_usd=("pnl_usd", "sum"),
                pnl_pips=("pnl_pips", "sum"),
                trades=("ticket", "count"),
                win_rate=("win", "mean"),
            )
            .reset_index()
        )
        out["win_rate"] = (out["win_rate"] * 100).round(2)
        return out

    def _build_summary(self, trades_df: pd.DataFrame, equity_df: pd.DataFrame) -> Dict[str, float | str | int]:
        if trades_df.empty:
            return {
                "config": self.config.name,
                "profitable": False,
                "total_pnl_usd": 0.0,
                "total_pnl_pips": 0.0,
                "trade_count": 0,
            }

        gross_profit = trades_df.loc[trades_df["pnl_usd"] > 0, "pnl_usd"].sum()
        gross_loss = abs(trades_df.loc[trades_df["pnl_usd"] < 0, "pnl_usd"].sum())
        win_rate = trades_df["win"].mean() * 100
        expectancy = trades_df["pnl_pips"].mean()
        hold_minutes = trades_df["hold_minutes"].mean()
        profit_factor = gross_profit / gross_loss if gross_loss else math.inf
        sharpe = self._sharpe_ratio(trades_df)
        max_dd = self._max_drawdown(equity_df)
        losing_streak = self._worst_losing_streak(trades_df)
        best_trade = trades_df.loc[trades_df["pnl_usd"].idxmax()].to_dict()
        worst_trade = trades_df.loc[trades_df["pnl_usd"].idxmin()].to_dict()
        conf_band = self._confidence_band_breakdown(trades_df)
        symbol_direction = (
            trades_df.groupby(["symbol", "direction"]).size().rename("trades").reset_index().to_dict("records")
        )

        return {
            "config": self.config.name,
            "profitable": bool(trades_df["pnl_usd"].sum() > 0),
            "total_pnl_usd": round(trades_df["pnl_usd"].sum(), 2),
            "total_pnl_pips": round(trades_df["pnl_pips"].sum(), 2),
            "trade_count": int(len(trades_df)),
            "win_rate_pct": round(win_rate, 2),
            "avg_hold_minutes": round(float(hold_minutes or 0), 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 4),
            "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else "inf",
            "expectancy_pips": round(expectancy, 2),
            "gross_profit_usd": round(gross_profit, 2),
            "gross_loss_usd": round(gross_loss, 2),
            "worst_losing_streak": int(losing_streak),
            "best_trade": {
                "ticket": best_trade["ticket"],
                "symbol": best_trade["symbol"],
                "direction": best_trade["direction"],
                "pnl_usd": round(float(best_trade["pnl_usd"]), 2),
                "pnl_pips": round(float(best_trade["pnl_pips"]), 2),
            },
            "worst_trade": {
                "ticket": worst_trade["ticket"],
                "symbol": worst_trade["symbol"],
                "direction": worst_trade["direction"],
                "pnl_usd": round(float(worst_trade["pnl_usd"]), 2),
                "pnl_pips": round(float(worst_trade["pnl_pips"]), 2),
            },
            "confidence_band_win_rate": conf_band,
            "trades_by_symbol_direction": symbol_direction,
        }

    def _confidence_band_breakdown(self, trades_df: pd.DataFrame) -> Dict[str, dict]:
        out = {}
        if trades_df.empty:
            return out
        for band, group in trades_df.groupby("confidence_band"):
            out[band] = {
                "trades": int(len(group)),
                "win_rate_pct": round(group["win"].mean() * 100, 2),
                "pnl_usd": round(group["pnl_usd"].sum(), 2),
            }
        return out

    def _sharpe_ratio(self, trades_df: pd.DataFrame) -> float:
        returns = trades_df["pnl_usd"] / self.config.starting_balance
        if returns.std(ddof=1) == 0 or len(returns) < 2:
            return 0.0
        return float((returns.mean() / returns.std(ddof=1)) * math.sqrt(len(returns)))

    def _max_drawdown(self, equity_df: pd.DataFrame) -> float:
        if equity_df.empty:
            return 0.0
        eq = equity_df["equity"].astype(float)
        peak = eq.cummax()
        dd = ((eq - peak) / peak.replace(0, pd.NA)).fillna(0)
        return abs(float(dd.min()) * 100)

    def _worst_losing_streak(self, trades_df: pd.DataFrame) -> int:
        streak = worst = 0
        for pnl in trades_df["pnl_usd"].tolist():
            if pnl <= 0:
                streak += 1
                worst = max(worst, streak)
            else:
                streak = 0
        return worst

    def _export_result(self, trades_df: pd.DataFrame, equity_df: pd.DataFrame, summary: dict, monthly: pd.DataFrame) -> BacktestResult:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        prefix = f"{self.config.export_prefix}_{self.config.name}_{stamp}"
        trade_log_path = TRADE_LOG_DIR / f"{prefix}_trades.csv"
        monthly_path = REPORT_DIR / f"{prefix}_monthly.csv"
        report_path = REPORT_DIR / f"{prefix}_summary.txt"
        equity_png_path = REPORT_DIR / f"{prefix}_equity.png"
        summary_json_path = REPORT_DIR / f"{prefix}_summary.json"

        trades_df.to_csv(trade_log_path, index=False)
        monthly.to_csv(monthly_path, index=False)
        self._write_report(report_path, summary, monthly)
        self._plot_equity(equity_df, equity_png_path)
        summary_json_path.write_text(json.dumps(summary, indent=2, default=str))

        return BacktestResult(
            config=self.config,
            trades=trades_df,
            equity_curve=equity_df,
            summary=summary,
            monthly=monthly,
            report_path=report_path,
            trade_log_path=trade_log_path,
            equity_png_path=equity_png_path,
        )

    def _write_report(self, path: Path, summary: dict, monthly: pd.DataFrame) -> None:
        lines = [
            f"Backtest Report: {self.config.name}",
            f"Thresholds: buy={self.config.buy_threshold} sell={self.config.sell_threshold}",
            f"Ranging penalty: {self.config.ranging_penalty}",
            f"Pyramid scaling: {'ON' if self.config.use_pyramid else 'OFF'}",
            "",
            "Summary Metrics",
            f"- Profitable: {summary.get('profitable')}",
            f"- Total PnL USD: {summary.get('total_pnl_usd')}",
            f"- Total PnL Pips: {summary.get('total_pnl_pips')}",
            f"- Trades: {summary.get('trade_count')}",
            f"- Win rate: {summary.get('win_rate_pct')}%",
            f"- Avg hold: {summary.get('avg_hold_minutes')} min",
            f"- Max drawdown: {summary.get('max_drawdown_pct')}%",
            f"- Sharpe: {summary.get('sharpe_ratio')}",
            f"- Profit factor: {summary.get('profit_factor')}",
            f"- Expectancy: {summary.get('expectancy_pips')} pips/trade",
            f"- Worst losing streak: {summary.get('worst_losing_streak')}",
            "",
            f"Best trade: {summary.get('best_trade')}",
            f"Worst trade: {summary.get('worst_trade')}",
            "",
            "Monthly Breakdown",
        ]
        if monthly.empty:
            lines.append("- No trades")
        else:
            for row in monthly.to_dict("records"):
                lines.append(
                    f"- {row['month']}: USD {row['pnl_usd']:.2f}, Pips {row['pnl_pips']:.2f}, Trades {row['trades']}, Win rate {row['win_rate']:.2f}%"
                )
        lines.extend(["", "Confidence Bands"])
        for band, payload in summary.get("confidence_band_win_rate", {}).items():
            lines.append(
                f"- {band}: trades={payload['trades']}, win_rate={payload['win_rate_pct']}%, pnl_usd={payload['pnl_usd']}"
            )
        path.write_text("\n".join(lines))

    def _plot_equity(self, equity_df: pd.DataFrame, output_path: Path) -> None:
        try:
            import matplotlib.pyplot as plt
        except Exception as exc:
            output_path.with_suffix(".txt").write_text(f"matplotlib unavailable: {exc}")
            return

        if equity_df.empty:
            return
        plt.figure(figsize=(12, 6))
        plt.plot(pd.to_datetime(equity_df["time"]), equity_df["equity"], label="Equity", linewidth=1.5)
        plt.plot(pd.to_datetime(equity_df["time"]), equity_df["balance"], label="Balance", linewidth=1.0, alpha=0.8)
        plt.title(f"Equity Curve — {self.config.name}")
        plt.xlabel("Time")
        plt.ylabel("USD")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()


def default_comparison_configs() -> list[StrategyConfig]:
    combos = []
    for threshold in (12, 15, 8):
        for penalty in (0.80, 0.60, 0.50):
            for use_pyramid in (True, False):
                name = f"thr{threshold}_pen{str(penalty).replace('.', '')}_{'pyr' if use_pyramid else 'flat'}"
                combos.append(
                    StrategyConfig(
                        name=name,
                        buy_threshold=threshold,
                        sell_threshold=-threshold,
                        ranging_penalty=penalty,
                        use_pyramid=use_pyramid,
                    )
                )
    return combos


def run_comparison(configs: Iterable[StrategyConfig]) -> pd.DataFrame:
    rows = []
    for cfg in configs:
        engine = BacktestEngine(cfg)
        result = engine.run()
        rows.append(
            {
                "config": cfg.name,
                "threshold": cfg.buy_threshold,
                "ranging_penalty": cfg.ranging_penalty,
                "use_pyramid": cfg.use_pyramid,
                "profitable": result.summary.get("profitable"),
                "total_pnl_usd": result.summary.get("total_pnl_usd"),
                "total_pnl_pips": result.summary.get("total_pnl_pips"),
                "trade_count": result.summary.get("trade_count"),
                "win_rate_pct": result.summary.get("win_rate_pct", 0),
                "max_drawdown_pct": result.summary.get("max_drawdown_pct", 0),
                "sharpe_ratio": result.summary.get("sharpe_ratio", 0),
                "profit_factor": result.summary.get("profit_factor", 0),
                "report_path": str(result.report_path),
                "trade_log_path": str(result.trade_log_path),
                "equity_png_path": str(result.equity_png_path),
            }
        )
    df = pd.DataFrame(rows).sort_values(["profitable", "total_pnl_usd", "sharpe_ratio"], ascending=[False, False, False])
    comparison_path = REPORT_DIR / f"comparison_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(comparison_path, index=False)
    log.info("Comparison report written to %s", comparison_path)
    return df


def print_terminal_summary(result: BacktestResult) -> None:
    summary = result.summary
    print("=" * 72)
    print(f"BACKTEST COMPLETE: {result.config.name}")
    print("=" * 72)
    print(f"Profitable       : {summary.get('profitable')}")
    print(f"Total PnL (USD)  : {summary.get('total_pnl_usd')}")
    print(f"Total PnL (Pips) : {summary.get('total_pnl_pips')}")
    print(f"Win Rate         : {summary.get('win_rate_pct')}%")
    print(f"Trades           : {summary.get('trade_count')}")
    print(f"Max Drawdown     : {summary.get('max_drawdown_pct')}%")
    print(f"Sharpe Ratio     : {summary.get('sharpe_ratio')}")
    print(f"Profit Factor    : {summary.get('profit_factor')}")
    print(f"Expectancy       : {summary.get('expectancy_pips')} pips/trade")
    print(f"Trade Log        : {result.trade_log_path}")
    print(f"Summary Report   : {result.report_path}")
    print(f"Equity Curve PNG : {result.equity_png_path}")
    print("=" * 72)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MT5 backtest engine for the live XAUUSD/XAGUSD strategy")
    parser.add_argument("--months", type=int, default=6, help="Months of history to test")
    parser.add_argument("--bar-count", type=int, help="Use the latest N candles per timeframe instead of a date-range fetch")
    parser.add_argument("--base-timeframe", default="M1", choices=["M1", "M5"], help="Base historical timeframe")
    parser.add_argument("--buy-threshold", type=float, default=12)
    parser.add_argument("--sell-threshold", type=float, default=-12)
    parser.add_argument("--ranging-penalty", type=float, default=0.80)
    parser.add_argument("--starting-balance", type=float, default=10_000.0)
    parser.add_argument("--disable-pyramid", action="store_true")
    parser.add_argument("--compare", action="store_true", help="Run the comparison grid requested in the brief")
    parser.add_argument("--name", default="current_config")
    parser.add_argument("--export-prefix", default="backtest")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    args = parse_args()

    if args.compare:
        cfgs = default_comparison_configs()
        for cfg in cfgs:
            cfg.months = args.months
            cfg.bar_count = args.bar_count
            cfg.base_timeframe = args.base_timeframe
            cfg.starting_balance = args.starting_balance
            cfg.export_prefix = args.export_prefix
        comparison = run_comparison(cfgs)
        print(comparison.to_string(index=False))
        if not comparison.empty:
            best = comparison.iloc[0]
            print("\nBest configuration:")
            print(best.to_dict())
        return

    cfg = StrategyConfig(
        name=args.name,
        buy_threshold=args.buy_threshold,
        sell_threshold=args.sell_threshold,
        ranging_penalty=args.ranging_penalty,
        use_pyramid=not args.disable_pyramid,
        base_timeframe=args.base_timeframe,
        months=args.months,
        bar_count=args.bar_count,
        starting_balance=args.starting_balance,
        export_prefix=args.export_prefix,
    )
    engine = BacktestEngine(cfg)
    result = engine.run()
    print_terminal_summary(result)


if __name__ == "__main__":
    main()
