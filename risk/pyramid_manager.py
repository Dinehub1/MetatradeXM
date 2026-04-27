"""
pyramid_manager.py — Smart 10-Tranche Pyramid Position Manager

Instead of entering 0.10 lot at once, splits into 10× 0.01 tranches
and scales in ONLY as price confirms direction.

Bad trades die small (0.01-0.02 lot). Good trades grow to full size.

Key mechanics:
  - Tranche 1: Test entry at 0.01 lot
  - Tranches 2-3: Quick adds if price moves in our favor (+3, +6 pips)
  - Tranches 4-10: Require indicator confirmation + continued momentum
  - SL moves to breakeven after tranche 3
  - Trailing SL activates after tranche 5
  - Each tranche has independent partial-close levels
"""

import json
import sqlite3
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from core.paths import DATA_DIR, STATE_DIR

log = logging.getLogger("pyramid")

DB_PATH = DATA_DIR / "trade_memory.db"
STATE_PATH = STATE_DIR / "pyramid_state.json"

# ── Pyramid Configuration ─────────────────────────────────────────────────────
PYRAMID_CFG = {
    "tranche_lot": 0.01,       # each tranche size
    "max_tranches": 10,        # total tranches per pyramid

    # Entry ladder — pip thresholds for each add
    # Tranche 1 is the initial entry (always fires)
    # Tranche 2+ requires price to have moved X pips in our direction
    "entry_ladder_pips": [0, 3, 6, 10, 15, 20, 25, 30, 35, 40],

    # Tranches 1-2 fire without extra confirmation (momentum check only)
    # Tranches 3+ require indicator confirmation
    "confirm_from_tranche": 3,

    # SL Management
    "initial_sl_pips": 30,      # tight SL on first tranche (0.01 × 30 pips = ~$3 risk)
    "breakeven_at_tranche": 4,  # wait for more confirmation before lifting the whole basket
    "trail_after_tranche": 6,   # start trailing later so shallow pullbacks do not clip the stack
    "trail_distance_pips": 20,  # wider trail to reduce premature basket stop-outs

    # TP — let broker TP handle the final target
    "tp_pips": 160,             # same as current SYMBOLS config

    # Safety
    "max_open_pyramids": 2,     # max concurrent pyramid groups
    "abort_drawdown_pips": 8,   # if latest tranche drops 8 pips, stop adding
    "cooldown_between_tranches_s": 10,  # minimum seconds between tranche adds

    # Time limit — don't add tranches forever
    "max_pyramid_duration_s": 3600,  # 1 hour max from first entry to last add
}


# ── State Management ──────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"pyramids": {}}  # symbol -> pyramid data


def _save_state(state: dict):
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning(f"[PYRAMID] State save error: {e}")


def _ensure_table():
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pyramid_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    direction   TEXT NOT NULL,
                    tranche_num INTEGER NOT NULL,
                    ticket      TEXT,
                    lot         REAL,
                    entry_price REAL,
                    sl          REAL,
                    tp          REAL,
                    pips_from_first REAL,
                    status      TEXT DEFAULT 'OPEN'
                )
            """)
    except Exception as e:
        log.warning(f"[PYRAMID] DB init error: {e}")


# ── Pyramid Session ───────────────────────────────────────────────────────────

class PyramidSession:
    """Tracks one active pyramid for a symbol/direction."""

    def __init__(self, symbol: str, direction: str, first_entry_price: float,
                 first_ticket: str, pip_size: float):
        self.symbol = symbol
        self.direction = direction
        self.first_entry_price = first_entry_price
        self.pip_size = pip_size
        self.started_at = time.time()
        self.last_add_time = time.time()
        self.tranches = [
            {
                "num": 1,
                "ticket": first_ticket,
                "price": first_entry_price,
                "lot": PYRAMID_CFG["tranche_lot"],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        ]

    @property
    def tranche_count(self) -> int:
        return len(self.tranches)

    @property
    def total_lot(self) -> float:
        return round(sum(t["lot"] for t in self.tranches), 2)

    @property
    def avg_entry_price(self) -> float:
        total_cost = sum(t["price"] * t["lot"] for t in self.tranches)
        total_lot = self.total_lot
        return total_cost / total_lot if total_lot > 0 else self.first_entry_price

    def current_pips_from_entry(self, current_price: float) -> float:
        """How many pips has price moved from first entry in our direction."""
        diff = current_price - self.first_entry_price
        if self.direction == "SELL":
            diff = -diff
        return diff / self.pip_size

    def should_add_tranche(self, current_price: float, market_state: dict = None) -> tuple:
        """
        Check if next tranche should be added.
        Returns (should_add: bool, reason: str)
        """
        cfg = PYRAMID_CFG

        # Max tranches reached
        if self.tranche_count >= cfg["max_tranches"]:
            return False, "max tranches reached"

        # Time limit
        elapsed = time.time() - self.started_at
        if elapsed > cfg["max_pyramid_duration_s"]:
            return False, f"pyramid duration exceeded ({elapsed:.0f}s)"

        # Cooldown between adds
        since_last = time.time() - self.last_add_time
        if since_last < cfg["cooldown_between_tranches_s"]:
            return False, f"cooldown ({since_last:.0f}s < {cfg['cooldown_between_tranches_s']}s)"

        # Check pip progress
        pips = self.current_pips_from_entry(current_price)
        next_tranche_idx = self.tranche_count  # 0-indexed, so tranche_count = next index
        required_pips = cfg["entry_ladder_pips"][next_tranche_idx]

        if pips < required_pips:
            return False, f"only {pips:.1f} pips (need {required_pips})"

        # Check if latest tranche is in drawdown (abort signal)
        latest_price = self.tranches[-1]["price"]
        latest_diff = current_price - latest_price
        if self.direction == "SELL":
            latest_diff = -latest_diff
        latest_pips = latest_diff / self.pip_size
        if latest_pips < -cfg["abort_drawdown_pips"]:
            return False, f"latest tranche in drawdown ({latest_pips:.1f} pips)"

        # Indicator confirmation for tranches 3+
        if next_tranche_idx >= cfg["confirm_from_tranche"] - 1:
            if market_state is None:
                return False, "no indicators for confirmation"

            confirmed, reason = self._check_confirmation(market_state)
            if not confirmed:
                return False, f"confirmation failed: {reason}"

        return True, f"ladder +{pips:.1f} pips (need {required_pips})"

    def _check_confirmation(self, market_state: dict) -> tuple:
        """Check if indicators confirm continued momentum."""
        indicators = market_state.get("indicators", market_state)
        score = float(market_state.get("score", 0.0))
        confidence = float(market_state.get("confidence", 0.0))
        signal_direction = market_state.get("signal_direction", "")
        factor_scores = market_state.get("factor_scores", {}) or {}
        directional_score = score if self.direction == "BUY" else -score
        tranche_num = self.tranche_count + 1

        # Don't keep layering if the newest analyzed signal is still fighting the pyramid.
        if signal_direction and signal_direction != self.direction:
            return False, f"signal flipped to {signal_direction}"

        # Late tranches need real conviction, not just price drift.
        min_conf = 0.50 if tranche_num <= 4 else 0.58
        if confidence and confidence < min_conf:
            return False, f"confidence too low ({confidence:.0%} < {min_conf:.0%})"

        min_score = 1.5 if tranche_num <= 4 else 6.0
        if directional_score < min_score:
            return False, f"directional score too weak ({directional_score:+.1f} < {min_score:+.1f})"

        disagree_count = 0
        f1 = factor_scores.get("f1_h4_trend", 0)
        f2 = factor_scores.get("f2_h1_trend", 0)
        f10 = factor_scores.get("f10_d1_trend", 0)
        if self.direction == "BUY":
            if f1 < 0:
                disagree_count += 1
            if f2 < 0:
                disagree_count += 1
            if f10 < 0:
                disagree_count += 1
        else:
            if f1 > 0:
                disagree_count += 1
            if f2 > 0:
                disagree_count += 1
            if f10 > 0:
                disagree_count += 1

        if tranche_num >= 4 and disagree_count >= 2:
            return False, f"too much higher-TF disagreement ({disagree_count})"

        checks_passed = 0
        checks_total = 0
        reasons = []

        # 1. EMA alignment — price should be on the right side of EMA20
        ema20 = indicators.get("ema20", 0)
        price = indicators.get("price", 0)
        if ema20 > 0 and price > 0:
            checks_total += 1
            if self.direction == "BUY" and price > ema20:
                checks_passed += 1
            elif self.direction == "SELL" and price < ema20:
                checks_passed += 1
            else:
                reasons.append(f"EMA20 misaligned ({price:.2f} vs {ema20:.2f})")

        # 2. MACD momentum — histogram should be growing in our direction
        macd_hist = indicators.get("macd_hist", 0)
        checks_total += 1
        if self.direction == "BUY" and macd_hist > 0:
            checks_passed += 1
        elif self.direction == "SELL" and macd_hist < 0:
            checks_passed += 1
        else:
            reasons.append(f"MACD against direction (hist={macd_hist:.4f})")

        # 3. RSI not extreme — don't add at overbought/oversold
        rsi = indicators.get("rsi", 50)
        checks_total += 1
        if self.direction == "BUY" and rsi < 75:
            checks_passed += 1
        elif self.direction == "SELL" and rsi > 25:
            checks_passed += 1
        else:
            reasons.append(f"RSI extreme ({rsi:.1f})")

        # 4. ADX trending — must have momentum
        adx = indicators.get("adx", 0)
        checks_total += 1
        if adx > 20:
            checks_passed += 1
        else:
            reasons.append(f"ADX weak ({adx:.1f})")

        # Need 3/4 checks to pass for tranche 3-4, then all 4 for later adds.
        required_checks = 3 if tranche_num <= 4 else 4
        ok = checks_passed >= required_checks
        if not ok:
            return False, "; ".join(reasons)
        return True, f"{checks_passed}/{checks_total} confirmed"

    def record_tranche(self, ticket: str, price: float):
        """Record a new tranche added to the pyramid."""
        self.tranches.append({
            "num": self.tranche_count + 1,
            "ticket": ticket,
            "price": price,
            "lot": PYRAMID_CFG["tranche_lot"],
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self.last_add_time = time.time()

    def get_sl_for_tranche(self, current_price: float, pip_size: float) -> float:
        """Compute SL for a new tranche based on pyramid state."""
        cfg = PYRAMID_CFG
        digits = 2 if pip_size >= 0.01 else 5

        if self.tranche_count >= cfg["trail_after_tranche"]:
            # Trailing SL
            trail = cfg["trail_distance_pips"] * pip_size
            if self.direction == "BUY":
                return round(current_price - trail, digits)
            else:
                return round(current_price + trail, digits)
        elif self.tranche_count >= cfg["breakeven_at_tranche"]:
            # Breakeven SL (first entry price ± 1 pip buffer)
            buffer = pip_size * 1
            if self.direction == "BUY":
                return round(self.first_entry_price - buffer, digits)
            else:
                return round(self.first_entry_price + buffer, digits)
        else:
            # Initial SL
            sl_dist = cfg["initial_sl_pips"] * pip_size
            if self.direction == "BUY":
                return round(self.first_entry_price - sl_dist, digits)
            else:
                return round(self.first_entry_price + sl_dist, digits)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "first_entry_price": self.first_entry_price,
            "pip_size": self.pip_size,
            "started_at": self.started_at,
            "last_add_time": self.last_add_time,
            "tranches": self.tranches,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PyramidSession":
        session = cls(
            symbol=data["symbol"],
            direction=data["direction"],
            first_entry_price=data["first_entry_price"],
            first_ticket=data["tranches"][0]["ticket"],
            pip_size=data["pip_size"],
        )
        session.started_at = data.get("started_at", time.time())
        session.last_add_time = data.get("last_add_time", time.time())
        session.tranches = data["tranches"]
        return session


# ── Pyramid Manager ───────────────────────────────────────────────────────────

class PyramidManager:
    """
    Manages all active pyramids. Integrate in continuous_trader.py:
      - Call start_pyramid() when AI gives a BUY/SELL signal
      - Call check_pyramids() every monitor cycle (15s) to add tranches
      - Call close_pyramid() when trade exits
    """

    def __init__(self):
        _ensure_table()
        self.state = _load_state()
        self.sessions: dict[str, PyramidSession] = {}

        # Restore active sessions from state
        for sym, data in self.state.get("pyramids", {}).items():
            try:
                self.sessions[sym] = PyramidSession.from_dict(data)
                log.info(f"[PYRAMID] Restored {sym} session: {data['direction']} "
                         f"{len(data['tranches'])} tranches")
            except Exception as e:
                log.warning(f"[PYRAMID] Failed to restore {sym}: {e}")

    def has_active_pyramid(self, symbol: str) -> bool:
        return symbol in self.sessions

    def get_session(self, symbol: str) -> PyramidSession | None:
        return self.sessions.get(symbol)

    def active_pyramid_count(self) -> int:
        return len(self.sessions)

    def start_pyramid(self, symbol: str, direction: str, entry_price: float,
                      ticket: str, pip_size: float, sl: float, tp: float) -> PyramidSession:
        """
        Start a new pyramid session with the first tranche.
        Called after the first 0.01 lot order is successfully placed.
        """
        if self.active_pyramid_count() >= PYRAMID_CFG["max_open_pyramids"]:
            log.warning(f"[PYRAMID] Max pyramids reached ({PYRAMID_CFG['max_open_pyramids']})")
            return None

        session = PyramidSession(symbol, direction, entry_price, ticket, pip_size)
        self.sessions[symbol] = session
        self._save()

        log.info(f"[PYRAMID] 🔺 Started {symbol} {direction} pyramid — "
                 f"Tranche 1/10 @ {entry_price:.2f} (0.01 lot)")

        # Record to DB
        self._record_tranche(symbol, direction, 1, ticket,
                             PYRAMID_CFG["tranche_lot"], entry_price, sl, tp, 0)
        return session

    def check_pyramids(self, bridge, symbols_cfg: dict, positions_by_sym: dict = None) -> list:
        """
        Check all active pyramids and add tranches where conditions are met.
        Called every monitor cycle (15s).
        Returns list of actions taken.
        """
        if positions_by_sym is not None:
            self._reconcile(positions_by_sym, symbols_cfg)

        actions = []

        for sym_key, session in list(self.sessions.items()):
            sym_cfg = symbols_cfg.get(session.symbol)
            if not sym_cfg:
                continue

            broker_sym = sym_cfg["broker"]
            pip = sym_cfg["pip"]
            digits = 2 if pip >= 0.01 else 5

            # Get current price
            try:
                tick = bridge.get_tick(broker_sym)
                current_price = tick.bid if session.direction == "SELL" else tick.ask
            except Exception as e:
                log.debug(f"[PYRAMID] Tick error for {session.symbol}: {e}")
                continue

            # Get current indicators (cached from last analysis)
            market_state = self._get_cached_indicators(session.symbol)

            # Check if we should add a tranche
            should_add, reason = session.should_add_tranche(current_price, market_state)

            if not should_add:
                # Log at debug level to avoid spam
                if "cooldown" not in reason and "only" not in reason:
                    log.debug(f"[PYRAMID] {session.symbol}: no add — {reason}")
                continue

            # Compute SL/TP for this tranche
            new_sl = session.get_sl_for_tranche(current_price, pip)
            tp_dist = PYRAMID_CFG["tp_pips"] * pip
            if session.direction == "BUY":
                new_tp = round(session.first_entry_price + tp_dist, digits)
            else:
                new_tp = round(session.first_entry_price - tp_dist, digits)

            # Place the tranche order
            order = {
                "symbol": broker_sym,
                "direction": session.direction,
                "lot": PYRAMID_CFG["tranche_lot"],
                "price": current_price,
                "sl": new_sl,
                "tp": new_tp,
                "comment": f"PYR{session.tranche_count + 1}-{session.direction}",
            }

            pips_from_first = session.current_pips_from_entry(current_price)

            try:
                result = bridge.place_order(order)
                if result and hasattr(result, "order"):
                    ticket = str(result.order)
                    session.record_tranche(ticket, current_price)
                    self._save()

                    tranche_num = session.tranche_count
                    log.info(
                        f"[PYRAMID] 🔺 {session.symbol} Tranche {tranche_num}/10 "
                        f"@ {current_price:.2f} (+{pips_from_first:.1f} pips) "
                        f"lot=0.01 total={session.total_lot:.2f} "
                        f"SL={new_sl:.2f}"
                    )

                    self._record_tranche(
                        session.symbol, session.direction, tranche_num,
                        ticket, PYRAMID_CFG["tranche_lot"], current_price,
                        new_sl, new_tp, pips_from_first
                    )

                    actions.append({
                        "symbol": session.symbol,
                        "tranche": tranche_num,
                        "ticket": ticket,
                        "price": current_price,
                        "pips": pips_from_first,
                        "total_lot": session.total_lot,
                    })

                    # Update SL on all existing tranches if breakeven/trailing
                    if tranche_num >= PYRAMID_CFG["breakeven_at_tranche"]:
                        self._update_all_sls(bridge, session, new_sl, broker_sym)

                else:
                    log.warning(f"[PYRAMID] Tranche order failed for {session.symbol}")
            except Exception as e:
                log.warning(f"[PYRAMID] Order error: {e}")

        return actions

    def _update_all_sls(self, bridge, session: PyramidSession, new_sl: float,
                        broker_sym: str):
        """Move all tranche SLs to the new level (breakeven or trailing)."""
        for tranche in session.tranches:
            ticket = tranche["ticket"]
            try:
                bridge.modify_position(ticket, sl=new_sl)
                log.info(f"[PYRAMID] 🛡️ #{ticket} SL → {new_sl:.2f}")
            except Exception as e:
                log.debug(f"[PYRAMID] SL modify failed #{ticket}: {e}")

    def close_pyramid(self, symbol: str, reason: str = ""):
        """Remove pyramid session when all positions close."""
        if symbol in self.sessions:
            session = self.sessions.pop(symbol)
            self._save()
            log.info(f"[PYRAMID] ✅ Closed {symbol} pyramid — "
                     f"{session.tranche_count} tranches, {reason}")

    def on_position_closed(self, symbol: str, ticket: str):
        """Called when any position closes — check if pyramid should be cleaned up.

        If ticket is empty string, it means our own close_position() fired for this
        symbol — treat it as all tranches gone and close the entire pyramid.
        """
        session = self.sessions.get(symbol)
        if not session:
            return

        if ticket:
            # Remove only the specific closed tranche
            session.tranches = [t for t in session.tranches if str(t["ticket"]) != str(ticket)]
        else:
            # Empty ticket = symbol closed by our own logic; wipe all tranches
            log.info(f"[PYRAMID] {symbol}: empty-ticket close — clearing all tranches")
            session.tranches = []

        if not session.tranches:
            self.close_pyramid(symbol, "all tranches closed")
        else:
            self._save()

    def force_reconcile(self, open_tickets: set):
        """Purge any pyramid session whose tickets no longer exist in MT5.

        Call at startup (before the first trading cycle) when you have the real
        set of open broker ticket IDs.  Prevents stale sessions from blocking
        new entries after a bot restart.
        """
        for sym in list(self.sessions.keys()):
            session = self.sessions[sym]
            alive = [t for t in session.tranches if str(t["ticket"]) in open_tickets]
            if len(alive) < len(session.tranches):
                removed = len(session.tranches) - len(alive)
                log.warning(
                    f"[PYRAMID] force_reconcile: {sym} — "
                    f"{removed} ghost tranche(s) removed (not in MT5)"
                )
                session.tranches = alive
            if not session.tranches:
                self.close_pyramid(sym, "force_reconcile: no live tranches")
            else:
                self._save()

    def hard_sync(self, bridge, symbols_cfg: dict):
        """Sync ALL pyramid sessions against actual MT5 positions via bridge.

        This is the primary anti-phantom-state method. Call at the TOP of every
        trading cycle, BEFORE any entry/exit logic. Unlike force_reconcile()
        (which needs a pre-built ticket set), this method queries MT5 directly.

        Args:
            bridge:      The active bridge instance (WebhookBridge or WSBridge)
            symbols_cfg: Dict of display_name -> sym_cfg (e.g. {"XAUUSD": {...}})
        """
        if not self.sessions:
            return  # nothing to sync

        # Gather all live tickets from MT5 in one call
        try:
            all_positions = bridge.get_open_positions()
            live_tickets = {str(getattr(p, "ticket", "")) for p in (all_positions or [])}
        except Exception as e:
            log.warning(f"[PYRAMID] hard_sync: position fetch failed: {e}")
            return

        purged_total = 0
        for sym in list(self.sessions.keys()):
            session = self.sessions[sym]
            before = len(session.tranches)
            session.tranches = [
                t for t in session.tranches if str(t["ticket"]) in live_tickets
            ]
            removed = before - len(session.tranches)

            if removed > 0:
                purged_total += removed
                log.warning(
                    f"[PYRAMID] hard_sync: {sym} — "
                    f"{removed} phantom tranche(s) purged (not in MT5)"
                )

            if not session.tranches:
                self.close_pyramid(sym, "hard_sync: all tranches gone from MT5")
            elif removed > 0:
                self._save()

        if purged_total > 0:
            log.info(f"[PYRAMID] hard_sync complete: {purged_total} phantom(s) removed")

    def _reconcile(self, positions_by_sym: dict, symbols_cfg: dict):
        """Ensure stored tranches actually exist in the broker's open positions."""
        for sym, session in list(self.sessions.items()):
            cfg = symbols_cfg.get(sym)
            if not cfg:
                continue

            broker_sym = cfg["broker"]
            open_pos = positions_by_sym.get(broker_sym, [])
            open_tickets = {str(getattr(p, "ticket", "")) for p in open_pos}

            # Filter tranches that still exist in broker
            alive = [t for t in session.tranches if str(t["ticket"]) in open_tickets]

            if len(alive) < len(session.tranches):
                diff = len(session.tranches) - len(alive)
                log.info(f"[PYRAMID] ⚠️ {sym}: {diff} tranches no longer exist in MT5. Syncing state.")
                session.tranches = alive

                if not session.tranches:
                    self.close_pyramid(sym, "all tranches vanished from broker")
                else:
                    self._save()

    def update_cached_indicators(self, symbol: str, indicators: dict):
        """Cache indicators from the latest analysis for inter-cycle tranche checks."""
        self.state.setdefault("indicator_cache", {})[symbol] = indicators
        # Don't save to disk on every update — too frequent

    def _get_cached_indicators(self, symbol: str) -> dict | None:
        return self.state.get("indicator_cache", {}).get(symbol)

    def _save(self):
        self.state["pyramids"] = {
            sym: session.to_dict() for sym, session in self.sessions.items()
        }
        _save_state(self.state)

    def _record_tranche(self, symbol, direction, tranche_num, ticket,
                        lot, price, sl, tp, pips_from_first):
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute("""
                    INSERT INTO pyramid_history
                    (ts, symbol, direction, tranche_num, ticket, lot,
                     entry_price, sl, tp, pips_from_first)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ts, symbol, direction, tranche_num, str(ticket),
                      lot, price, sl, tp, pips_from_first))
        except Exception as e:
            log.warning(f"[PYRAMID] DB record error: {e}")

    def get_summary(self) -> dict:
        """Return summary of all active pyramids."""
        summary = {}
        for sym, session in self.sessions.items():
            summary[sym] = {
                "direction": session.direction,
                "tranches": session.tranche_count,
                "total_lot": session.total_lot,
                "avg_entry": round(session.avg_entry_price, 2),
                "first_entry": session.first_entry_price,
                "elapsed_s": int(time.time() - session.started_at),
            }
        return summary


if __name__ == "__main__":
    mgr = PyramidManager()
    print(f"Active pyramids: {mgr.get_summary()}")
