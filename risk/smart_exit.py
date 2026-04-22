"""
smart_exit.py — Smart Exit Manager (Adaptive TP/SL & Momentum-Reversal Exits)

Makes intelligent exit decisions when trades aren't hitting TP:
  1. Partial close at intermediate profit levels (lock in gains)
  2. Momentum-reversal detection (exit if indicators flip against you)
  3. Time-based decay (old trades with small profit → close)
  4. Breakeven stop-loss (move SL to entry when in profit)
  5. TP hit-rate tracking (auto-adjust TP ratios over time)

This module is the "brain" that prevents the bot from:
  - Holding losers too long
  - Watching winners turn into losers
  - Staying in trades when momentum dies
"""

import json
import os
import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.ai_client import ask_openrouter  # unified OpenRouter client (T1 → T2)
from core.paths import DATA_DIR


log = logging.getLogger("smart_exit")

DB_PATH = DATA_DIR / "trade_memory.db"

# ── Smart Exit Configuration ─────────────────────────────────────────────────
EXIT_CFG = {
    # Profit taking: let winners run with trailing stops
    "partial_close_pips":      50,       # close 1/3 at +50 pips (let rest run)
    "partial_close_fraction":  0.33,    # close 1/3 of position

    # Breakeven: protect entry after reasonable profit
    "breakeven_trigger_pips":  15,       # move SL to breakeven at +15 pips
    "breakeven_buffer_pips":   1.0,     # SL at entry + 1.0 pip

    # Momentum reversal: detect reversals
    "reversal_check_enabled":  True,
    "reversal_min_factors":    3,       # 3+ factors against = reversal

    # Time decay: give trades time to develop
    "max_trade_age_hours":     4,       # max 4 hours for a trade
    "stale_min_profit_pips":   10,      # if <10 pips after 2hr, consider closing
    "stale_check_hours":       2.0,     # check staleness after 2hr

    # Trailing stop: wider trailing for larger moves
    "trailing_start_pips":     30,       # start trailing at +30 pips
    "trailing_distance_pips":  10,      # trail 10 pips behind price

    # AI confirmation for exits — keep disabled for speed
    "ai_confirm_exits":        False,
    "ai_timeout":              10,

    # Loss cut: cut losses much earlier
    "loss_cut_pips":           82,       # close 50% at -82 pips (much larger stop for volatile market)
    "loss_cut_fraction":       0.5,      # close 50% of position on loss
    "loss_time_cut_minutes":   15,       # close all if negative after 15 min

    # Winner protection: protect larger profits
    "winner_peak_pips":        30,       # if trade was ever +30 pips...
    "winner_floor_pips":       15,       # ...and drops below +15 pip, close
}


# ── Ensure tracking table ────────────────────────────────────────────────────
def _ensure_tables():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS smart_exits (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                ticket      TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                exit_type   TEXT NOT NULL,
                profit_pips REAL,
                profit_usd  REAL,
                reason      TEXT,
                ai_input    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tp_tracking (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                tp_pips     REAL,
                max_pips    REAL,
                hit_tp      INTEGER DEFAULT 0,
                actual_close_pips REAL
            )
        """)


def _record_exit(ticket, symbol, direction, exit_type, pips, usd, reason):
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                INSERT INTO smart_exits (ts, ticket, symbol, direction, exit_type,
                    profit_pips, profit_usd, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, str(ticket), symbol, direction, exit_type, pips, usd, reason))
    except Exception as e:
        log.warning(f"[SMART EXIT] Failed to record exit: {e}")

    # Also record in trade memory so self-improver has complete data
    try:
        from memory import TradeMemory
        mem = TradeMemory()
        outcome = "WIN" if (usd or 0) > 0 else "LOSS"
        mem.record_outcome(str(ticket), 0.0, round(pips, 1), outcome)
    except Exception as e:
        log.debug(f"[SMART EXIT] Memory record_outcome failed: {e}")


# ── Smart Exit Manager ───────────────────────────────────────────────────────

class SmartExitManager:
    """
    Evaluates open positions and makes smart exit decisions.
    Called each cycle from continuous_trader.py, BEFORE the normal close checks.
    """

    def __init__(self):
        _ensure_tables()
        self._partial_closed = set()   # tickets already partially closed
        self._breakeven_set  = set()   # tickets with SL moved to breakeven
        self._loss_cut_set   = set()   # tickets already loss-cut (50%)
        self._peak_pips      = {}      # ticket -> peak profit in pips
        self._last_reversal_check = {}  # ticket -> last reversal check time
        log.info("[SMART EXIT] Smart exit manager initialized")

    def evaluate_exits(self, bridge, positions_by_sym: dict, symbols: dict,
                       account_balance: float, state: dict,
                       dry_run: bool = False) -> list:
        """
        Main entry point. Checks all open positions for smart exit opportunities.
        Returns list of closed/modified tickets.
        """
        actions = []

        for display_name, sym_cfg in symbols.items():
            broker_sym = sym_cfg["broker"]
            pip        = sym_cfg["pip"]
            positions  = positions_by_sym.get(broker_sym, [])

            for pos in positions:
                ticket     = str(getattr(pos, "ticket", "?"))
                pos_type   = getattr(pos, "type", 1)
                direction  = "BUY" if pos_type == 0 else "SELL"
                volume     = getattr(pos, "volume", 0.01)
                open_price = getattr(pos, "price_open", 0.0)
                profit     = getattr(pos, "profit", 0.0)
                current_sl = getattr(pos, "sl", 0.0)
                current_tp = getattr(pos, "tp", 0.0)
                open_time  = getattr(pos, "time", None)

                # Calculate profit in pips using contract_size for correct pip value
                # pip_value_per_lot = pip * contract_size  (e.g. gold: 0.10*100=$10/pip/lot)
                contract_size = sym_cfg.get("contract_size", 100)
                _pip_val = pip * contract_size * volume
                profit_pips = profit / _pip_val if _pip_val > 0 else 0

                # Track peak pips for winner protection
                prev_peak = self._peak_pips.get(ticket, 0)
                if profit_pips > prev_peak:
                    self._peak_pips[ticket] = profit_pips

                # 0a. LOSS CUT — aggressive loss protection
                lc_action = self._check_loss_cut(
                    bridge, ticket, display_name, direction,
                    profit, profit_pips, volume, open_time, dry_run
                )
                if lc_action:
                    self._update_stats(state, profit, profit_pips)
                    actions.append(lc_action)
                    # Any loss cut action fully closes the position — skip all other checks
                    if "dry" in lc_action.get("action", "") or lc_action.get("action") in (
                        "loss_cut_full", "loss_time_cut", "loss_cut_50pct"
                    ):
                        continue

                # 0b. WINNER PROTECTION — never let a winner become a loser
                wp_action = self._check_winner_protection(
                    bridge, ticket, display_name, direction,
                    profit, profit_pips, dry_run
                )
                if wp_action:
                    self._update_stats(state, profit, profit_pips)
                    actions.append(wp_action)
                    continue  # position closed

                # 1. BREAKEVEN STOP — move SL to entry when in profit
                be_action = self._check_breakeven(
                    bridge, ticket, display_name, direction,
                    open_price, current_sl, profit_pips, pip, dry_run
                )
                if be_action:
                    actions.append(be_action)

                # 2. TIME DECAY — close stale trades
                td_action = self._check_time_decay(
                    bridge, ticket, display_name, direction,
                    profit, profit_pips, open_time, dry_run
                )
                if td_action:
                    self._update_stats(state, profit, profit_pips)
                    actions.append(td_action)
                    continue  # position closed, skip other checks

                # 3. MOMENTUM REVERSAL — exit if indicators flip
                if EXIT_CFG["reversal_check_enabled"]:
                    rev_action = self._check_momentum_reversal(
                        bridge, ticket, display_name, direction,
                        profit, profit_pips, sym_cfg, dry_run
                    )
                    if rev_action:
                        self._update_stats(state, profit, profit_pips)
                        actions.append(rev_action)
                        continue

                # 4. TRAILING STOP — tighten SL as profit grows
                trail_action = self._check_trailing_stop(
                    bridge, ticket, display_name, direction,
                    open_price, current_sl, profit_pips, pip, sym_cfg, dry_run
                )
                if trail_action:
                    actions.append(trail_action)

        return actions

    # ── 1. Breakeven Stop ────────────────────────────────────────────────────

    def _check_breakeven(self, bridge, ticket, symbol, direction,
                         open_price, current_sl, profit_pips, pip, dry_run):
        """Move SL to entry + buffer when profit exceeds threshold."""
        if ticket in self._breakeven_set:
            return None

        if profit_pips < EXIT_CFG["breakeven_trigger_pips"]:
            return None

        buffer = EXIT_CFG["breakeven_buffer_pips"] * pip
        digits = 2 if pip >= 0.01 else 5

        if direction == "BUY":
            new_sl = round(open_price + buffer, digits)
            if current_sl >= new_sl:  # SL already at or past breakeven
                self._breakeven_set.add(ticket)
                return None
        else:  # SELL
            new_sl = round(open_price - buffer, digits)
            if current_sl != 0 and current_sl <= new_sl:
                self._breakeven_set.add(ticket)
                return None

        log.info(f"[SMART EXIT] 🛡️ {symbol} #{ticket}: Moving SL to breakeven "
                 f"({current_sl} → {new_sl}, profit={profit_pips:.1f}pips)")

        if not dry_run:
            try:
                bridge.modify_position(ticket, sl=new_sl)
                self._breakeven_set.add(ticket)
                return {"ticket": ticket, "action": "breakeven", "new_sl": new_sl}
            except Exception as e:
                log.warning(f"[SMART EXIT] Breakeven modify failed: {e}")
                return None
        else:
            self._breakeven_set.add(ticket)
            return {"ticket": ticket, "action": "breakeven_dry", "new_sl": new_sl}

    # ── 2. Time Decay ────────────────────────────────────────────────────────

    def _check_time_decay(self, bridge, ticket, symbol, direction,
                          profit, profit_pips, open_time, dry_run):
        """Close stale trades that have been open too long with little profit."""
        if open_time is None:
            return None

        try:
            if isinstance(open_time, (int, float)):
                open_dt = datetime.fromtimestamp(open_time, tz=timezone.utc)
            elif isinstance(open_time, str):
                open_dt = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
            else:
                open_dt = open_time
                if open_dt.tzinfo is None:
                    open_dt = open_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

        now = datetime.now(timezone.utc)
        age_hours = (now - open_dt).total_seconds() / 3600

        # Max age: force close
        if age_hours >= EXIT_CFG["max_trade_age_hours"]:
            reason = (f"Trade open {age_hours:.1f}h (max {EXIT_CFG['max_trade_age_hours']}h), "
                      f"profit={profit_pips:.1f}pips — closing stale trade")
            log.info(f"[SMART EXIT] ⏰ {symbol} #{ticket}: {reason}")

            if not dry_run:
                try:
                    bridge.close_position(ticket)
                    _record_exit(ticket, symbol, direction, "TIME_DECAY_MAX",
                                 profit_pips, profit, reason)
                    return {"ticket": ticket, "action": "time_decay", "reason": reason}
                except Exception as e:
                    log.warning(f"[SMART EXIT] Time decay close failed: {e}")
            else:
                return {"ticket": ticket, "action": "time_decay_dry", "reason": reason}

        # Stale check: if trade is old and barely profitable
        if age_hours >= EXIT_CFG["stale_check_hours"]:
            if 0 < profit_pips < EXIT_CFG["stale_min_profit_pips"]:
                reason = (f"Trade stale: {age_hours:.1f}h old, only {profit_pips:.1f}pips "
                          f"(need {EXIT_CFG['stale_min_profit_pips']}+ pips by now)")
                log.info(f"[SMART EXIT] 😴 {symbol} #{ticket}: {reason}")

                if not dry_run:
                    try:
                        bridge.close_position(ticket)
                        _record_exit(ticket, symbol, direction, "TIME_DECAY_STALE",
                                     profit_pips, profit, reason)
                        return {"ticket": ticket, "action": "stale_close", "reason": reason}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Stale close failed: {e}")
                else:
                    return {"ticket": ticket, "action": "stale_close_dry", "reason": reason}

        return None

    # ── 3. Momentum Reversal ─────────────────────────────────────────────────

    def _check_momentum_reversal(self, bridge, ticket, symbol, direction,
                                  profit, profit_pips, sym_cfg, dry_run):
        """
        Exit if market indicators have flipped against the position's direction.
        Only acts when position is near breakeven or in small profit
        (avoids killing big winners too early).
        """
        # Only check every 2 minutes per ticket
        now = time.time()
        last_check = self._last_reversal_check.get(ticket, 0)
        if now - last_check < 120:
            return None
        self._last_reversal_check[ticket] = now

        # Don't close positions with significant unrealized loss (let SL handle it)
        if profit_pips < -3:
            return None

        # Don't close big winners — momentum reversal is for mediocre trades
        if profit_pips > 15:
            return None

        # Ask AI for momentum assessment
        if not EXIT_CFG["ai_confirm_exits"]:
            return None

        try:
            ai_says_exit, ai_reason = self._ai_reversal_check(
                symbol, direction, profit_pips, sym_cfg
            )

            if ai_says_exit:
                reason = f"Momentum reversal: {ai_reason} (profit={profit_pips:.1f}pips)"
                log.info(f"[SMART EXIT] 🔄 {symbol} #{ticket}: {reason}")

                if not dry_run:
                    try:
                        bridge.close_position(ticket)
                        _record_exit(ticket, symbol, direction, "MOMENTUM_REVERSAL",
                                     profit_pips, profit, reason)
                        return {"ticket": ticket, "action": "reversal_close", "reason": reason}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Reversal close failed: {e}")
                else:
                    return {"ticket": ticket, "action": "reversal_close_dry", "reason": reason}

        except Exception as e:
            log.debug(f"[SMART EXIT] Reversal check error: {e}")

        return None

    # ── 4. Trailing Stop ─────────────────────────────────────────────────────

    def _check_trailing_stop(self, bridge, ticket, symbol, direction,
                              open_price, current_sl, profit_pips, pip,
                              sym_cfg, dry_run):
        """Tighten SL as profit grows — lock in gains progressively."""
        if profit_pips < EXIT_CFG["trailing_start_pips"]:
            return None

        trail_distance = EXIT_CFG["trailing_distance_pips"] * pip
        digits = 2 if pip >= 0.01 else 5

        try:
            tick = bridge.get_tick(sym_cfg["broker"])
            current_price = tick.bid if direction == "BUY" else tick.ask
        except Exception:
            return None

        if direction == "BUY":
            ideal_sl = round(current_price - trail_distance, digits)
            if ideal_sl > current_sl and ideal_sl > open_price:
                log.info(f"[SMART EXIT] 📈 {symbol} #{ticket}: Trailing SL "
                         f"{current_sl} → {ideal_sl} (profit={profit_pips:.1f}pips)")
                if not dry_run:
                    try:
                        bridge.modify_position(ticket, sl=ideal_sl)
                        return {"ticket": ticket, "action": "trailing_stop", "new_sl": ideal_sl}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Trailing modify failed: {e}")
        else:  # SELL
            ideal_sl = round(current_price + trail_distance, digits)
            if current_sl == 0 or (ideal_sl < current_sl and ideal_sl < open_price):
                log.info(f"[SMART EXIT] 📉 {symbol} #{ticket}: Trailing SL "
                         f"{current_sl} → {ideal_sl} (profit={profit_pips:.1f}pips)")
                if not dry_run:
                    try:
                        bridge.modify_position(ticket, sl=ideal_sl)
                        return {"ticket": ticket, "action": "trailing_stop", "new_sl": ideal_sl}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Trailing modify failed: {e}")

        return None

    # ── AI Reversal Check ────────────────────────────────────────────────────

    def _ai_reversal_check(self, symbol: str, direction: str,
                            profit_pips: float, sym_cfg: dict) -> tuple:
        """
        Ask OpenRouter: has momentum reversed against our position?
        Returns (should_exit: bool, reason: str)
        """
        prompt = f"""You are monitoring an open {direction} trade on {symbol}.
Current unrealized profit: {profit_pips:.1f} pips.

The trade has been open and is showing signs of stalling or reversing.
Should we EXIT this position NOW to protect our capital?

Consider:
- If momentum has reversed against our {direction} position
- If key support/resistance levels have been broken against us
- If the profit is small and likely to evaporate
- If staying in the trade has negative expected value

Be DECISIVE. Small profits are better than turning a winner into a loser.

Respond with ONLY JSON (no markdown):
{{"exit": true/false, "reason": "1 sentence explanation"}}"""

        data = ask_openrouter(
            messages=[
                {"role": "system", "content":
                 "You protect trading capital. When in doubt, EXIT the trade. "
                 "A small profit is infinitely better than a loss."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=64,
            label=f"exit-{symbol}",
        )

        if data:
            should_exit = bool(data.get("exit", False))
            reason = data.get("reason", "AI decision")
            return should_exit, reason

        # Fallback: if profit is < 2 pips and positive, protect it
        log.debug(f"[SMART EXIT] AI reversal check returned no data — using fallback")
        if 0 < profit_pips < 2:
            return True, "Fallback: tiny profit, protecting capital"
        return False, ""

    # ── 0a. Loss Cut ────────────────────────────────────────────────────────

    def _check_loss_cut(self, bridge, ticket, symbol, direction,
                        profit, profit_pips, volume, open_time, dry_run):
        """Aggressive loss protection: cut 50% at -3 pips, close all after 5 min if negative."""

        # Rule 1: If negative after X minutes, close everything
        if open_time is not None and profit_pips < 0:
            try:
                if isinstance(open_time, (int, float)):
                    open_dt = datetime.fromtimestamp(open_time, tz=timezone.utc)
                elif isinstance(open_time, str):
                    open_dt = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
                else:
                    open_dt = open_time
                    if open_dt.tzinfo is None:
                        open_dt = open_dt.replace(tzinfo=timezone.utc)

                age_minutes = (datetime.now(timezone.utc) - open_dt).total_seconds() / 60
                if age_minutes >= EXIT_CFG["loss_time_cut_minutes"]:
                    reason = (f"Loss time cut: {profit_pips:.1f} pips after {age_minutes:.0f}min "
                              f"— closing to protect capital")
                    log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
                    if not dry_run:
                        try:
                            bridge.close_position(ticket)
                            _record_exit(ticket, symbol, direction, "LOSS_TIME_CUT",
                                         profit_pips, profit, reason)
                            return {"ticket": ticket, "action": "loss_time_cut", "reason": reason}
                        except Exception as e:
                            log.warning(f"[SMART EXIT] Loss time cut failed: {e}")
                    else:
                        return {"ticket": ticket, "action": "loss_time_cut_dry", "reason": reason}
            except Exception:
                pass

        # Rule 2: If hits loss threshold, reduce exposure
        if profit_pips <= -EXIT_CFG["loss_cut_pips"] and ticket not in self._loss_cut_set:
            min_lot = 0.01
            close_vol = round(volume * EXIT_CFG["loss_cut_fraction"], 2)

            # If position is at minimum lot OR partial vol < min_lot, do full close
            if volume <= min_lot or close_vol < min_lot:
                reason = (f"Loss cut (full close — min lot): {profit_pips:.1f} pips "
                          f"— closing entire {volume} lot position")
                log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
                if not dry_run:
                    try:
                        ok = bridge.close_position(ticket)
                        if ok:
                            self._loss_cut_set.add(ticket)
                            _record_exit(ticket, symbol, direction, "LOSS_CUT_FULL",
                                         profit_pips, profit, reason)
                            return {"ticket": ticket, "action": "loss_cut_full", "reason": reason}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Loss cut full close failed: {e}")
                else:
                    self._loss_cut_set.add(ticket)
                    return {"ticket": ticket, "action": "loss_cut_full_dry", "reason": reason}
            else:
                reason = (f"Loss cut 50%: {profit_pips:.1f} pips — reducing exposure "
                          f"(closing {close_vol} of {volume})")
                log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
                if not dry_run:
                    try:
                        bridge.close_position_partial(ticket, close_vol)
                        self._loss_cut_set.add(ticket)
                        _record_exit(ticket, symbol, direction, "LOSS_CUT_50PCT",
                                     profit_pips, profit, reason)
                        return {"ticket": ticket, "action": "loss_cut_50pct", "reason": reason}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Loss cut partial failed: {e}")
                else:
                    self._loss_cut_set.add(ticket)
                    return {"ticket": ticket, "action": "loss_cut_50pct_dry", "reason": reason}

        return None

    # ── 0b. Winner Protection ───────────────────────────────────────────────

    def _check_winner_protection(self, bridge, ticket, symbol, direction,
                                  profit, profit_pips, dry_run):
        """Never let a winner become a loser. If trade was +3 pips and drops below +1, close."""
        peak = self._peak_pips.get(ticket, 0)

        if peak >= EXIT_CFG["winner_peak_pips"] and profit_pips < EXIT_CFG["winner_floor_pips"]:
            reason = (f"Winner protection: was +{peak:.1f} pips, now +{profit_pips:.1f} pips "
                      f"— closing to lock remaining profit")
            log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
            if not dry_run:
                try:
                    bridge.close_position(ticket)
                    _record_exit(ticket, symbol, direction, "WINNER_PROTECTION",
                                 profit_pips, profit, reason)
                    # Cleanup peak tracking
                    self._peak_pips.pop(ticket, None)
                    return {"ticket": ticket, "action": "winner_protection", "reason": reason}
                except Exception as e:
                    log.warning(f"[SMART EXIT] Winner protection close failed: {e}")
            else:
                self._peak_pips.pop(ticket, None)
                return {"ticket": ticket, "action": "winner_protection_dry", "reason": reason}

        return None

    # ── Stats Helper ─────────────────────────────────────────────────────────

    def _update_stats(self, state: dict, profit: float, profit_pips: float):
        """Update win/loss counters after a smart exit."""
        state["total_trades"] = state.get("total_trades", 0) + 1
        if profit > 0 or profit_pips > 0:
            state["wins"] = state.get("wins", 0) + 1
        else:
            state["losses"] = state.get("losses", 0) + 1

    # ── Performance Report ───────────────────────────────────────────────────

    def get_exit_stats(self) -> dict:
        """Return smart exit performance stats."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM smart_exits ORDER BY id DESC LIMIT 50").fetchall()
                if not rows:
                    return {"total": 0}

                total = len(rows)
                by_type = {}
                total_pips = 0
                total_usd = 0

                for r in rows:
                    et = r["exit_type"]
                    by_type[et] = by_type.get(et, 0) + 1
                    total_pips += r["profit_pips"] or 0
                    total_usd += r["profit_usd"] or 0

                return {
                    "total": total,
                    "by_type": by_type,
                    "total_pips": round(total_pips, 1),
                    "total_usd": round(total_usd, 2),
                    "avg_pips": round(total_pips / total, 1) if total > 0 else 0,
                }
        except Exception:
            return {"total": 0}


if __name__ == "__main__":
    mgr = SmartExitManager()
    stats = mgr.get_exit_stats()
    print(f"Smart exit stats: {json.dumps(stats, indent=2)}")
