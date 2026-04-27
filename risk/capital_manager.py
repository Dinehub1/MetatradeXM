"""
capital_manager.py — Dynamic Capital & Lot Size Manager

Intelligently scales lot size based on:
  1. Current win streak / drawdown state
  2. Account equity growth (compound profits automatically)
  3. Risk per trade % (fixed-fractional money management)
  4. Volatility (ATR-based lot reduction in chaotic markets)
  5. Session quality (reduce size during low-liquidity windows)

PROFIT COMPOUNDING:
  As the account grows, lot size grows proportionally — you automatically
  trade bigger when you're winning and smaller when you're losing.

AGGRESSIVE PROFIT BOOKING:
  Partial close system books real USD at every 3–5 pip milestone.
  Even if SL is eventually hit on the remainder, you've already banked profit.
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from core.paths import DATA_DIR, STATE_DIR


log = logging.getLogger("capital_mgr")

DB_PATH    = DATA_DIR / "trade_memory.db"
STATE_PATH = STATE_DIR / "capital_state.json"

# ── Configuration ─────────────────────────────────────────────────────────────
CAPITAL_CFG = {
    # Risk Management — tightened for scalping with small account
    "risk_per_trade_pct":    0.5,    # risk 0.5% of balance per trade (was 1.0%)
    "max_lot":               0.10,   # hard ceiling lot size
    "min_lot":               0.01,   # floor lot size
    "pip_sl_estimate":       15,     # estimated SL in pips for scalping (was 25)

    # Compounding: conservative on streaks
    "streak_boost_threshold": 3,     # 3+ consecutive wins → boost risk
    "streak_boost_pct":       0.8,   # boost to 0.8% risk on winning streak (was 1.5%)
    "drawdown_reduce_threshold": 3,  # 3 consecutive losses → reduce risk
    "drawdown_reduce_pct":    0.3,   # reduce to 0.3% risk on losing streak (was 0.5%)

    # Profit Booking — calibrated to 160-pip TP (Senior Trader Overhaul 2026-04-23)
    # OLD milestones: 3/6/10 pips (scalping) — fired immediately and killed the whole trade.
    # On 0.01 lot (minimum) partial closes fail because you can't close a fraction.
    # NEW milestones: let the trade breathe and book at 25%/50%/75% of the way to TP.
    # At 160-pip TP: milestone 1 = 40 pips (25%), 2 = 80 pips (50%), 3 = 120 pips (75%)
    "partial_book_enabled":   True,
    "book_levels": [
        {"pips": 40,  "close_pct": 0.40},  # at +40 pips (25% of TP): book 40%
        {"pips": 80,  "close_pct": 0.30},  # at +80 pips (50% of TP): book another 30%
        {"pips": 120, "close_pct": 0.20},  # at +120 pips (75% of TP): book another 20%
        # remaining 10% rides free to TP or SL — pure profit
    ],
    # Minimum lot required to attempt partial close — below this, skip (can't split)
    "min_lot_for_partial":    0.02,   # 0.01 lot = can't close fraction, skip all levels

    # Volatility scaling
    "atr_scale_enabled":     True,
    "atr_high_threshold":    2.0,    # ATR > 2× average → reduce lot by 30%
    "atr_low_threshold":     0.5,    # ATR < 0.5× average → is dead market, skip

    # Session quality multipliers — fixed key mismatch
    "session_multipliers": {
        "LONDON":            1.0,   # full size
        "NEW_YORK":          0.8,   # slightly reduced
        "LONDON_NY_OVERLAP": 1.0,   # best liquidity (was "LONDON_NY" — key mismatch fix)
        "ASIAN":             0.3,   # heavily reduced — thin markets (was 0.6)
        "MARKET_CLOSED":     0.0,   # no trading
    },
}


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "consecutive_wins":   0,
        "consecutive_losses": 0,
        "peak_balance":       0.0,
        "booked_profit_usd":  0.0,
        "total_booked_trades": 0,
        "partial_closes_done": {},  # ticket -> list of levels done
    }


def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _ensure_table():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS partial_closes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT NOT NULL,
                ticket       TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                direction    TEXT NOT NULL,
                level_pips   REAL NOT NULL,
                close_pct    REAL NOT NULL,
                close_volume REAL NOT NULL,
                profit_usd   REAL,
                outcome      TEXT DEFAULT 'BOOKED'
            )
        """)


# ── Capital Manager ──────────────────────────────────────────────────────────

class CapitalManager:
    """
    Computes optimal lot size per trade and manages partial profit booking.
    Integrate in continuous_trader.py:
        - Call compute_lot() before placing any trade
        - Call book_profits() in the monitor loop
    """

    def __init__(self):
        _ensure_table()
        self.state = _load_state()
        log.info(f"[CAPITAL] Initialized. Streak W:{self.state['consecutive_wins']} "
                 f"L:{self.state['consecutive_losses']} | "
                 f"Booked: ${self.state['booked_profit_usd']:.2f} total")

    def compute_lot(self, balance: float, atr: float, atr_avg: float,
                    session: str, sl_pips: float = None,
                    symbol: str = "GOLD.i#") -> float:
        """
        Calculate the optimal lot size for the next trade.

        Args:
            balance:   Current account balance in USD
            atr:       Current ATR value (in price units)
            atr_avg:   Average ATR over past 20 candles
            session:   Market session string (LONDON, NEW_YORK, etc.)
            sl_pips:   Estimated SL distance in pips (uses default if None)

        Returns:
            Rounded lot size (e.g. 0.02)
        """
        # 1. Base risk %
        risk_pct = CAPITAL_CFG["risk_per_trade_pct"]

        # 2. Streak adjustment (compound on wins, protect on losses)
        if self.state["consecutive_wins"] >= CAPITAL_CFG["streak_boost_threshold"]:
            risk_pct = CAPITAL_CFG["streak_boost_pct"]
            log.info(f"[CAPITAL] 🚀 Win streak {self.state['consecutive_wins']} — "
                     f"boosting risk to {risk_pct}%")
        elif self.state["consecutive_losses"] >= CAPITAL_CFG["drawdown_reduce_threshold"]:
            risk_pct = CAPITAL_CFG["drawdown_reduce_pct"]
            log.info(f"[CAPITAL] 🛡️ Loss streak {self.state['consecutive_losses']} — "
                     f"reducing risk to {risk_pct}%")

        # 3. Session quality multiplier
        session_mult = CAPITAL_CFG["session_multipliers"].get(session, 0.0)
        if session_mult == 0.0:
            log.info(f"[CAPITAL] Market closed — lot = 0")
            return 0.0

        # 4. Volatility adjustment
        atr_mult = 1.0
        if CAPITAL_CFG["atr_scale_enabled"] and atr_avg > 0:
            atr_ratio = atr / atr_avg
            if atr_ratio > CAPITAL_CFG["atr_high_threshold"]:
                atr_mult = 0.7   # high volatility → reduce by 30%
                log.info(f"[CAPITAL] ⚡ High ATR ({atr_ratio:.1f}×) — reducing lot by 30%")
            elif atr_ratio < CAPITAL_CFG["atr_low_threshold"]:
                atr_mult = 0.0   # dead market → skip
                log.info(f"[CAPITAL] 💀 Dead market (ATR {atr_ratio:.1f}×) — skipping")
                return 0.0

        # 5. Compute lot from risk
        sl = sl_pips or CAPITAL_CFG["pip_sl_estimate"]
        risk_usd    = balance * (risk_pct / 100)
        # Pip value per lot depends on symbol contract size
        # Gold: pip=0.10, contract=100oz → $10/pip/lot → $0.10 per 0.01 lot
        # Silver: pip=0.01, contract=5000oz → $50/pip/lot → $0.50 per 0.01 lot
        pip_sizes = {"GOLD.i#": 0.10, "SILVER.i#": 0.01}
        contract_sizes = {"GOLD.i#": 100, "SILVER.i#": 5000}
        _pip = pip_sizes.get(symbol, 0.0001)
        _cs = contract_sizes.get(symbol, 100)
        pip_value_per_lot = _pip * _cs   # e.g. Gold: 0.10*100=$10, Silver: 0.01*5000=$50
        raw_lot = risk_usd / (sl * pip_value_per_lot)

        # 6. Apply multipliers
        lot = raw_lot * session_mult * atr_mult

        # 7. Clamp and round
        lot = max(CAPITAL_CFG["min_lot"], min(CAPITAL_CFG["max_lot"], lot))
        lot = round(round(lot / 0.01) * 0.01, 2)   # round to nearest 0.01

        log.info(f"[CAPITAL] Lot calc: balance=${balance:.2f} risk={risk_pct}% "
                 f"session_mult={session_mult} atr_mult={atr_mult:.1f} → lot={lot}")
        return lot

    def record_outcome(self, outcome: str, profit_usd: float):
        """
        Call this after every trade closes to update streaks and compound state.
        outcome: 'WIN' or 'LOSS'
        """
        if outcome == "WIN":
            self.state["consecutive_wins"] += 1
            self.state["consecutive_losses"] = 0
        else:
            self.state["consecutive_losses"] += 1
            self.state["consecutive_wins"] = 0

        _save_state(self.state)
        log.info(f"[CAPITAL] Outcome: {outcome} ${profit_usd:+.2f} | "
                 f"Streak W:{self.state['consecutive_wins']} L:{self.state['consecutive_losses']}")

    def book_profits(self, bridge, positions_by_sym: dict, symbols: dict,
                     dry_run: bool = False) -> list:
        """
        Scan all open positions and execute partial closes at profit milestones.
        This BOOKS REAL CASH at every level even if the trade later reverses.

        Returns list of partial close actions taken.
        """
        if not CAPITAL_CFG["partial_book_enabled"]:
            return []

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
                profit_usd = getattr(pos, "profit", 0.0)

                # Calculate pips using contract_size for correct pip value
                contract_size = sym_cfg.get("contract_size", 100)
                profit_pips = profit_usd / (pip * contract_size * volume) if (pip * contract_size * volume) > 0 else 0

                # Only book profits (not losses)
                if profit_pips <= 0:
                    continue

                # Check which levels have already been booked for this ticket
                done_levels = self.state["partial_closes_done"].get(ticket, [])

                for level in CAPITAL_CFG["book_levels"]:
                    level_pips = level["pips"]
                    close_pct  = level["close_pct"]

                    # Skip if already booked this level
                    if level_pips in done_levels:
                        continue

                    # Skip if not yet at this level
                    if profit_pips < level_pips:
                        continue

                    # GUARD: skip partial closes if volume is at minimum (can't split)
                    if volume < CAPITAL_CFG["min_lot_for_partial"]:
                        # Log once only (when first level would have triggered)
                        if level_pips == CAPITAL_CFG["book_levels"][0]["pips"] and profit_pips >= level_pips:
                            log.info(f"[CAPITAL] {display_name} #{ticket}: lot={volume} too small for partial close — riding to TP")
                        break

                    # Calculate volume to close
                    close_vol = round(volume * close_pct, 2)
                    close_vol = max(0.01, close_vol)

                    # Don't close more than we have
                    close_vol = min(close_vol, volume - 0.01)
                    if close_vol <= 0:
                        continue

                    # Estimate booked profit
                    booked_usd = profit_usd * close_pct

                    log.info(f"[CAPITAL] 💰 BOOKING PROFIT: {display_name} #{ticket} "
                             f"{'BUY' if direction=='BUY' else 'SELL'} "
                             f"+{profit_pips:.1f}pips → closing {close_pct:.0%} "
                             f"({close_vol} lots) ≈ ${booked_usd:.2f}")

                    if not dry_run:
                        try:
                            bridge.close_position(ticket, volume=close_vol)

                            # Record
                            done_levels.append(level_pips)
                            self.state["partial_closes_done"][ticket] = done_levels
                            self.state["booked_profit_usd"] = round(
                                self.state.get("booked_profit_usd", 0) + booked_usd, 4)
                            self.state["total_booked_trades"] = \
                                self.state.get("total_booked_trades", 0) + 1
                            _save_state(self.state)

                            _record_partial_close(
                                ticket, display_name, direction,
                                level_pips, close_pct, close_vol, booked_usd
                            )

                            actions.append({
                                "ticket":    ticket,
                                "symbol":    display_name,
                                "level_pips": level_pips,
                                "close_vol": close_vol,
                                "booked_usd": booked_usd,
                            })
                        except Exception as e:
                            log.warning(f"[CAPITAL] Partial close failed: {e}")
                    else:
                        done_levels.append(level_pips)
                        self.state["partial_closes_done"][ticket] = done_levels
                        log.info(f"[CAPITAL] DRY: Would have booked ${booked_usd:.2f}")
                        actions.append({
                            "ticket": ticket, "symbol": display_name,
                            "level_pips": level_pips, "booked_usd_dry": booked_usd
                        })

        return actions

    def get_summary(self) -> dict:
        """Return capital management performance summary."""
        return {
            "consecutive_wins":    self.state["consecutive_wins"],
            "consecutive_losses":  self.state["consecutive_losses"],
            "total_booked_usd":    self.state.get("booked_profit_usd", 0),
            "total_booked_trades": self.state.get("total_booked_trades", 0),
            "current_risk_pct":    (
                CAPITAL_CFG["streak_boost_pct"]
                if self.state["consecutive_wins"] >= CAPITAL_CFG["streak_boost_threshold"]
                else CAPITAL_CFG["drawdown_reduce_pct"]
                if self.state["consecutive_losses"] >= CAPITAL_CFG["drawdown_reduce_threshold"]
                else CAPITAL_CFG["risk_per_trade_pct"]
            ),
        }


def _record_partial_close(ticket, symbol, direction, level_pips,
                           close_pct, close_vol, profit_usd):
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                INSERT INTO partial_closes
                (ts, ticket, symbol, direction, level_pips, close_pct, close_volume, profit_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, str(ticket), symbol, direction, level_pips,
                  close_pct, close_vol, profit_usd))
    except Exception as e:
        log.warning(f"[CAPITAL] DB record failed: {e}")


if __name__ == "__main__":
    mgr = CapitalManager()
    # Test lot calc
    lot = mgr.compute_lot(balance=984.6, atr=13.8, atr_avg=11.5, session="LONDON")
    print(f"Suggested lot: {lot}")
    summary = mgr.get_summary()
    print(f"Capital summary: {json.dumps(summary, indent=2)}")
