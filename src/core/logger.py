"""
TradeLogger — Supabase-backed trade/signal journal.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from core.paths import LOG_DIR

LOG_FILE = LOG_DIR / "bot.log"

_FMT = logging.Formatter(
    "%(asctime)s [%(levelname)-5s] [%(name)-12s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_FMT.converter = time.gmtime  # UTC timestamps — prevents local-time confusion


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
    def __init__(self, db_path: str = None):
        if db_path:
            bot_log.warning("Ignoring db_path=%s; Supabase is the only signal journal", db_path)

    def _init_db(self):
        return None

    def log(self, signal: dict, action: str = "HOLD",
            order: dict | None = None, ticket: int | None = None,
            symbol: str = ""):
        sym = symbol or signal.get("indicators", {}).get("symbol", "N/A")
        try:
            from core.supabase_db import SupabaseDB
            SupabaseDB().log_runtime_event(
                "signal",
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "direction": signal["direction"],
                    "confidence": signal["confidence"],
                    "reason": signal["reason"],
                    "action": action,
                    "ticket": ticket,
                    "order_json": order or None,
                },
                source="trade_logger",
                symbol=sym,
            )
        except Exception as e:
            bot_log.warning("Supabase signal journal failed: %s", e)

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
        from core.supabase_db import SupabaseDB
        events = [
            e for e in SupabaseDB().get_live_events(limit=limit * 5)
            if e.get("event_type") == "signal"
        ]
        rows = []
        for event in events:
            payload = event.get("payload") or {}
            action = payload.get("action", "")
            sym = event.get("symbol", "")
            if filter_action and action.upper() != filter_action.upper():
                continue
            if filter_symbol and sym.upper() != filter_symbol.upper():
                continue
            rows.append((
                event.get("ts", ""),
                sym,
                payload.get("direction", ""),
                payload.get("confidence", 0),
                action,
                payload.get("reason", ""),
            ))
            if len(rows) >= limit:
                break

        if not rows:
            print("\n  No trade history yet.")
            return

        print(f"\n── Last {len(rows)} entries ─────────────────────────────────")
        for ts, sym, direction, conf, action, reason in rows:
            color = _COLORS.get(direction, "")
            print(f"  {ts[:19]}  {sym:8s}  {color}{direction:6s}{_RESET}  "
                  f"{conf:.0%}  [{action:20s}]  {reason[:55]}")
        print("─" * 70 + "\n")
