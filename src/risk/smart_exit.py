"""
smart_exit.py — Smart Exit Manager (Consolidated Exit Logic)

REWRITE 2026-05-04: replaces 7 overlapping rules with 4 clean ones.

Old design had USD cap, pip-based loss cut, loss-time-cut, breakeven,
winner protection, profit floor, momentum reversal, partial close, and
trailing stop — many of which fired before the broker SL or duplicated
each other. Net effect: tiny wins ($5.56 avg) with full-SL losses
($11.19 avg) → R:R 0.50, mathematically guaranteed losses.

New design is a single ordered hierarchy. The broker SL is the source
of truth for losses. We add only what it cannot do:

  1. CATASTROPHIC BACKSTOP — fires only if broker SL failed (very wide).
  2. TIME-BASED CLOSE      — max age and stale-progress check.
  3. PROFIT LOCK           — close if peak ≥ X pips and gave back to ≤ Y.
  4. TRAILING STOP         — adaptive ADX-based, activates near breakeven.

Each ticket evaluates exactly one terminal action per cycle (early
return). Trailing-stop modifications are non-terminal (just tighten SL).
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from core.logger_factory import get_logger
from core.paths import DATA_DIR

log = get_logger("smart_exit")

DB_PATH = DATA_DIR / "trade_memory.db"

# ── Exit Configuration ───────────────────────────────────────────────────────
# All thresholds in pips unless suffixed _usd, _hours, _minutes.
# Pip values are symbol-agnostic; conversion to USD uses contract_size×volume.
EXIT_CFG = {
    # ── 1. Catastrophic backstop ─────────────────────────────────────────────
    # Only fires if the broker SL was never placed or was rejected.
    # Set wide enough that a normal SL hit ($35 on 0.01 lot gold) never
    # triggers it.
    "catastrophic_loss_usd":   100,

    # ── 2. Time-based close ──────────────────────────────────────────────────
    "max_trade_age_hours":     48,    # was 72 — stale trades rarely come back
    "stale_check_hours":       8,     # check progress at 8h
    "stale_min_pip_fraction":  0.25,  # need ≥ 25% of SL distance as profit

    # ── 3. Profit lock (peak retracement) ────────────────────────────────────
    # Replaces the old winner_peak / winner_floor / min_lock / profit_floor.
    "lock_peak_pips":          15,    # arm once we hit +15 pips
    "lock_giveback_pips":      8,     # close if pulled back to ≤ +8 pips

    # ── 4. Trailing stop (the profit engine) ─────────────────────────────────
    # Activates near breakeven so winners actually get a trail. Distance
    # scales with ADX so chop tightens fast and trends get room.
    "trail_activate_pips":     12,    # was 35 — avg win is 5.6p, 35 never fires
    "trail_distance_chop":     8,     # ADX < 22
    "trail_distance_normal":   12,    # ADX 22–30 (from old config)
    "trail_distance_strong":   18,    # ADX > 30 — let runners breathe

    # ── Legacy keys (kept for test/dashboard compatibility, unused) ──────────
    "loss_cut_pips":           30,    # legacy; SL is single source of truth now
    "trailing_start_pips":     12,    # alias for trail_activate_pips
}


# ── DB helpers ───────────────────────────────────────────────────────────────

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
        log.warning(f"[SMART EXIT] DB record failed: {e}")

    try:
        from learning.memory import TradeMemory
        outcome = "WIN" if (usd or 0) > 0 else "LOSS"
        TradeMemory().record_outcome(str(ticket), 0.0, round(pips or 0, 1), outcome)
    except Exception as e:
        log.debug(f"[SMART EXIT] Memory record_outcome failed: {e}")


# ── Smart Exit Manager ───────────────────────────────────────────────────────

class SmartExitManager:
    """
    Per-cycle exit evaluator. Called from continuous_trader.py before
    the standard close checks. Closes/modifies positions that need
    intervention beyond the broker's static SL/TP.
    """

    def __init__(self):
        _ensure_tables()
        self._peak_pips     = {}    # ticket -> peak profit in pips
        self._lock_armed    = set() # tickets that have crossed lock_peak_pips
        self.closed_tickets = set() # exposed for double-count prevention
        log.info("[SMART EXIT] Manager initialized (consolidated exit logic v2)")

    # ── Public API ──────────────────────────────────────────────────────────

    def evaluate_exits(self, bridge, positions_by_sym: dict, symbols: dict,
                       account_balance: float, state: dict,
                       dry_run: bool = False) -> list:
        """
        Walk every open position and apply the exit hierarchy.
        Returns a list of action dicts for logging/state updates.
        """
        actions = []

        for display_name, sym_cfg in symbols.items():
            broker_sym = sym_cfg["broker"]
            pip        = sym_cfg["pip"]
            positions  = positions_by_sym.get(broker_sym, [])

            for pos in positions:
                action = self._evaluate_one(
                    bridge, pos, display_name, sym_cfg, pip, state, dry_run
                )
                if action:
                    actions.append(action)

        return actions

    def get_exit_stats(self) -> dict:
        """Return last-50 smart-exit performance stats."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM smart_exits ORDER BY id DESC LIMIT 50"
                ).fetchall()
                if not rows:
                    return {"total": 0}

                total = len(rows)
                by_type = {}
                total_pips = total_usd = 0.0
                for r in rows:
                    et = r["exit_type"]
                    by_type[et] = by_type.get(et, 0) + 1
                    total_pips += r["profit_pips"] or 0
                    total_usd  += r["profit_usd"]  or 0

                return {
                    "total":      total,
                    "by_type":    by_type,
                    "total_pips": round(total_pips, 1),
                    "total_usd":  round(total_usd, 2),
                    "avg_pips":   round(total_pips / total, 1) if total else 0,
                }
        except Exception as e:
            log.debug(f"[SMART EXIT] stats query failed: {e}")
            return {"total": 0}

    # ── Per-position evaluation ─────────────────────────────────────────────

    def _evaluate_one(self, bridge, pos, symbol, sym_cfg, pip, state, dry_run):
        """Apply the 4-rule hierarchy to a single position."""
        ticket     = str(getattr(pos, "ticket", "?"))
        pos_type   = getattr(pos, "type", 1)
        direction  = "BUY" if pos_type == 0 else "SELL"
        volume     = getattr(pos, "volume", 0.01)
        open_price = getattr(pos, "price_open", 0.0)
        profit_usd = getattr(pos, "profit", 0.0)
        current_sl = getattr(pos, "sl", 0.0)
        open_time  = getattr(pos, "time", None)

        # Convert USD profit → pips using contract_size × volume.
        contract_size = sym_cfg.get("contract_size", 100)
        usd_per_pip   = pip * contract_size * volume
        profit_pips   = profit_usd / usd_per_pip if usd_per_pip > 0 else 0.0

        # Track peak for profit-lock arming.
        if profit_pips > self._peak_pips.get(ticket, 0):
            self._peak_pips[ticket] = profit_pips
        peak = self._peak_pips.get(ticket, 0)
        if peak >= EXIT_CFG["lock_peak_pips"]:
            self._lock_armed.add(ticket)

        # ── Rule 1: catastrophic backstop ────────────────────────────────────
        if profit_usd <= -EXIT_CFG["catastrophic_loss_usd"]:
            reason = (f"CATASTROPHIC: ${profit_usd:.2f} "
                      f"≤ -${EXIT_CFG['catastrophic_loss_usd']} "
                      f"(SL likely failed) — emergency close")
            return self._close(bridge, ticket, symbol, direction,
                               profit_pips, profit_usd, "CATASTROPHIC",
                               reason, state, dry_run)

        # ── Rule 2: time-based close ─────────────────────────────────────────
        time_action = self._check_time(
            bridge, ticket, symbol, direction,
            profit_pips, profit_usd, open_time,
            open_price, current_sl, pip, state, dry_run,
        )
        if time_action:
            return time_action

        # ── Rule 3: profit lock ──────────────────────────────────────────────
        if (ticket in self._lock_armed
                and profit_pips <= EXIT_CFG["lock_giveback_pips"]):
            reason = (f"PROFIT LOCK: peaked +{peak:.1f}p, now +{profit_pips:.1f}p "
                      f"(≤ +{EXIT_CFG['lock_giveback_pips']}p giveback)")
            return self._close(bridge, ticket, symbol, direction,
                               profit_pips, profit_usd, "PROFIT_LOCK",
                               reason, state, dry_run)

        # ── Rule 4: trailing stop (non-terminal — just tightens SL) ──────────
        return self._check_trail(
            bridge, ticket, symbol, direction,
            open_price, current_sl, profit_pips, pip, sym_cfg, dry_run,
        )

    # ── Rule 2 helper: time-based close ─────────────────────────────────────

    def _check_time(self, bridge, ticket, symbol, direction,
                    profit_pips, profit_usd, open_time,
                    open_price, current_sl, pip, state, dry_run):
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
        except Exception as e:
            log.debug(f"[SMART EXIT] time parse failed: {e}")
            return None

        age_hours = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600

        # 2a. Hard max age — close regardless of P&L.
        if age_hours >= EXIT_CFG["max_trade_age_hours"]:
            reason = (f"MAX AGE: {age_hours:.1f}h "
                      f"≥ {EXIT_CFG['max_trade_age_hours']}h, "
                      f"P&L={profit_pips:.1f}p")
            return self._close(bridge, ticket, symbol, direction,
                               profit_pips, profit_usd, "TIME_MAX",
                               reason, state, dry_run)

        # 2b. Stale check — old and not making meaningful progress.
        if age_hours >= EXIT_CFG["stale_check_hours"] and profit_pips > 0:
            sl_dist_pips = (abs(open_price - current_sl) / pip
                            if (current_sl > 0 and pip > 0) else 0)
            min_progress = sl_dist_pips * EXIT_CFG["stale_min_pip_fraction"]
            if min_progress > 0 and profit_pips < min_progress:
                reason = (f"STALE: {age_hours:.1f}h old, +{profit_pips:.1f}p "
                          f"(need +{min_progress:.0f}p, SL={sl_dist_pips:.0f}p)")
                return self._close(bridge, ticket, symbol, direction,
                                   profit_pips, profit_usd, "TIME_STALE",
                                   reason, state, dry_run)

        return None

    # ── Rule 4 helper: trailing stop ────────────────────────────────────────

    def _check_trail(self, bridge, ticket, symbol, direction,
                     open_price, current_sl, profit_pips, pip,
                     sym_cfg, dry_run):
        if profit_pips < EXIT_CFG["trail_activate_pips"]:
            return None

        adx = sym_cfg.get("_last_adx", 0.0)
        if adx >= 30:
            trail_pips = EXIT_CFG["trail_distance_strong"]
        elif adx >= 22:
            trail_pips = EXIT_CFG["trail_distance_normal"]
        else:
            trail_pips = EXIT_CFG["trail_distance_chop"]

        trail_dist = trail_pips * pip
        digits     = 2 if pip >= 0.01 else 5

        try:
            tick = bridge.get_tick(sym_cfg["broker"])
            current_price = tick.bid if direction == "BUY" else tick.ask
        except Exception as e:
            log.debug(f"[SMART EXIT] tick fetch failed: {e}")
            return None

        if direction == "BUY":
            new_sl = round(current_price - trail_dist, digits)
            improving = new_sl > current_sl and new_sl > open_price
        else:
            new_sl = round(current_price + trail_dist, digits)
            improving = (current_sl == 0) or (new_sl < current_sl and new_sl < open_price)

        if not improving:
            return None

        log.info(f"[SMART EXIT] 📈 {symbol} #{ticket}: trail SL "
                 f"{current_sl} → {new_sl} (+{profit_pips:.1f}p, ADX={adx:.0f})")
        if dry_run:
            return {"ticket": ticket, "action": "trailing_dry", "new_sl": new_sl}
        try:
            bridge.modify_position(ticket, sl=new_sl)
            return {"ticket": ticket, "action": "trailing_stop", "new_sl": new_sl}
        except Exception as e:
            log.warning(f"[SMART EXIT] trail modify failed: {e}")
            return None

    # ── Shared close helper ─────────────────────────────────────────────────

    def _close(self, bridge, ticket, symbol, direction,
               profit_pips, profit_usd, exit_type, reason,
               state, dry_run):
        """Execute the close, record it, update stats, and clean up state."""
        log.info(f"[SMART EXIT] {exit_type} {symbol} #{ticket}: {reason}")

        if dry_run:
            self._cleanup(ticket)
            return {"ticket": ticket, "action": f"{exit_type.lower()}_dry",
                    "reason": reason}

        try:
            bridge.close_position(ticket)
        except Exception as e:
            log.warning(f"[SMART EXIT] {exit_type} close failed: {e}")
            return None

        _record_exit(ticket, symbol, direction, exit_type,
                     profit_pips, profit_usd, reason)
        self._update_stats(state, profit_usd, profit_pips, ticket)
        self._cleanup(ticket)
        return {"ticket": ticket, "action": exit_type.lower(), "reason": reason}

    def _cleanup(self, ticket: str):
        self._peak_pips.pop(ticket, None)
        self._lock_armed.discard(ticket)

    def _update_stats(self, state: dict, profit: float = 0.0,
                      profit_pips: float = 0.0, ticket: str = ""):
        """Counts smart-exit closures into session state and tags the
        ticket so the main loop's external-close detector skips it."""
        state["total_trades"] = state.get("total_trades", 0) + 1
        if profit > 0 or profit_pips > 0:
            state["wins"] = state.get("wins", 0) + 1
        else:
            state["losses"] = state.get("losses", 0) + 1
        if ticket:
            self.closed_tickets.add(ticket)
