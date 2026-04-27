"""
TradeLogger — SQLite-backed trade/signal journal.
"""
from __future__ import annotations

import sqlite3
import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from core.paths import LOG_DIR, DATA_DIR

DB_FILE = DATA_DIR / "trades.db"
LOG_FILE = LOG_DIR / "bot.log"

_FMT = logging.Formatter(
    "%(asctime)s [%(levelname)-5s] [%(name)-12s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger → bot.log (rotating file) + console at INFO+.

    Call once at process startup before any loggers are created.
    Outputs:
      - Console (stdout): INFO+ for IDE/terminal visibility
      - File (logs/bot.log): INFO+ with 10 MB × 3 rotation for SSH/PM2
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # ── Console handler (stdout) — full visibility ────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    ch.setFormatter(_FMT)
    root.addHandler(ch)

    # ── File handler (logs/bot.log) — persistent for SSH/PM2 ─────────────
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(_FMT)
        root.addHandler(fh)
    except Exception as e:
        # Don't crash if log dir is not writable (e.g. first run)
        print(f"[WARN] Could not create file logger: {e}", file=sys.stderr)


# ── Backwards-compatible alias so existing imports still work ─────────────────
bot_log = logging.getLogger("trading_bot")

# ── Color helpers ─────────────────────────────────────────────────────────────
try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init()
    _COLORS = {"BUY": Fore.GREEN, "SELL": Fore.RED, "HOLD": Fore.YELLOW}
    _RESET  = Style.RESET_ALL
except ImportError:
    _COLORS = {}
    _RESET  = ""


class TradeLogger:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT,
                    symbol    TEXT,
                    direction TEXT,
                    confidence REAL,
                    reason    TEXT,
                    action    TEXT,
                    ticket    INTEGER,
                    order_json TEXT
                )
            """)
            conn.commit()

    def log(self, signal: dict, action: str = "HOLD",
            order: dict | None = None, ticket: int | None = None,
            symbol: str = ""):
        sym = symbol or signal.get("indicators", {}).get("symbol", "N/A")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO signals (ts, symbol, direction, confidence, reason, action, ticket, order_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                sym,
                signal["direction"],
                signal["confidence"],
                signal["reason"],
                action,
                ticket,
                json.dumps(order) if order else None,
            ))
            conn.commit()

        # Write to rotating file log
        msg = (f"{sym} {signal['direction']} conf={signal['confidence']:.0%} "
               f"action={action} | {signal['reason'][:80]}")
        if action in ("ORDER_FAILED", "DRAWDOWN_HALT"):
            bot_log.error(msg)
        elif action in ("MARKET_CLOSED", "SKIP_SESSION"):
            bot_log.warning(msg)
        else:
            bot_log.info(msg)

    def print_history(self, limit: int = 20, filter_action: str = None,
                      filter_symbol: str = None):
        query  = "SELECT ts, symbol, direction, confidence, action, reason FROM signals"
        params = []
        conds  = []
        if filter_action:
            conds.append("action = ?"); params.append(filter_action.upper())
        if filter_symbol:
            conds.append("symbol = ?"); params.append(filter_symbol.upper())
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()

        if not rows:
            print("\n  No trade history yet.")
            return

        print(f"\n── Last {len(rows)} entries ─────────────────────────────────")
        for ts, sym, direction, conf, action, reason in rows:
            color = _COLORS.get(direction, "")
            print(f"  {ts[:19]}  {sym:8s}  {color}{direction:6s}{_RESET}  "
                  f"{conf:.0%}  [{action:20s}]  {reason[:55]}")
        print("─" * 70 + "\n")
