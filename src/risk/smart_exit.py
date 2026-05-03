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
# CALIBRATED 2026-04-28 from 30-day live data:
#   - 30-day avg win $7.03 vs avg loss $14.36 → R:R 0.49 (need 1.0+ to be profitable)
#   - Tiny wins ($0.08, $0.10) from breakeven SL closing on micro-pullbacks
#   - Single position lost -$533 (no per-position stop)
EXIT_CFG = {
    # ═══════════════════════════════════════════════════════════════
    # R:R FIX (2026-05-03)
    # 30-day live data: avg win $5.56 (5.6 pips) vs avg loss $11.19 (11.2 pips)
    # = R:R 0.50 (need 67% WR to break even, have 57%).
    # ROOT CAUSE: trailing_start at 35p never fires (avg win is 5.6p),
    # so winners close early via other exits. Lowering to 20p.
    # Also: USD cap ($30) fires before SL (35p=$35) → SL is irrelevant.
    # ═══════════════════════════════════════════════════════════════

    # Profit taking — partial close at 60 pips (was 80 — too high)
    "partial_close_pips":      60,
    "partial_close_fraction":  0.33,

    # Breakeven — DISABLED (backtest avg -1 pip per BE exit = useless)
    "breakeven_trigger_pips":  999,      # effectively disabled
    "breakeven_buffer_pips":   10.0,

    # Momentum reversal — DISABLED (AI too slow for live exit decisions)
    "reversal_check_enabled":  False,
    "reversal_min_factors":    3,

    # Time decay — relaxed to let winners develop
    "max_trade_age_hours":     72,       # 3 days max (unchanged)
    "stale_min_profit_pips":   999,      # disabled
    "stale_check_hours":       12.0,

    # Trailing stop — THE PROFIT ENGINE
    # FIX: start was 35p but avg win was only 5.6p → trail NEVER activated.
    # Lowered to 20p so more winners get trail protection.
    "trailing_start_pips":     20,       # 35→20: activate trail much earlier
    "trailing_distance_pips":  8,        # 20→8: default for ranging (ADX<22)

    # AI confirmation — disabled (slow + adds variance)
    "ai_confirm_exits":        False,
    "ai_timeout":              10,

    # Loss cut — calibrated to not interfere with SL
    # FIX: USD cap was $30 but SL is 35 pips = $35 on 0.01 lot gold.
    # The USD cap was firing BEFORE the SL, negating it entirely.
    "loss_cut_pips":           30,
    "loss_cut_fraction":       0.5,
    "loss_time_cut_minutes":   180,      # 60→180: gold consolidates for hours before moving
    "max_position_loss_usd":   50,       # 30→50: don't fire before SL (35p=$35 on 0.01 lot)

    # Winner protection — DISABLED (let trail handle profit)
    "winner_peak_pips":        999,      # disabled
    "winner_floor_pips":       25,

    # Profit floor — DISABLED (let trail do the work)
    "min_lock_pips":           999,
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
        self._negative_since = {}      # ticket -> datetime when we first saw it negative
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
            if positions:
                log.debug(f"[SMART EXIT] checking {len(positions)} position(s) for {display_name}")

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
                    profit, profit_pips, volume, dry_run
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
                    profit, profit_pips, open_time, dry_run,
                    open_price=open_price, current_sl=current_sl, pip=pip
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

                # 4. TRAILING STOP — tighten SL as profit grows (ADX-adaptive distance)
                _adx_for_trail = sym_cfg.get("_last_adx", 0.0)
                trail_action = self._check_trailing_stop(
                    bridge, ticket, display_name, direction,
                    open_price, current_sl, profit_pips, pip, sym_cfg, dry_run,
                    adx=_adx_for_trail,
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
                          profit, profit_pips, open_time, dry_run,
                          open_price: float = 0.0, current_sl: float = 0.0,
                          pip: float = 0.10):
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
            # Dynamic minimum: require at least 60% of SL distance as profit before stale exit
            # This prevents exiting winners for pennies when the risk was large
            _sl_dist_pips = abs(open_price - current_sl) / pip if (current_sl > 0 and pip > 0) else 0
            _dynamic_min = max(_sl_dist_pips * 0.60, EXIT_CFG["stale_min_profit_pips"])
            if 0 < profit_pips < _dynamic_min:
                reason = (f"Trade stale: {age_hours:.1f}h old, only {profit_pips:.1f}pips "
                          f"(need {_dynamic_min:.0f}+ pips, SL={_sl_dist_pips:.0f}p away)")
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
                              sym_cfg, dry_run, adx: float = 0.0):
        """Tighten SL as profit grows — lock in gains progressively.

        Adaptive trail distance:
          - ADX ≥ 30 (strong trend): trail 20 pips → let winners run further
          - ADX ≥ 22 (normal trend): trail 12 pips
          - ADX < 22 (ranging):      trail 8 pips → tighten fast in chop
        Backtest shows TRAIL is the #1 P&L driver — widening in strong trends
        captures the tail of large moves without giving back more than needed.
        """
        if profit_pips < EXIT_CFG["trailing_start_pips"]:
            return None

        if adx >= 30:
            trail_pips = 18   # strong trend — give room to breathe (was 20)
        elif adx >= 22:
            trail_pips = 12   # normal trend (unchanged)
        else:
            trail_pips = EXIT_CFG["trailing_distance_pips"]   # ranging: 8p (was 20)

        trail_distance = trail_pips * pip
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
                        profit, profit_pips, volume, dry_run):
        """Aggressive loss protection: hard USD cap, pip threshold, and time-based cut."""

        # Rule 0: Hard USD cap — kill any position losing more than $30
        # 30-day data showed one position lost -$533 with no per-trade brake.
        max_usd_loss = EXIT_CFG.get("max_position_loss_usd", 30)
        if profit <= -max_usd_loss and ticket not in self._loss_cut_set:
            reason = (f"USD hard cap: ${profit:.2f} ≤ -${max_usd_loss} "
                      f"— emergency close (kills runaway losses)")
            log.warning(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
            if not dry_run:
                try:
                    bridge.close_position(ticket)
                    self._loss_cut_set.add(ticket)
                    _record_exit(ticket, symbol, direction, "USD_CAP",
                                 profit_pips, profit, reason)
                    return {"ticket": ticket, "action": "usd_cap", "reason": reason}
                except Exception as e:
                    log.warning(f"[SMART EXIT] USD cap close failed: {e}")
            else:
                self._loss_cut_set.add(ticket)
                return {"ticket": ticket, "action": "usd_cap_dry", "reason": reason}

        # Rule 0b: Profit floor — once trade peaked above min_lock_pips, never let it close below
        peak = self._peak_pips.get(ticket, 0)
        min_lock = EXIT_CFG.get("min_lock_pips", 5)
        if peak >= min_lock and profit_pips < min_lock and profit_pips > 0:
            reason = (f"Profit floor: peaked at +{peak:.1f} pips, now +{profit_pips:.1f} "
                      f"— locking minimum {min_lock} pips")
            log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
            if not dry_run:
                try:
                    bridge.close_position(ticket)
                    _record_exit(ticket, symbol, direction, "PROFIT_FLOOR",
                                 profit_pips, profit, reason)
                    return {"ticket": ticket, "action": "profit_floor", "reason": reason}
                except Exception as e:
                    log.warning(f"[SMART EXIT] Profit floor close failed: {e}")
            else:
                return {"ticket": ticket, "action": "profit_floor_dry", "reason": reason}

        # Rule 1: If negative for X continuous minutes (tracked by us, not broker time)
        # We use our own internal clock so broker timezone differences don't matter.
        now_utc = datetime.now(timezone.utc)
        if profit_pips < 0:
            if ticket not in self._negative_since:
                self._negative_since[ticket] = now_utc
            neg_minutes = (now_utc - self._negative_since[ticket]).total_seconds() / 60
            if neg_minutes >= EXIT_CFG["loss_time_cut_minutes"]:
                reason = (f"Loss time cut: {profit_pips:.1f} pips for {neg_minutes:.0f}min "
                          f"— closing to protect capital")
                log.info(f"[SMART EXIT] {symbol} #{ticket}: {reason}")
                if not dry_run:
                    try:
                        bridge.close_position(ticket)
                        _record_exit(ticket, symbol, direction, "LOSS_TIME_CUT",
                                     profit_pips, profit, reason)
                        self._negative_since.pop(ticket, None)
                        return {"ticket": ticket, "action": "loss_time_cut", "reason": reason}
                    except Exception as e:
                        log.warning(f"[SMART EXIT] Loss time cut failed: {e}")
                else:
                    self._negative_since.pop(ticket, None)
                    return {"ticket": ticket, "action": "loss_time_cut_dry", "reason": reason}
        else:
            # Position went positive — reset the negative timer
            self._negative_since.pop(ticket, None)

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
