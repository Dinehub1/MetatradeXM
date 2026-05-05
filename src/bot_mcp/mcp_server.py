#!/usr/bin/env python3
"""
mcp_server.py — MCP bridge between Hermes Agent and the trading bot.

Exposes trading bot state, trade history, skill configs, and control actions
as MCP tools so Hermes can monitor, learn from, and control the bot.

Usage:
    python3 mcp_server.py                # stdio mode (for Hermes MCP)
    python3 mcp_server.py --http         # HTTP mode (for remote access)
    python3 mcp_server.py --stdio         # explicit stdio mode
"""

import sys
import json
import argparse
import logging
from core.logger_factory import get_logger
from core.utils import now_utc
from datetime import datetime, timezone, timedelta
from pathlib import Path
from core.supabase_db import SupabaseDB

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = get_logger("mcp_trading")

# ── Paths ─────────────────────────────────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paths import ROOT_DIR

BOT_DIR = ROOT_DIR
STATUS_FILE = ROOT_DIR / "state" / "bot_status.json"
STATE_FILE = ROOT_DIR / "state" / "trader_state.json"
SCORING_WEIGHTS = ROOT_DIR / "config" / "scoring_weights.json"
SKILLS_DIR = ROOT_DIR / "skills"

# ── FastMCP setup ─────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "trading-bot",
    debug=False,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception as e:
        try: log.debug(f'Caught exception: {e}')
        except: pass
        return default if default is not None else {}


def _db() -> SupabaseDB:
    return SupabaseDB()


# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_status() -> dict:
    """Return the current bot status: cycle, balance, positions, signals, indicators."""
    log.info("[get_status]")
    status = _load_json(STATUS_FILE, {})
    state = _load_json(STATE_FILE, {})
    return {
        "bot": status,
        "state": state,
        "server_time_utc": now_utc().isoformat(),
    }


@mcp.tool()
def get_trades(limit: int = 50, symbol: str = None) -> dict:
    """
    Return recent trades from Supabase trade_outcomes.

    Args:
        limit: Max number of trades (default 50, max 500)
        symbol: Filter by symbol, e.g. 'XAUUSD' (optional)
    """
    log.info(f"[get_trades] limit={limit}, symbol={symbol}")
    limit = min(limit, 500)

    rows = _db().get_all_outcomes(symbol=symbol, limit=limit)

    return {"trades": rows, "count": len(rows)}


@mcp.tool()
def get_performance_summary(days: int = 7) -> dict:
    """
    Return a performance summary over the last N days.

    Args:
        days: Number of days to look back (default 7, max 90)
    """
    log.info(f"[get_performance_summary] days={days}")
    days = min(days, 90)

    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    trades = [
        trade for trade in _db().get_all_outcomes(limit=1000)
        if (trade.get("ts") or "") >= cutoff
    ]

    if not trades:
        return {"days": days, "trades": [], "summary": {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "total_pips": 0.0, "avg_pips": 0.0
        }}

    wins = [t for t in trades if t.get("outcome") == "WIN"]
    losses = [t for t in trades if t.get("outcome") == "LOSS"]
    total_pips = sum(t.get("pips_result") or 0 for t in trades)

    # Per-symbol breakdown
    symbols = {}
    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        if sym not in symbols:
            symbols[sym] = {"total": 0, "wins": 0, "losses": 0, "pips": 0}
        symbols[sym]["total"] += 1
        symbols[sym]["pips"] += t.get("pips_result") or 0
        if t.get("outcome") == "WIN":
            symbols[sym]["wins"] += 1
        else:
            symbols[sym]["losses"] += 1

    # Per-skill breakdown
    skill_stats = {}
    for t in trades:
        skills_raw = t.get("skills_used", [])
        if isinstance(skills_raw, str):
            try:
                skills_raw = json.loads(skills_raw)
            except Exception as e:
                try: log.debug(f'Caught exception: {e}')
                except: pass
                skills_raw = []
        for skill in (skills_raw or []):
            if skill not in skill_stats:
                skill_stats[skill] = {"total": 0, "wins": 0, "losses": 0, "pips": 0}
            skill_stats[skill]["total"] += 1
            skill_stats[skill]["pips"] += t.get("pips_result") or 0
            if t.get("outcome") == "WIN":
                skill_stats[skill]["wins"] += 1
            else:
                skill_stats[skill]["losses"] += 1

    # Session breakdown
    sessions = {}
    for t in trades:
        ses = t.get("session", "UNKNOWN") or "UNKNOWN"
        if ses not in sessions:
            sessions[ses] = {"total": 0, "wins": 0, "losses": 0}
        sessions[ses]["total"] += 1
        if t.get("outcome") == "WIN":
            sessions[ses]["wins"] += 1
        else:
            sessions[ses]["losses"] += 1

    for sym_data in symbols.values():
        sym_data["win_rate"] = round(sym_data["wins"] / sym_data["total"] * 100, 1) if sym_data["total"] > 0 else 0

    for skill_data in skill_stats.values():
        skill_data["win_rate"] = round(skill_data["wins"] / skill_data["total"] * 100, 1) if skill_data["total"] > 0 else 0

    return {
        "days": days,
        "trades": trades,
        "summary": {
            "total": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_pips": round(total_pips, 2),
            "avg_pips": round(total_pips / len(trades), 2) if trades else 0,
        },
        "by_symbol": symbols,
        "by_skill": skill_stats,
        "by_session": sessions,
    }


@mcp.tool()
def get_skill_suggestions() -> dict:
    """
    Return current skill configs and self-improvement suggestions.
    Reads all SKILL.md files and the scoring_weights.json.
    """
    log.info("[get_skill_suggestions]")

    # Scoring weights
    weights = _load_json(SCORING_WEIGHTS, {})

    # All skill configs
    skills = {}
    if SKILLS_DIR.exists():
        for skill_path in sorted(SKILLS_DIR.iterdir()):
            if skill_path.is_dir():
                md_file = skill_path / "SKILL.md"
                if md_file.exists():
                    text = md_file.read_text()
                    # Parse frontmatter
                    frontmatter = {}
                    body = ""
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        if len(parts) >= 3:
                            frontmatter_text = parts[1].strip()
                            body = parts[2].strip()
                            # Simple parse
                            for line in frontmatter_text.splitlines():
                                line = line.strip()
                                if not line or line.startswith("#"):
                                    continue
                                if ":" in line:
                                    k, v = line.split(":", 1)
                                    k = k.strip()
                                    v = v.strip()
                                    if v == "" or v.lower() == "null":
                                        frontmatter[k] = None
                                    elif v.replace(".", "").replace("-", "").isdigit():
                                        try:
                                            frontmatter[k] = float(v) if "." in v else int(v)
                                        except ValueError:
                                            frontmatter[k] = v.strip("'\"")
                                    elif v.lower() in ("true", "false"):
                                        frontmatter[k] = v.lower() == "true"
                                    elif v.startswith("[") and v.endswith("]"):
                                        frontmatter[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",")]
                                    else:
                                        frontmatter[k] = v.strip("'\"")
                    skills[skill_path.name] = {
                        "frontmatter": frontmatter,
                        "body_preview": body[:300],
                    }

    # Improvement suggestions from self-improver logs (last entry)
    improvements = []
    log_file = BOT_DIR / "trading.log"
    if log_file.exists():
        content = log_file.read_text()
        # Grab last [SKILLS] Improvement block
        import re
        blocks = re.findall(r'\[SKILLS\] Improvement for \'([^\']+)\':\s*\[(.*?)\]', content, re.DOTALL)
        for skill_name, items_str in reversed(blocks[-10:]):
            items = re.findall(r"'([^']+)'", items_str)
            improvements.append({"skill": skill_name, "suggestions": items})

    return {
        "scoring_weights": weights,
        "skills": skills,
        "recent_improvements": improvements[-10:] if improvements else [],
    }


@mcp.tool()
def get_skill_config(skill_name: str) -> dict:
    """
    Return the full config and body of a specific skill.

    Args:
        skill_name: Name of the skill directory (e.g. 'mean-reversion')
    """
    log.info(f"[get_skill_config] {skill_name}")
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return {"error": f"Skill '{skill_name}' not found"}

    text = skill_path.read_text()
    frontmatter = {}
    body = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            for line in fm_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip()
                    if v == "" or v.lower() == "null":
                        frontmatter[k] = None
                    elif v.replace(".", "").replace("-", "").isdigit():
                        try:
                            frontmatter[k] = float(v) if "." in v else int(v)
                        except ValueError:
                            frontmatter[k] = v.strip("'\"")
                    elif v.lower() in ("true", "false"):
                        frontmatter[k] = v.lower() == "true"
                    elif v.startswith("[") and v.endswith("]"):
                        frontmatter[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",")]
                    else:
                        frontmatter[k] = v.strip("'\"")
    return {"skill_name": skill_name, "frontmatter": frontmatter, "body": body}


@mcp.tool()
def update_skill_config(skill_name: str, updates: dict) -> dict:
    """
    Update a skill's frontmatter fields (conditions, performance, etc).
    Writes changes directly to the SKILL.md file.

    Args:
        skill_name: Name of the skill directory (e.g. 'mean-reversion')
        updates: Dict of top-level YAML keys to update.
                 Example: {"max_adx": 30, "boost_amount": 0.08}
    """
    log.info(f"[update_skill_config] {skill_name} -> {updates}")
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return {"error": f"Skill '{skill_name}' not found"}

    text = skill_path.read_text()
    if not text.startswith("---"):
        return {"error": "No frontmatter found in SKILL.md"}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"error": "Invalid frontmatter format"}

    fm_text = parts[1].strip()
    body = parts[2].strip()

    # Parse frontmatter into dict
    fm = {}
    current_key = None
    current_dict = None
    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  ") and current_dict is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            v = v.strip()
            if v.lower() == "null" or v == "":
                v = None
            elif v.replace(".", "").replace("-", "").isdigit() and v.count(".") <= 1:
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    v = v.strip("'\"")
            elif v.lower() in ("true", "false"):
                v = v.lower() == "true"
            elif v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                v = v.strip("'\"")
            current_dict[k.strip()] = v
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v == "":
                fm[k] = {}
                current_dict = fm[k]
                current_key = k
            elif v.lower() == "null":
                fm[k] = None
                current_dict = None
            elif v.replace(".", "").replace("-", "").isdigit() and v.count(".") <= 1:
                try:
                    fm[k] = float(v) if "." in v else int(v)
                except ValueError:
                    fm[k] = v.strip("'\"")
                current_dict = None
            elif v.lower() in ("true", "false"):
                fm[k] = v.lower() == "true"
                current_dict = None
            elif v.startswith("[") and v.endswith("]"):
                fm[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                current_dict = None
            else:
                fm[k] = v.strip("'\"")
            current_key = k
        elif stripped.startswith("- ") and current_key:
            # List item without key
            pass

    # Apply updates
    fm.update(updates)

    # Serialize back to YAML-ish string
    def _fmt_val(v, indent=0):
        prefix = "  " * indent
        if v is None:
            return "null"
        elif isinstance(v, bool):
            return "true" if v else "false"
        elif isinstance(v, (int, float)):
            return str(v)
        elif isinstance(v, list):
            return "[" + ", ".join(str(x) for x in v) + "]"
        elif isinstance(v, dict):
            lines = []
            for k2, v2 in v.items():
                lines.append(f"{prefix}  {k2}: {_fmt_val(v2, indent+1)}")
            return "\n" + "\n".join(lines)
        else:
            return f"'{v}'"

    fm_lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, dict):
            fm_lines.append(f"{k}:")
            for k2, v2 in v.items():
                fm_lines.append(f"  {k2}: {_fmt_val(v2)}")
        else:
            fm_lines.append(f"{k}: {_fmt_val(v)}")
    fm_lines.append("---")

    new_text = "\n".join(fm_lines) + "\n" + body
    skill_path.write_text(new_text)

    return {"success": True, "skill": skill_name, "updated": updates}


@mcp.tool()
def update_scoring_weights(updates: dict) -> dict:
    """
    Update scoring_weights.json fields.

    Args:
        updates: Dict of weight keys and new values.
                 Example: {"f3_rsi": 1.5}

    NOTE: buy_threshold and sell_threshold are LOCKED and cannot be changed
    via this tool. They were critically fixed (4→18) after causing excessive
    entries. Edit scoring_weights.json directly if you truly need to change them.
    """
    # These fields control entry frequency — a wrong value causes runaway trading.
    # They must only be changed by a human with full understanding of the consequences.
    LOCKED_FIELDS = {"buy_threshold", "sell_threshold"}
    blocked = {k: v for k, v in updates.items() if k in LOCKED_FIELDS}
    if blocked:
        log.warning(f"[update_scoring_weights] BLOCKED attempt to modify locked fields: {blocked}")
        updates = {k: v for k, v in updates.items() if k not in LOCKED_FIELDS}
        if not updates:
            return {"success": False, "error": f"Fields {list(blocked.keys())} are locked. No changes made."}

    log.info(f"[update_scoring_weights] {updates}")
    weights = _load_json(SCORING_WEIGHTS, {})
    weights.update(updates)
    weights["last_adjusted"] = now_utc().strftime("%Y-%m-%d")
    if "adjustment_history" not in weights:
        weights["adjustment_history"] = []
    weights["adjustment_history"].append({
        "date": now_utc().strftime("%Y-%m-%d"),
        "change": str(updates),
    })
    SCORING_WEIGHTS.write_text(json.dumps(weights, indent=2))
    return {"success": True, "updated": updates, "weights": weights}


@mcp.tool()
def close_all_positions() -> dict:
    """
    Close all currently open positions via the trading bot's bridge.
    Returns the result of the close operation.
    """
    log.info("[close_all_positions]")
    import importlib.util

    # Dynamically load continuous_trader to call cmd_close_all
    spec = importlib.util.spec_from_file_location("continuous_trader", BOT_DIR / "continuous_trader.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["continuous_trader"] = mod
    spec.loader.exec_module(mod)

    # Capture log output
    import io, logging.handlers
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = get_logger("continuous_trader")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    try:
        result = mod.cmd_close_all()
        log_output = stream.getvalue()
        return {"result": result, "log": log_output}
    except Exception as e:
        log.error(f"Error closing all positions: {e}")
        return {"error": str(e)}
    finally:
        logger.removeHandler(handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP server for trading bot")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode")
    parser.add_argument("--stdio", action="store_true", help="Run in stdio mode (explicit)")
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="http")
    elif args.stdio:
        mcp.run(transport="stdio")
    else:
        # Default to stdio for Hermes
        mcp.run(transport="stdio")
