#!/usr/bin/env python3
"""MT5 AI Trading Bot — Futuristic Dashboard v3.0
Port: 8889 | Chart.js | Multi-symbol | Real-time | Glassmorphism | Threaded
"""
import http.server, json, os, time, threading
from datetime import datetime, timezone
from socketserver import ThreadingMixIn
from pathlib import Path
import sys

# ── Authentication ─────────────────────────────────────────────────────────────
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "changeme")  # env override: DASHBOARD_TOKEN=<secret>

PORT         = 8889
BASE_DIR     = Path(__file__).resolve().parent.parent.parent   # src/dashboard/ -> src/ -> project root
STATUS_FILE  = BASE_DIR / "state" / "bot_status.json"
CANDLES_FILE = BASE_DIR / "state" / "candles_cache.json"

# Log files — check PM2 log first (primary), fall back to logs/bot.log
_PM2_LOG     = Path("/home/ubuntu/.pm2/logs/metatradeXM-bot-out.log")
_BOT_LOG     = BASE_DIR / "logs" / "bot.log"
LOG_FILE     = _PM2_LOG if _PM2_LOG.exists() else _BOT_LOG

# ── Live bridge initialization ───────────────────────────────────────────────────
sys.path.insert(0, str(BASE_DIR / "src"))
try:
    from core.config import get_webhook_config as _get_wh_cfg
    _bridge = None
    _bridge_lock = threading.Lock()

    def _build_bridge():
        _wh = _get_wh_cfg()
        ws_url = (_wh.get("ws_url") or "").strip()
        http_url = (_wh.get("webhook_url") or "").strip()
        if ws_url and http_url:
            from bridges.ws_bridge import WSBridge
            return WSBridge(ws_url, http_url)
        if http_url:
            from bridges.webhook_bridge import WebhookBridge
            return WebhookBridge(http_url)
        from bridges.mt5_bridge import MT5Bridge
        return MT5Bridge()

    def get_bridge():
        global _bridge
        with _bridge_lock:
            if _bridge is None:
                _bridge = _build_bridge()
                if not _bridge.connect():
                    _bridge = None
        return _bridge
except ImportError:
    _bridge = None
    _bridge_lock = threading.Lock()
    def get_bridge():
        return None

# ── Supabase client initialization ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    from core.supabase_db import SupabaseDB
    _supabase = SupabaseDB()
except Exception as e:
    _supabase = None
    print(f"[WARNING] Supabase init failed: {e}")

_SYNC_THREAD = None
_SYNC_STOP = threading.Event()
_BROKER_TO_DISPLAY = {
    "GOLD.i#": "XAUUSD",
    "SILVER.i#": "XAGUSD",
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
}
_DISPLAY_TO_BROKER = {
    "XAUUSD": "GOLD.i#",
    "XAGUSD": "SILVER.i#",
}

# ── Response caching (TTL-based) ────────────────────────────────────────────────
_response_cache = {}
CACHE_TTL = 2  # seconds; live dashboard should feel immediate

def _get_cached(key: str, fetch_func):
    """Get cached response or compute fresh one."""
    now = time.time()
    if key in _response_cache:
        cached_data, timestamp = _response_cache[key]
        if now - timestamp < CACHE_TTL:
            return cached_data
    result = fetch_func()
    _response_cache[key] = (result, now)
    return result

def _current_session() -> str:
    h = datetime.now(timezone.utc).hour
    if  8 <= h < 13: return "LONDON"
    if 13 <= h < 17: return "LONDON_NY_OVERLAP"
    if 17 <= h < 22: return "NEW_YORK"
    return "ASIAN"

def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def _epoch(value) -> int:
    if not value:
        return 0
    try:
        if isinstance(value, (int, float)):
            return int(value)
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0

def _age_s(value) -> int:
    ts = _epoch(value)
    if not ts:
        return 0
    return max(0, int(time.time() - ts))

def _require_supabase():
    if _supabase is None:
        raise RuntimeError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY.")
    return _supabase

def _live_snapshot_from_supabase() -> dict:
    db = _require_supabase()
    rows = db.get_live_market_snapshots()
    snap = {"session": _current_session(), "symbols": {}, "source": "supabase"}
    newest = 0

    for sym, row in rows.items():
        raw = row.get("raw_json") or {}
        indicators = row.get("indicators_json") or raw.get("indicators") or {}
        timeframes = row.get("timeframes_json") or raw.get("timeframes") or {}
        ts = row.get("updated_at") or row.get("ts")
        newest = max(newest, _epoch(ts))

        sym_data = {
            "price": _num(row.get("price") or indicators.get("price")),
            "bid": _num(row.get("bid"), None),
            "ask": _num(row.get("ask"), None),
            "spread": _num(row.get("spread"), None),
            "digits": row.get("digits"),
            "atr": _num(row.get("atr") or indicators.get("atr"), None),
            "daily_high": _num(row.get("daily_high"), None),
            "daily_low": _num(row.get("daily_low"), None),
            "signal": row.get("signal") or raw.get("signal"),
            "confidence": _num(row.get("confidence") or raw.get("confidence"), 0),
            "score": _num(row.get("score") or indicators.get("score"), 0),
            "session": row.get("session") or raw.get("session") or snap["session"],
            "indicators": indicators,
            "timeframes": timeframes,
            "factor_scores": row.get("factor_scores") or raw.get("factor_scores") or {},
            "_last_update": _epoch(ts),
            "_age_s": _age_s(ts),
            "source": row.get("source") or "supabase",
        }
        if sym_data["bid"] is None:
            sym_data.pop("bid")
        if sym_data["ask"] is None:
            sym_data.pop("ask")
        snap["symbols"][sym] = sym_data

    if newest:
        snap["_updated"] = newest
        snap["_age_s"] = max(0, int(time.time() - newest))
    return snap

def _status_from_supabase() -> dict:
    db = _require_supabase()
    account = db.get_live_account_snapshot()
    snap = _live_snapshot_from_supabase()
    newest = account.get("updated_at") or account.get("ts") or snap.get("_updated")

    symbols = {}
    for sym, row in snap.get("symbols", {}).items():
        symbols[sym] = {
            "signal": row.get("signal") or "HOLD",
            "confidence": row.get("confidence") or 0,
            "score": row.get("score") or 0,
            "session": row.get("session"),
            "indicators": row.get("indicators") or {},
            "timeframes": row.get("timeframes") or {},
            "factor_scores": row.get("factor_scores") or {},
            "bid": row.get("bid"),
            "ask": row.get("ask"),
        }

    return {
        "state": "running" if symbols else "waiting_for_supabase_live_data",
        "session": snap.get("session"),
        "source": "supabase",
        "_status_age_s": _age_s(newest),
        "_stale": _age_s(newest) > 120 if newest else True,
        "account": {
            "balance": _num(account.get("balance"), 0),
            "equity": _num(account.get("equity"), 0),
            "margin": _num(account.get("margin"), 0),
            "margin_free": _num(account.get("margin_free"), 0),
            "currency": account.get("currency") or "USD",
            "leverage": account.get("leverage") or 0,
        },
        "symbols": symbols,
    }

def _positions_from_supabase() -> list:
    db = _require_supabase()
    positions = []
    for row in db.get_live_positions():
        raw = row.get("raw_json") or {}
        entry = _num(row.get("entry_price") or raw.get("open_price") or raw.get("entry_price"), 0)
        current = _num(row.get("current_price") or raw.get("current_price"), entry)
        positions.append({
            "ticket": row.get("ticket"),
            "symbol": row.get("symbol") or raw.get("symbol", ""),
            "direction": row.get("direction") or raw.get("direction", ""),
            "entry_price": round(entry, 5),
            "current_price": round(current, 5),
            "profit_loss_usd": round(_num(row.get("profit_loss_usd") or raw.get("profit"), 0), 2),
            "profit_loss_pct": round(_num(row.get("profit_loss_pct"), 0), 2),
            "lot_size": _num(row.get("volume") or raw.get("volume") or raw.get("lot_size"), 0),
            "entry_time": row.get("entry_time") or raw.get("entry_time", ""),
            "status": row.get("status") or "OPEN",
        })
    return positions

def _logs_from_supabase(limit: int = 150) -> list:
    import sys as _sys
    print(f"[DEBUG_LOGS_ENTRY] v2 called limit={limit}", file=_sys.stderr, flush=True)
    db = _require_supabase()
    logs = []
    for event in db.get_live_events(limit=limit):
        payload = event.get("payload") or {}
        ts = event.get("ts") or event.get("created_at") or ""
        ts_short = str(ts)[:19].replace("T", " ")
        raw_sym = event.get("symbol") or ""
        symbol = _BROKER_TO_DISPLAY.get(raw_sym, raw_sym)
        event_type = event.get("event_type", "")
        print(f"[DEBUG_LOGS] et={event_type!r} sym={symbol!r}", file=_sys.stderr, flush=True)
        if event_type == "signal" and symbol:
            direction = payload.get("direction", "HOLD")
            conf = round(float(payload.get("confidence", 0)) * 100)
            score = float(payload.get("score", 0))
            reason = payload.get("reason", "")
            ind = payload.get("indicators") or {}
            fs = payload.get("factor_scores") or {}
            tr = payload.get("trends") or {}
            # SIGNAL line — matches JS regex: SIGNAL | SYMBOL | DIR CONF% | score X
            logs.append(
                f"{ts_short} [INFO ] [trader      ] SIGNAL | {symbol} | {direction} {conf}%"
                f" | score {score:+.1f} | {reason[:300]}"
            )
            # DETAIL line 1: trends
            if tr:
                logs.append(
                    f"{ts_short} [INFO ] [trader      ] DETAIL | {symbol} | trend"
                    f" D1={tr.get('d1','?')} H4={tr.get('h4','?')}"
                    f" H1={tr.get('h1','?')} M15={tr.get('m15','?')}"
                )
            # DETAIL line 2: ADX / RSI / MACD / BB / ATR
            if ind:
                adx = float(ind.get("adx", 0))
                adx_lbl = "TREND" if adx >= 25 else "RANGE"
                logs.append(
                    f"{ts_short} [INFO ] [trader      ] DETAIL | {symbol} | ADX {adx:.1f} {adx_lbl}"
                    f" | RSI {float(ind.get('rsi', 50)):.1f}"
                    f" | MACD {ind.get('macd_signal', 'N/A')}"
                    f" | BB {ind.get('bb_position', 'N/A')}"
                    f" | ATR {float(ind.get('atr', 0)):.5f}"
                )
            # DETAIL line 3: Price / Change / Stoch / Score / Factors
            if ind:
                factors_str = " ".join(
                    f"{k}={float(v):+.1f}" for k, v in fs.items()
                    if k != "adx_regime" and isinstance(v, (int, float))
                ) if fs else ""
                logs.append(
                    f"{ts_short} [INFO ] [trader      ] DETAIL | {symbol}"
                    f" | Price {float(ind.get('price', 0)):.5f}"
                    f" | Change {float(ind.get('price_change', 0)):+.3f}%"
                    f" | Stoch {float(ind.get('stoch_k', 50)):.0f}/{float(ind.get('stoch_d', 50)):.0f} NONE"
                    f" | Score {score:+.1f} | Factors {factors_str}"
                )
            # ACTION line when not a plain analysis hold
            action = payload.get("action", "")
            if action and action not in ("ANALYSIS", "HOLD"):
                logs.append(
                    f"{ts_short} [INFO ] [trader      ] ACTION | {symbol} | {action.lower()}"
                )
        else:
            sym_part = f" | {symbol}" if symbol else ""
            detail = payload.get("message") or payload.get("reason") or json.dumps(payload, default=str)
            logs.append(f"{ts} [{event.get('severity', 'INFO')}] {event.get('source')}:{event_type}{sym_part} | {detail}")
    return logs

def _normalize_symbol(symbol: str) -> str:
    return _BROKER_TO_DISPLAY.get(symbol or "", symbol or "")

def _publish_live_bridge_to_supabase():
    if _supabase is None:
        return
    bridge = get_bridge()
    if bridge is None:
        return

    account = bridge.get_account_info()
    if account:
        _supabase.upsert_live_account_snapshot({
            "balance": getattr(account, "balance", 0),
            "equity": getattr(account, "equity", 0),
            "margin": getattr(account, "margin", 0),
            "margin_free": getattr(account, "margin_free", 0),
            "currency": getattr(account, "currency", "USD"),
            "leverage": getattr(account, "leverage", 0),
            "ts": datetime.now(timezone.utc).isoformat(),
        }, source="dashboard_bridge")

    positions = []
    for pos in bridge.get_open_positions() or []:
        direction = "BUY" if getattr(pos, "type", 0) == 0 else "SELL"
        broker_symbol = getattr(pos, "symbol", "")
        display_symbol = _normalize_symbol(broker_symbol)
        entry_price = float(getattr(pos, "price_open", 0) or 0)
        current_price = float(getattr(pos, "price_current", entry_price) or entry_price)
        pnl_usd = float(getattr(pos, "profit", 0) or 0)
        pnl_pct = 0.0
        if entry_price > 0:
            if direction == "BUY":
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
        positions.append({
            "ticket": getattr(pos, "ticket", ""),
            "symbol": display_symbol,
            "direction": direction,
            "volume": float(getattr(pos, "volume", 0) or 0),
            "entry_price": entry_price,
            "current_price": current_price,
            "profit_loss_usd": pnl_usd,
            "profit_loss_pct": pnl_pct,
            "sl": float(getattr(pos, "sl", 0) or 0),
            "tp": float(getattr(pos, "tp", 0) or 0),
            "entry_time": datetime.fromtimestamp(getattr(pos, "time", 0), tz=timezone.utc).isoformat() if getattr(pos, "time", 0) else "",
            "status": "OPEN",
            "broker_symbol": broker_symbol,
        })
    _supabase.replace_live_positions(positions, source="dashboard_bridge")

    for display_symbol, broker_symbol in _DISPLAY_TO_BROKER.items():
        tick = bridge.get_tick(broker_symbol)
        if not tick:
            continue
        indicators = {}
        price = float(getattr(tick, "bid", 0) or 0)
        if price:
            indicators["price"] = price
        _supabase.upsert_live_market_snapshot(
            display_symbol,
            broker_symbol=broker_symbol,
            payload={
                "price": price,
                "bid": float(getattr(tick, "bid", 0) or 0),
                "ask": float(getattr(tick, "ask", 0) or 0),
                "time": getattr(tick, "time", 0),
                "session": _current_session(),
                "indicators": indicators,
            },
            source="dashboard_bridge",
        )

def _live_sync_loop():
    while not _SYNC_STOP.is_set():
        try:
            _publish_live_bridge_to_supabase()
        except Exception as e:
            try:
                if _supabase:
                    _supabase.log_live_event(
                        "dashboard_bridge_error",
                        {"message": str(e)},
                        source="dashboard_bridge",
                        severity="ERROR",
                    )
            except Exception:
                pass
        _SYNC_STOP.wait(5)

def _start_live_bridge_sync():
    global _SYNC_THREAD
    if _supabase is None or _SYNC_THREAD is not None:
        return
    _SYNC_THREAD = threading.Thread(
        target=_live_sync_loop,
        name="dashboard-live-sync",
        daemon=True,
    )
    _SYNC_THREAD.start()

# ── Auth removed — dashboard is open access ─────────────────────────────────

# ── HTML Page ──────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MetaTraderXM - Gold & Silver</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-main: #0a0e17;
    --bg-card: rgba(255, 255, 255, 0.03);
    --gold: #F2C94C;
    --gold-glow: rgba(242, 201, 76, 0.2);
    --silver: #B0C4DE;
    --silver-glow: rgba(176, 196, 222, 0.2);
    --green: #00ff88;
    --red: #ff3b5c;
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --border: rgba(255, 255, 255, 0.08);
    --border-highlight: rgba(255, 255, 255, 0.15);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--bg-main);
    color: var(--text-main);
    font-family: 'Outfit', sans-serif;
    min-height: 100vh;
    background-image: 
      radial-gradient(circle at 15% 50%, rgba(242, 201, 76, 0.03), transparent 25%),
      radial-gradient(circle at 85% 30%, rgba(176, 196, 222, 0.03), transparent 25%);
    display: flex;
    flex-direction: column;
  }
  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg-main); }
  ::-webkit-scrollbar-thumb { background: var(--border-highlight); border-radius: 4px; }

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 15px 30px;
    background: rgba(10, 14, 23, 0.8);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .logo {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 1px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .logo span { color: var(--gold); }
  
  .sessions { display: flex; gap: 15px; }
  .session {
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-muted);
    transition: 0.3s;
  }
  .session.active {
    border-color: var(--gold);
    color: var(--gold);
    box-shadow: 0 0 10px var(--gold-glow);
  }

  .nav-links a {
    color: var(--text-muted);
    text-decoration: none;
    font-size: 13px;
    margin-left: 20px;
    transition: 0.3s;
  }
  .nav-links a:hover { color: var(--gold); }

  main {
    padding: 20px 30px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    flex: 1;
    max-width: 1600px;
    margin: 0 auto;
    width: 100%;
  }

  .grid-top {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 20px;
  }
  
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px;
    backdrop-filter: blur(10px);
    box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
    transition: transform 0.3s, border-color 0.3s;
  }
  .card:hover {
    border-color: var(--border-highlight);
    transform: translateY(-2px);
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
  }
  .card-title {
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    font-weight: 600;
  }

  .metal-card.gold .card-title { color: var(--gold); }
  .metal-card.silver .card-title { color: var(--silver); }

  .price-display {
    font-family: 'JetBrains Mono', monospace;
    font-size: 36px;
    font-weight: 700;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 15px;
  }
  .price-change {
    font-size: 16px;
    padding: 4px 10px;
    border-radius: 8px;
  }
  .price-change.up { background: rgba(0, 255, 136, 0.1); color: var(--green); }
  .price-change.down { background: rgba(255, 59, 92, 0.1); color: var(--red); }

  .stats-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 15px;
    margin-top: 15px;
    padding-top: 15px;
    border-top: 1px solid var(--border);
  }
  .stat-item {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;}
  .stat-val { font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 500; }

  /* Middle Grid */
  .grid-mid {
    display: grid;
    grid-template-columns: 1fr 1.5fr 1fr;
    gap: 20px;
  }
  @media (max-width: 1200px) {
    .grid-mid { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 768px) {
    .grid-mid { grid-template-columns: 1fr; }
  }

  /* Signals */
  .signal-badge {
    display: inline-block;
    padding: 8px 20px;
    border-radius: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    font-size: 24px;
    text-align: center;
    width: 100%;
    margin-bottom: 15px;
  }
  .signal-badge.buy { background: rgba(0, 255, 136, 0.15); color: var(--green); border: 1px solid var(--green); box-shadow: 0 0 20px rgba(0,255,136,0.2); }
  .signal-badge.sell { background: rgba(255, 59, 92, 0.15); color: var(--red); border: 1px solid var(--red); box-shadow: 0 0 20px rgba(255,59,92,0.2); }
  .signal-badge.neutral { background: rgba(255, 255, 255, 0.1); color: var(--text-main); border: 1px solid var(--border-highlight); }

  .progress-bar {
    height: 8px;
    background: var(--bg-main);
    border-radius: 4px;
    overflow: hidden;
    margin-top: 5px;
  }
  .progress-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
  }
  .progress-fill.gold { background: linear-gradient(90deg, #F2C94C, #F2994A); }
  .progress-fill.silver { background: linear-gradient(90deg, #B0C4DE, #87CEFA); }

  /* Tables */
  .table-container {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    padding: 12px;
    color: var(--text-muted);
    font-weight: 500;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 12px;
    border-bottom: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255, 255, 255, 0.02); }
  
  .btn-action {
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-main);
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    transition: 0.2s;
  }
  .btn-action:hover { background: var(--border-highlight); }

  .profit-pos { color: var(--green); }
  .profit-neg { color: var(--red); }

  /* Correlation */
  .correlation-meter {
    display: flex;
    align-items: center;
    gap: 15px;
    margin-top: 15px;
  }
  .correlation-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 20px;
    color: var(--gold);
  }

</style>
</head>
<body>

<header>
  <div class="logo">MetaTrader<span style="color:var(--gold);">XM</span></div>
  <div class="sessions">
    <div class="session" id="sess-asia">Asian</div>
    <div class="session active" id="sess-london">London</div>
    <div class="session" id="sess-ny">New York</div>
  </div>
  <div class="nav-links">
    <a href="/logs" target="_blank">View Logs</a>
    <a href="/live" target="_blank">Live Stream</a>
  </div>
</header>

<main>
  <!-- TOP ROW: Metals Data -->
  <div class="grid-top">
    <!-- GOLD CARD -->
    <div class="card metal-card gold">
      <div class="card-header">
        <div class="card-title">🥇 XAUUSD (Gold)</div>
        <div class="stat-label" id="time-gold">--:--:--</div>
      </div>
      <div class="price-display">
        <span id="price-gold">2345.67</span>
        <span class="price-change up" id="change-gold">+0.45%</span>
      </div>
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-label">Spread</span>
          <span class="stat-val" id="spread-gold">12</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">ATR (14)</span>
          <span class="stat-val" id="atr-gold">18.45</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Daily High</span>
          <span class="stat-val" id="high-gold">2350.10</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Daily Low</span>
          <span class="stat-val" id="low-gold">2330.25</span>
        </div>
      </div>
    </div>

    <!-- SILVER CARD -->
    <div class="card metal-card silver">
      <div class="card-header">
        <div class="card-title">🥈 XAGUSD (Silver)</div>
        <div class="stat-label" id="time-silver">--:--:--</div>
      </div>
      <div class="price-display">
        <span id="price-silver">28.450</span>
        <span class="price-change down" id="change-silver">-0.12%</span>
      </div>
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-label">Spread</span>
          <span class="stat-val" id="spread-silver">25</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">ATR (14)</span>
          <span class="stat-val" id="atr-silver">0.650</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Daily High</span>
          <span class="stat-val" id="high-silver">28.900</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Daily Low</span>
          <span class="stat-val" id="low-silver">28.200</span>
        </div>
      </div>
    </div>
  </div>

  <!-- MIDDLE ROW: Signals, Correlation, Performance -->
  <div class="grid-mid">
    
    <!-- AI SIGNALS -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">🤖 AI Trading Signals</div>
      </div>
      
      <div style="margin-bottom: 20px;">
        <div style="display:flex; justify-content:space-between; margin-bottom: 5px;">
          <span style="font-size:12px; color:var(--gold); font-weight: 600;">XAUUSD</span>
          <span style="font-family:'JetBrains Mono'; font-size:12px;" id="conf-gold-txt">--%</span>
        </div>
        <div class="signal-badge neutral" id="sig-gold">WAITING</div>
        <div class="progress-bar"><div class="progress-fill gold" id="conf-gold-bar" style="width: 0%"></div></div>
        <div id="factors-gold" style="margin-top: 8px; min-height: 15px;"></div>
      </div>

      <div>
        <div style="display:flex; justify-content:space-between; margin-bottom: 5px;">
          <span style="font-size:12px; color:var(--silver); font-weight: 600;">XAGUSD</span>
          <span style="font-family:'JetBrains Mono'; font-size:12px;" id="conf-silver-txt">--%</span>
        </div>
        <div class="signal-badge neutral" id="sig-silver">WAITING</div>
        <div class="progress-bar"><div class="progress-fill silver" id="conf-silver-bar" style="width: 0%"></div></div>
        <div id="factors-silver" style="margin-top: 8px; min-height: 15px;"></div>
      </div>
    </div>

    <!-- PERFORMANCE & RISK -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">⚖️ Risk & Performance</div>
      </div>
      <div class="stats-grid" style="border:none; padding:0; margin:0;">
        <div class="stat-item">
          <span class="stat-label">Account Balance</span>
          <span class="stat-val" style="font-size: 20px; color: var(--gold);" id="acc-balance">--</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Equity</span>
          <span class="stat-val" style="font-size: 20px;" id="acc-equity">--</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Current P&L</span>
          <span class="stat-val" id="acc-pnl">--</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Currency</span>
          <span class="stat-val" id="acc-currency">USD</span>
        </div>
      </div>
      
      <div style="margin-top: 25px; border-top: 1px solid var(--border); padding-top: 15px;">
        <div class="card-title" style="margin-bottom: 5px;">Gold/Silver Correlation</div>
        <div class="correlation-meter">
          <span class="correlation-val" id="corr-val">0.82</span>
          <div style="flex:1;">
            <div class="progress-bar"><div class="progress-fill" style="width: 82%; background: var(--green);"></div></div>
            <div class="stat-label" style="margin-top:5px;">Highly Correlated</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ECONOMIC CALENDAR -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">📅 Economic Calendar</div>
      </div>
      <div class="table-container" style="max-height: 200px;">
        <table>
          <tbody id="eco-cal">
            <tr>
              <td style="color:var(--text-muted); font-size:11px;">14:30</td>
              <td style="color:var(--red); font-weight: 600;">USD</td>
              <td>CPI m/m</td>
            </tr>
            <tr>
              <td style="color:var(--text-muted); font-size:11px;">18:00</td>
              <td style="color:var(--red); font-weight: 600;">USD</td>
              <td>FOMC Statement</td>
            </tr>
            <tr>
              <td style="color:var(--text-muted); font-size:11px;">18:30</td>
              <td style="color:var(--red); font-weight: 600;">USD</td>
              <td>Fed Press Conference</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>

  <!-- BOTTOM ROW: Tables -->
  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
    <!-- OPEN POSITIONS -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">🟢 Open Positions</div>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th>Lots</th>
              <th>Open</th>
              <th>Current</th>
              <th>P&L (%)</th>
              <th>P&L ($)</th>
            </tr>
          </thead>
          <tbody id="pos-table">
            <!-- Data filled via JS -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- PENDING ORDERS -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">⏳ Pending Orders</div>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th>Lots</th>
              <th>Price</th>
              <th>SL</th>
              <th>TP</th>
            </tr>
          </thead>
          <tbody id="pend-table">
            <!-- Data filled via JS -->
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Trade History & Analytics Section ────────────────────────── -->
  <div style="padding: 30px; background: linear-gradient(135deg, rgba(10,14,23,0.5), rgba(242,201,76,0.02)); border-top: 1px solid var(--border);">
    <h2 style="font-size: 1.5em; margin-bottom: 20px; color: var(--text-main);">📊 Trade History & Analytics</h2>

    <!-- Performance Cards -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-bottom: 30px;">
      <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px;">
        <div style="font-size: 0.85em; color: var(--text-muted); margin-bottom: 10px;">TOTAL TRADES</div>
        <div style="font-size: 2.2em; font-weight: 600; color: var(--gold);" id="perf-total">—</div>
        <div style="font-size: 0.9em; color: var(--text-muted); margin-top: 10px;" id="perf-wr">Win Rate: —</div>
      </div>

      <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px;">
        <div style="font-size: 0.85em; color: var(--text-muted); margin-bottom: 10px;">AVERAGE PIPS</div>
        <div style="font-size: 2.2em; font-weight: 600; color: var(--silver);" id="perf-avg">—</div>
        <div style="font-size: 0.9em; color: var(--text-muted); margin-top: 10px;" id="perf-total-pips">Total: —</div>
      </div>

      <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px;">
        <div style="font-size: 0.85em; color: var(--text-muted); margin-bottom: 10px;">BEST vs WORST TRADE</div>
        <div style="font-size: 1.8em; font-weight: 600; margin-top: 5px;">
          <span style="color: var(--green);" id="perf-best">—</span> / <span style="color: var(--red);" id="perf-worst">—</span>
        </div>
        <div style="font-size: 0.9em; color: var(--text-muted); margin-top: 10px;">pips</div>
      </div>

      <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px;">
        <div style="font-size: 0.85em; color: var(--text-muted); margin-bottom: 10px;">SYMBOL PERFORMANCE</div>
        <div id="perf-symbols" style="font-size: 0.9em; color: var(--text-muted);">—</div>
      </div>
    </div>

    <!-- Equity Curve Chart -->
    <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 30px;">
      <div style="font-size: 1.1em; font-weight: 600; color: var(--text-main); margin-bottom: 15px;">Equity Curve (Cumulative P&L)</div>
      <canvas id="equityChart" height="60"></canvas>
    </div>

    <!-- Trade History Table -->
    <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 30px; overflow-x: auto;">
      <div style="font-size: 1.1em; font-weight: 600; color: var(--text-main); margin-bottom: 15px;">Trade History (Last 50)</div>
      <table id="tradesTable" style="width: 100%; font-size: 0.85em; border-collapse: collapse;">
        <thead>
          <tr style="border-bottom: 1px solid var(--border); color: var(--text-muted);">
            <th style="text-align: left; padding: 10px;">Time (Most Recent)</th>
            <th style="text-align: center; padding: 10px;">Symbol</th>
            <th style="text-align: center; padding: 10px;">Dir</th>
            <th style="text-align: center; padding: 10px;">Lots</th>
            <th style="text-align: right; padding: 10px;">Entry</th>
            <th style="text-align: right; padding: 10px;">Exit</th>
            <th style="text-align: right; padding: 10px;">Pips</th>
            <th style="text-align: right; padding: 10px;">P&L ($)</th>
            <th style="text-align: center; padding: 10px;">Duration</th>
            <th style="text-align: center; padding: 10px;">Confidence</th>
            <th style="text-align: center; padding: 10px;">Result</th>
          </tr>
        </thead>
        <tbody id="tradeRows">
          <tr><td colspan="11" style="text-align: center; padding: 20px; color: var(--text-muted);">Loading trades...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Factor Heatmap -->
    <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 20px;">
      <div style="font-size: 1.1em; font-weight: 600; color: var(--text-main); margin-bottom: 15px;">Factor Effectiveness (Win Rate %)</div>
      <table id="factorsTable" style="width: 100%; font-size: 0.8em; border-collapse: collapse;">
        <thead>
          <tr style="border-bottom: 1px solid var(--border); color: var(--text-muted);">
            <th style="text-align: left; padding: 10px;">Factor</th>
            <th style="text-align: center; padding: 10px;">Win Rate %</th>
            <th style="text-align: center; padding: 10px;">Trades</th>
            <th style="text-align: center; padding: 10px;">Avg When Win</th>
            <th style="text-align: center; padding: 10px;">Avg When Loss</th>
          </tr>
        </thead>
        <tbody id="factorRows">
          <tr><td colspan="5" style="text-align: center; padding: 20px; color: var(--text-muted);">Loading factors...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</main>

<script>
  let gPrice = 2345.67;
  let sPrice = 28.450;
  
  const pendData = []; // Real pending orders fetched via API

  function formatPrice(val, decimals) {
    return Number(val).toFixed(decimals);
  }

  // --- Real API Polling ---
  async function poll() {
    try {
      // Phase 2: All API calls need Bearer token for auth
      const token = window.__AUTH_TOKEN || 'changeme';
      const opts = { headers: { 'Authorization': `Bearer ${token}` } };
      const [status, positionsRes, history, snap] = await Promise.all([
        fetch('/api/status', opts).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/open-positions', opts).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch('/api/history', opts).then(r => r.ok ? r.json() : []).catch(() => []),
        fetch('/api/live-snapshot', opts).then(r => r.ok ? r.json() : null).catch(() => null)
      ]);
      
      if (status) renderStatus(status);
      if (positionsRes && positionsRes.positions) renderPositions(positionsRes.positions);
      if (snap) renderLiveSnap(snap);
      // No simulated fallback — only show real MT5 data

    } catch(e) {
      console.warn('poll error', e);
    }
  }

  function renderStatus(d) {
    const ac = d.account || {};
    if (ac.balance !== undefined) {
      document.getElementById('acc-balance').textContent = '$' + (+ac.balance).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      document.getElementById('acc-equity').textContent  = '$' + (+ac.equity).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      const pnl = (ac.equity || 0) - (ac.balance || 0);
      const pe = document.getElementById('acc-pnl');
      pe.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + ' ' + (ac.currency || 'USD');
      pe.className = 'stat-val ' + (pnl >= 0 ? 'profit-pos' : 'profit-neg');
      document.getElementById('acc-currency').textContent = ac.currency || 'USD';
    }
    
    if (d.symbols) {
      updateSignal('gold', d.symbols['XAUUSD']);
      updateSignal('silver', d.symbols['XAGUSD']);
    }
  }

  function updateSignal(metalId, data) {
    if (!data) return;
    const sigBadge = document.getElementById(`sig-${metalId}`);
    const confTxt = document.getElementById(`conf-${metalId}-txt`);
    const confBar = document.getElementById(`conf-${metalId}-bar`);
    
    if (sigBadge) {
      const sig = (data.signal || 'HOLD').toUpperCase();
      let scoreStr = '';
      if (data.score !== undefined) {
         scoreStr = ` (Score: ${data.score > 0 ? '+' : ''}${parseFloat(data.score).toFixed(1)})`;
      }
      sigBadge.textContent = sig + scoreStr;
      sigBadge.className = 'signal-badge ' + (sig === 'BUY' ? 'buy' : sig === 'SELL' ? 'sell' : 'neutral');
    }
    
    if (confTxt) {
      const conf = parseFloat(data.confidence || 0);
      const pct = Math.round((conf <= 1 && conf > 0 ? conf * 100 : conf));
      confTxt.textContent = pct + '%';
      if (confBar) confBar.style.width = pct + '%';
    }

    const factorDiv = document.getElementById(`factors-${metalId}`);
    if (factorDiv && data.factor_scores) {
      const factors = Object.entries(data.factor_scores)
        .filter(([k,v]) => typeof v === 'number' && v !== 0 && !k.endsWith('_regime'))
        .map(([k,v]) => `<span style="display:inline-block; margin-right:8px; font-size:10px;"><span style="color:var(--text-muted)">${k.split('_').slice(1).join('_')}:</span> <span style="color:${v > 0 ? 'var(--green)' : 'var(--red)'}">${v > 0 ? '+' : ''}${v.toFixed(1)}</span></span>`)
        .join('');
      factorDiv.innerHTML = factors || '<span style="color:var(--text-muted); font-size:10px;">No active factors</span>';
    }
  }

  function renderPositions(positions) {
    const tbody = document.getElementById('pos-table');
    tbody.innerHTML = '';
    if(!positions || positions.length === 0) {
       tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted)">No open positions</td></tr>';
       return;
    }
    positions.forEach(p => {
      const dec = p.symbol.includes('XAU') ? 2 : (p.symbol.includes('XAG') ? 3 : 5);
      const isBuy = p.direction === 'BUY';
      const pnlClass = p.profit_loss_usd >= 0 ? 'profit-pos' : 'profit-neg';

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="color:${p.symbol.includes('XAU') ? 'var(--gold)' : (p.symbol.includes('XAG') ? 'var(--silver)' : 'var(--text-main)')}; font-weight: 600;">${p.symbol}</td>
        <td class="${isBuy ? 'profit-pos' : 'profit-neg'}">${p.direction}</td>
        <td>${p.lot_size}</td>
        <td>${p.entry_price.toFixed(dec)}</td>
        <td>${p.current_price.toFixed(dec)}</td>
        <td class="${pnlClass}">${p.profit_loss_pct >= 0 ? '+' : ''}${p.profit_loss_pct.toFixed(2)}%</td>
        <td class="${pnlClass}">${p.profit_loss_usd >= 0 ? '+' : ''}$${p.profit_loss_usd.toFixed(2)}</td>
      `;
      tbody.appendChild(tr);
    });
  }
  
  function renderLiveSnap(snap) {
      if(!snap.symbols) return;
      
      const gold = snap.symbols['XAUUSD'];
      if(gold) {
          gPrice = gold.price || gPrice;
          document.getElementById('price-gold').innerText = formatPrice(gPrice, 2);
          if (gold.spread) document.getElementById('spread-gold').innerText = gold.spread;
          if (gold.atr) document.getElementById('atr-gold').innerText = formatPrice(gold.atr, 2);
          if (gold.daily_high) document.getElementById('high-gold').innerText = formatPrice(gold.daily_high, 2);
          if (gold.daily_low) document.getElementById('low-gold').innerText = formatPrice(gold.daily_low, 2);
      }
      
      const silver = snap.symbols['XAGUSD'];
      if(silver) {
          sPrice = silver.price || sPrice;
          document.getElementById('price-silver').innerText = formatPrice(sPrice, 3);
          if (silver.spread) document.getElementById('spread-silver').innerText = silver.spread;
          if (silver.atr) document.getElementById('atr-silver').innerText = formatPrice(silver.atr, 3);
          if (silver.daily_high) document.getElementById('high-silver').innerText = formatPrice(silver.daily_high, 3);
          if (silver.daily_low) document.getElementById('low-silver').innerText = formatPrice(silver.daily_low, 3);
      }
      
      // Update Times
      const now = new Date();
      document.getElementById('time-gold').innerText = now.toLocaleTimeString();
      document.getElementById('time-silver').innerText = now.toLocaleTimeString();
  }

  function updatePricesSimulated() {
    // Disabled — dashboard now uses real MT5 data only
  }

  function renderPending() {
    const tbody = document.getElementById('pend-table');
    tbody.innerHTML = '';
    pendData.forEach((p, idx) => {
      const dec = p.sym === 'XAUUSD' ? 2 : 3;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="color:${p.sym === 'XAUUSD' ? 'var(--gold)' : 'var(--silver)'}; font-weight: 600;">${p.sym}</td>
        <td>${p.type}</td>
        <td>${p.lots}</td>
        <td>${p.price.toFixed(dec)}</td>
        <td style="color:var(--red)">${p.sl.toFixed(dec)}</td>
        <td style="color:var(--green)">${p.tp.toFixed(dec)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  // Update sessions based on UTC time
  function updateSessions() {
    const h = new Date().getUTCHours();
    document.querySelectorAll('.session').forEach(el => el.classList.remove('active'));
    if (h >= 8 && h < 16) document.getElementById('sess-london').classList.add('active');
    if (h >= 13 && h < 22) document.getElementById('sess-ny').classList.add('active');
    if (h >= 22 || h < 8) document.getElementById('sess-asia').classList.add('active');
  }

  setInterval(poll, 3000);
  setInterval(updateSessions, 60000);

  // Init
  poll();
  renderPending();
  updateSessions();

  // ── Trade History & Analytics Initialization ──────────────────────────────
  let equityChart = null;

  async function loadTradeHistory() {
    try {
      const res = await fetch('/api/trades/history');
      const data = await res.json();
      const trades = (data.trades || []).slice(0, 50);
      const tbody = document.getElementById('tradeRows');
      if (!tbody) return;

      // Sort trades by date descending (most recent first)
      const sortedTrades = [...trades].sort((a, b) => {
        const dateA = new Date(a.ts || 0);
        const dateB = new Date(b.ts || 0);
        return dateB - dateA;
      });

      tbody.innerHTML = sortedTrades.length === 0
        ? '<tr><td colspan="11" style="text-align:center;padding:20px;color:var(--text-muted);">No trades yet</td></tr>'
        : sortedTrades.map(t => {
          const time = t.ts ? new Date(t.ts).toLocaleString() : '—';
          const pnlColor = t.outcome === 'WIN' ? 'color:var(--green)' : 'color:var(--red)';
          const rowBg = t.outcome === 'WIN' ? 'background:rgba(0,255,136,0.05)' : 'background:rgba(255,59,92,0.05)';
          const pipsColor = t.pips >= 0 ? 'color:var(--green)' : 'color:var(--red)';
          return `<tr style="${rowBg};border-bottom:1px solid var(--border)">
            <td style="padding:10px;font-size:0.85em">${time}</td>
            <td style="padding:10px;text-align:center;font-weight:600">${t.symbol}</td>
            <td style="padding:10px;text-align:center;color:${t.direction==='BUY'?'var(--green)':'var(--red)'};font-weight:600">${t.direction}</td>
            <td style="padding:10px;text-align:center">${(t.volume || 0).toFixed(2)}</td>
            <td style="padding:10px;text-align:right">${t.entry_price.toFixed(2)}</td>
            <td style="padding:10px;text-align:right">${t.exit_price.toFixed(2)}</td>
            <td style="padding:10px;text-align:right;${pipsColor};font-weight:600">${t.pips >= 0 ? '+' : ''}${t.pips.toFixed(1)}</td>
            <td style="padding:10px;text-align:right;${pnlColor};font-weight:600">${t.outcome === 'WIN' ? '+' : ''}${t.pnl_usd.toFixed(2)}</td>
            <td style="padding:10px;text-align:center">${Math.round(t.duration_min)}m</td>
            <td style="padding:10px;text-align:center">${(t.confidence*100).toFixed(0)}%</td>
            <td style="padding:10px;text-align:center;color:${t.outcome === 'WIN' ? 'var(--green)' : 'var(--red)'};font-weight:600">${t.outcome}</td>
          </tr>`;
        }).join('');
    } catch (e) {
      console.error('Trade history load failed:', e);
    }
  }

  async function loadPerformance() {
    try {
      const res = await fetch('/api/trades/performance');
      const p = await res.json();

      const el_total = document.getElementById('perf-total');
      const el_wr = document.getElementById('perf-wr');
      const el_avg = document.getElementById('perf-avg');
      const el_pips = document.getElementById('perf-total-pips');
      const el_best = document.getElementById('perf-best');
      const el_worst = document.getElementById('perf-worst');
      const el_symbols = document.getElementById('perf-symbols');

      if (el_total) el_total.textContent = p.total_trades || '—';
      if (el_wr) el_wr.textContent = `Win Rate: ${p.win_rate || 0}% (${p.wins || 0}W / ${p.losses || 0}L)`;
      if (el_avg) el_avg.textContent = (p.avg_pips || 0).toFixed(1);
      if (el_pips) el_pips.textContent = `Total: ${(p.total_pips || 0).toFixed(1)} pips`;
      if (el_best) el_best.textContent = (p.best_trade_pips || 0).toFixed(1);
      if (el_worst) el_worst.textContent = (p.worst_trade_pips || 0).toFixed(1);

      if (el_symbols) {
        const symHtml = Object.entries(p.by_symbol || {})
          .map(([sym, s]) => `<div>${sym}: ${s.win_rate}% (${s.wins}/${s.losses+s.wins})</div>`)
          .join('');
        el_symbols.innerHTML = symHtml || '—';
      }

      drawEquityCurve();
    } catch (e) {
      console.error('Performance load failed:', e);
    }
  }

  async function drawEquityCurve() {
    try {
      const res = await fetch('/api/trades/history');
      const data = await res.json();
      const trades = data.trades || [];
      const canvas = document.getElementById('equityChart');
      if (!canvas) return;

      let cumPnL = 0;
      const cumulativePnL = [];
      const labels = [];

      [...trades].reverse().forEach((t, idx) => {
        cumPnL += (t.pnl_usd || 0);
        cumulativePnL.push(cumPnL);
        labels.push((idx + 1).toString());
      });

      const ctx = canvas.getContext('2d');
      if (equityChart) equityChart.destroy();
      equityChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Cumulative P&L (USD)',
            data: cumulativePnL,
            borderColor: cumulativePnL[cumulativePnL.length-1] > 0 ? 'var(--green)' : 'var(--red)',
            backgroundColor: cumulativePnL[cumulativePnL.length-1] > 0
              ? 'rgba(0,255,136,0.1)'
              : 'rgba(255,59,92,0.1)',
            tension: 0.4,
            fill: true,
            borderWidth: 2,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: { legend: { display: false } },
          scales: {
            y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: 'var(--text-muted)' } },
            x: { grid: { display: false }, ticks: { color: 'var(--text-muted)' } }
          }
        }
      });
    } catch (e) {
      console.error('Equity curve failed:', e);
    }
  }

  async function loadFactors() {
    try {
      const res = await fetch('/api/trades/factors');
      const factors = await res.json();
      const tbody = document.getElementById('factorRows');
      if (!tbody) return;

      const entries = Object.entries(factors || {})
        .sort((a, b) => b[1].win_rate - a[1].win_rate)
        .slice(0, 15);

      tbody.innerHTML = entries.length === 0
        ? '<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-muted);">No factor data</td></tr>'
        : entries.map(([name, data]) => {
          const bgIntensity = Math.min(data.win_rate / 100 * 0.3, 0.3);
          return `<tr style="background:rgba(242,201,76,${bgIntensity});border-bottom:1px solid var(--border)">
            <td style="padding:10px">${name}</td>
            <td style="padding:10px;text-align:center;font-weight:600;color:${data.win_rate > 50 ? 'var(--green)' : 'var(--red)'}">${data.win_rate.toFixed(1)}%</td>
            <td style="padding:10px;text-align:center">${data.sample_size}</td>
            <td style="padding:10px;text-align:center">${(data.avg_when_win || 0).toFixed(2)}</td>
            <td style="padding:10px;text-align:center">${(data.avg_when_loss || 0).toFixed(2)}</td>
          </tr>`;
        }).join('');
    } catch (e) {
      console.error('Factors load failed:', e);
    }
  }

  // Load on page load
  loadTradeHistory();
  loadPerformance();
  loadFactors();

  // Auto-refresh every 30 seconds
  setInterval(() => {
    loadTradeHistory();
    loadPerformance();
    loadFactors();
  }, 30000);

</script>
</body>
</html>"""


# ── Live Data Viewer (/live) ───────────────────────────────────────────────────
LIVE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live Stream — MetatradeXM</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#06090f;--bg1:#0b1017;--bg2:rgba(255,255,255,.04);--bg3:rgba(255,255,255,.07);
  --border:rgba(255,255,255,.08);--border2:rgba(255,255,255,.14);
  --cyan:#00e5ff;--purple:#a855f7;--green:#00ff88;--red:#ff3b5c;--amber:#fbbf24;
  --text:#e2e8f0;--muted:#64748b;
  --font:'Inter',system-ui,sans-serif;--mono:'JetBrains Mono','Fira Code',monospace;
}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;line-height:1.5;min-height:100vh;
  background-image:radial-gradient(ellipse 90% 55% at 50% -10%,rgba(0,229,255,.05) 0%,transparent 100%),
    radial-gradient(ellipse 55% 45% at 85% 85%,rgba(168,85,247,.04) 0%,transparent 100%);}
header{position:sticky;top:0;z-index:100;background:rgba(6,9,15,.9);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);padding:0 24px;height:52px;display:flex;align-items:center;gap:14px;}
.logo{font-size:15px;font-weight:700;color:var(--cyan);letter-spacing:-.3px}
.logo span{color:var(--text);opacity:.7}
.pill{display:flex;align-items:center;gap:6px;padding:4px 12px;border-radius:99px;font-size:11px;font-weight:600;border:1px solid;transition:all .3s;}
.pill.ok{border-color:var(--green);color:var(--green);background:rgba(0,255,136,.1)}
.pill.bad{border-color:var(--red);color:var(--red);background:rgba(255,59,92,.1)}
.pill.wait{border-color:var(--amber);color:var(--amber);background:rgba(251,191,36,.1)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:pulse 1.4s infinite}
.pill.ok .dot{animation:pulse 1.4s infinite}
.pill.bad .dot,.pill.wait .dot{animation:none;opacity:.8}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.ml{margin-left:auto}.mono{font-family:var(--mono)}
.btn{padding:4px 10px;border-radius:6px;border:1px solid var(--border2);background:var(--bg2);
  color:var(--text);font-size:11px;cursor:pointer;transition:background .2s;}
.btn:hover{background:var(--bg3)}
a.back{font-size:12px;color:var(--muted);text-decoration:none;padding:4px 10px;border-radius:6px;
  border:1px solid var(--border);transition:all .2s;}
a.back:hover{color:var(--text);background:var(--bg2)}
main{padding:20px 24px;display:flex;flex-direction:column;gap:18px}
#banner{padding:12px 16px;border-radius:10px;border:1px solid;display:flex;align-items:center;gap:10px;font-size:13px;transition:all .4s;}
#banner.ok{border-color:rgba(0,255,136,.25);background:rgba(0,255,136,.06);color:var(--green)}
#banner.bad{border-color:rgba(255,59,92,.25);background:rgba(255,59,92,.06);color:var(--red)}
#banner.wait{border-color:rgba(251,191,36,.25);background:rgba(251,191,36,.06);color:var(--amber)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
.card{background:var(--bg1);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.card-hdr{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sym{font-size:16px;font-weight:700;color:var(--cyan)}
.price{font-family:var(--mono);font-size:20px;font-weight:500;margin-left:auto}
.chg{font-family:var(--mono);font-size:11px;padding:2px 7px;border-radius:5px}
.chg.up{background:rgba(0,255,136,.12);color:var(--green)}
.chg.dn{background:rgba(255,59,92,.12);color:var(--red)}
.age{font-size:10px;color:var(--muted);font-family:var(--mono)}
.tabs{display:flex;gap:6px;padding:10px 16px 0;border-bottom:1px solid var(--border)}
.tab{padding:5px 14px;border-radius:7px 7px 0 0;font-size:11px;font-weight:600;cursor:pointer;
  border:1px solid transparent;border-bottom:none;color:var(--muted);transition:all .2s;}
.tab:hover{color:var(--text);background:var(--bg2)}
.tab.active{background:var(--bg2);border-color:var(--border);border-bottom:1px solid var(--bg1);color:var(--cyan);margin-bottom:-1px;}
.tab.disabled{opacity:.3;cursor:not-allowed}
.inds{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:8px;padding:14px 16px}
.ind{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px}
.il{font-size:10px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.4px}
.iv{font-family:var(--mono);font-size:14px;font-weight:500;margin-top:3px;word-break:break-all}
.iv.g{color:var(--green)}.iv.r{color:var(--red)}.iv.a{color:var(--amber)}.iv.m{color:var(--muted)}
.sbar{display:flex;flex-wrap:wrap;gap:12px;padding:12px 16px;background:var(--bg2);border-top:1px solid var(--border)}
.sc{display:flex;flex-direction:column;gap:2px}
.sl{font-size:9px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.sv{font-family:var(--mono);font-size:13px;font-weight:600}
.raw-card{background:var(--bg1);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.raw-hdr{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--muted)}
.raw-body{font-family:var(--mono);font-size:11px;line-height:1.6;max-height:280px;overflow-y:auto;padding:12px 16px;color:rgba(255,255,255,.5)}
.re{border-bottom:1px solid var(--border);padding:5px 0}.re:last-child{border-bottom:none}
.rt{color:var(--cyan);margin-right:8px}.rtype{color:var(--purple);margin-right:6px}
::-webkit-scrollbar{width:4px;height:4px}::-webkit-scrollbar-track{background:var(--bg1)}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:2px}
</style>
</head>
<body>
<header>
  <div class="logo">MetatradeXM <span>/ Live Stream</span></div>
  <div id="pill" class="pill wait"><span class="dot"></span><span id="pillTxt">Connecting…</span></div>
  <span class="mono" style="font-size:11px;color:var(--muted)" id="wsEndpoint">Supabase live source</span>
  <div class="ml" style="display:flex;gap:8px;align-items:center">
    <span style="font-size:11px;color:var(--muted)">Msgs: <b id="mc" style="color:var(--text)">0</b></span>
    <span style="font-size:11px;color:var(--muted)">Last: <b id="lt" class="mono" style="color:var(--cyan)">—</b></span>
    <button class="btn" onclick="reconnect()">↺ Reconnect</button>
    <a href="/" class="back">← Dashboard</a>
  </div>
</header>
<main>
  <div id="banner" class="wait"><span id="bi">⏳</span><span id="bm">Connecting to Supabase live source…</span></div>
  <div class="grid2" id="grid"></div>
  <div class="raw-card">
    <div class="raw-hdr">📡 Supabase Live Events
      <span style="margin-left:auto;font-size:10px;cursor:pointer;color:var(--cyan)" onclick="clrLog()">clear</span>
    </div>
    <div class="raw-body" id="log"><div style="color:var(--muted);padding:6px 0">Waiting…</div></div>
  </div>
</main>
<script>
const WS=null, POLL='/api/live-snapshot';
let ws=null,mc=0,log=[],latest={},atf={},rt=null,usePoll=false;

function connect(){
  setState('ok');
  startPoll();
  if(!WS){addLog('system','Using Supabase-backed HTTP live source');return}
  try{
    ws=new WebSocket(WS);
    ws.onopen=()=>{setState('ok');addLog('system','WebSocket connected to '+WS);usePoll=false};
    ws.onmessage=(e)=>{mc++;document.getElementById('mc').textContent=mc;
      document.getElementById('lt').textContent=new Date().toLocaleTimeString();
      let m;try{m=JSON.parse(e.data)}catch(err){return}
      addLog(m.type||'msg',e.data.length+'B');handle(m)};
    ws.onerror=()=>addLog('error','WebSocket error');
    ws.onclose=(e)=>{setState('bad');addLog('system','Closed ('+e.code+') — retrying in 5s…');
      rt=setTimeout(()=>{if(!usePoll)connect()},5000)};
    // Fallback to polling if WS not established in 6s
    setTimeout(()=>{if(ws.readyState!==1){addLog('system','WS timeout — switching to HTTP polling');
      usePoll=true;ws.close();startPoll()}},6000);
    // Start HTTP polling immediately for AI indicators
    startPoll();
  }catch(e){addLog('error','Cannot create WebSocket: '+e);startPoll()}
}

function startPoll(){
  if(window._pollStarted) return;
  window._pollStarted = true;
  addLog('system','Started HTTP poll mode for AI indicators');
  fetchPoll();setInterval(fetchPoll,15000);}
function fetchPoll(){
  // Phase 2: Include auth in fallback poll
  const token = window.__AUTH_TOKEN || 'changeme';
  const opts = { headers: { 'Authorization': `Bearer ${token}` } };
  Promise.all([
    fetch('/api/live-snapshot', opts).then(r=>r.json()).catch(()=>({})),
    fetch('/api/status', opts).then(r=>r.json()).catch(()=>({}))
  ]).then(([snap, status]) => {
    mc++;
    document.getElementById('mc').textContent=mc;
    document.getElementById('lt').textContent=new Date().toLocaleTimeString();
    
    // Merge status indicators into snapshot symbols.
    // status = /api/status (bot_status.json) — has score, signal, confidence, indicators.
    // snap   = /api/live-snapshot (TV server)  — has timeframes (M15/H1/H4/D1).
    // Merge both so live stream shows TFS tabs, SCORE, TREND STRONG, BB SQUEEZE.
    const mergedSymbols = snap.symbols || {};
    if (status.symbols) {
      for (const sym in status.symbols) {
        if (!mergedSymbols[sym]) mergedSymbols[sym] = {};
        const ss = status.symbols[sym];
        // Indicators from bot status already include score/trend_strong/bb_squeeze
        mergedSymbols[sym].indicators = ss.indicators;
        mergedSymbols[sym].signal = ss.signal;
        mergedSymbols[sym].confidence = ss.confidence;
        // Timeframes from TV-server snapshot (already merged via /api/live-snapshot)
        if (!mergedSymbols[sym].timeframes && ss.timeframes)
          mergedSymbols[sym].timeframes = ss.timeframes;
        // If timeframes present, inject score into M15 indicators so render shows it
        if (mergedSymbols[sym].timeframes && mergedSymbols[sym].timeframes.M15
            && ss.score !== undefined)
          mergedSymbols[sym].timeframes.M15.score = ss.score;
      }
    }
    
    addLog('poll','HTTP snapshot + status — '+Object.keys(mergedSymbols).join(', '));
    handle({type:'snapshot', data: {symbols: mergedSymbols}});
    if(ws && ws.readyState===1) setState('ok');
  }).catch(e=>addLog('error','Poll failed: '+e));}
function reconnect(){if(rt)clearTimeout(rt);usePoll=false;if(ws)ws.close();connect()}

function handle(m){
  const MAP = {'GOLD.i#': 'XAUUSD', 'SILVER.i#': 'XAGUSD'};
  const sym = MAP[m.symbol] || m.symbol;

  if(m.type==='snapshot'){const s=m.data?.symbols||{};Object.assign(latest,s);renderAll()}
  else if(m.type==='indicator_update'&&m.symbol){
    if(!latest[sym])latest[sym]={};
    if(m.indicators)latest[sym].indicators=m.indicators;
    if(m.timeframes)latest[sym].timeframes=m.timeframes;
    render(sym);
  }
  else if(m.type==='candles' && m.candles && m.candles.length) {
    if(!sym) return;
    if(!latest[sym])latest[sym]={indicators:{}};
    latest[sym].indicators = latest[sym].indicators || {};
    latest[sym].indicators.price = m.candles[m.candles.length-1].c;
    latest[sym]._last_update = Math.round(Date.now()/1000);
    render(sym);
  }
  else if(m.type==='ticks' && m.ticks && m.ticks.length) {
    if(!sym) return;
    if(!latest[sym])latest[sym]={indicators:{}};
    latest[sym].indicators = latest[sym].indicators || {};
    latest[sym].indicators.price = m.ticks[m.ticks.length-1].bid;
    latest[sym]._last_update = Math.round(Date.now()/1000);
    render(sym);
  }
}

function renderAll(){Object.keys(latest).sort().forEach(render)}
function render(sym){
  const d=latest[sym];if(!d)return;
  const g=document.getElementById('grid');
  let c=document.getElementById('c-'+sym);
  if(!c){c=document.createElement('div');c.className='card';c.id='c-'+sym;g.appendChild(c);atf[sym]='M15'}
  const tfs=d.timeframes||{};const tf=atf[sym]||'M15';
  const ind=(tfs[tf]||d.indicators)||{};
  const age=d._last_update?Math.round(Date.now()/1000-d._last_update):null;
  const p=ind.price||0,ch=ind.price_change||0;
  const tabs=['M15','H1','H4','D1'].map(t=>`<div class="tab${t===tf?' active':''}${tfs[t]?'':' disabled'}" onclick="setTf('${sym}','${t}')">${t}</div>`).join('');
  c.innerHTML=`
    <div class="card-hdr">
      <span class="sym">${sym}</span>
      <span class="price">${p>0?p.toFixed(sym.includes('XAU')?2:3):'—'}</span>
      ${ch!==0?`<span class="chg ${ch>0?'up':'dn'}">${ch>0?'+':''}${ch.toFixed(3)}%</span>`:''}
      <span class="age ml">${age!==null?age+'s ago':'waiting…'}</span>
    </div>
    <div class="tabs">${tabs}</div>
    <div class="inds">${buildInds(ind,sym)}</div>
    <div class="sbar">
      ${sc('TFs',Object.keys(tfs).join(' · ')||'—')}
      ${sc('Score',ind.score!==undefined?(ind.score>0?'+':'')+Number(ind.score).toFixed(1):'—')}
      ${sc('Trend Strong',ind.trend_strong?'YES':ind.trend_strong===false?'NO':'—')}
      ${sc('BB Squeeze',ind.bb_squeeze?'🔴 YES':ind.bb_squeeze===false?'NO':'—')}
    </div>`;}

function setTf(sym,tf){atf[sym]=tf;render(sym)}

function buildInds(i,sym){
  if(!i||!Object.keys(i).length)return`<div style="grid-column:1/-1;color:var(--muted);padding:12px 0">No data yet</div>`;
  const rows=[
    ['ADX',      f(i.adx,1),      adxC(i.adx)],
    ['+DI',      f(i.plus_di,1),  'g'],
    ['-DI',      f(i.minus_di,1), 'r'],
    ['RSI',      f(i.rsi,1),      rsiC(i.rsi)],
    ['EMA Trend',i.ema_trend||'—',tC(i.ema_trend)],
    ['MACD',     i.macd_signal||'—',mC(i.macd_signal)],
    ['MACD Hist',f(i.macd_hist,5),i.macd_hist>0?'g':i.macd_hist<0?'r':'a'],
    ['BB Pos',   i.bb_position||'—',bbC(i.bb_position)],
    ['Stoch K',  f(i.stoch_k,1),  rsiC(i.stoch_k)],
    ['Stoch D',  f(i.stoch_d,1),  rsiC(i.stoch_d)],
    ['Stoch X',  i.stoch_cross||'—',mC(i.stoch_cross)],
    ['Williams%R',f(i.williams_r,1),wrC(i.williams_r)],
    ['ATR',      f(i.atr,4),      'a'],
    ['EMA 20',   f(i.ema20,2),    'm'],
    ['EMA 50',   f(i.ema50,2),    'm'],
    ['EMA 200',  f(i.ema200,2),   'm'],
    ['Vol Ratio',f(i.vol_ratio,2),i.vol_ratio>1.2?'g':i.vol_ratio<0.8?'r':'a'],
    ['Candle',   fs(i.candle_pattern_score),i.candle_pattern_score>0?'g':i.candle_pattern_score<0?'r':'m'],
  ];
  return rows.map(([l,v,c])=>`<div class="ind"><div class="il">${l}</div><div class="iv ${c}">${v}</div></div>`).join('')}

function f(v,d=2){if(v==null||v==='')return'—';const n=parseFloat(v);return isNaN(n)?String(v):n.toFixed(d)}
function fs(v){if(v==null)return'—';const n=parseFloat(v);return isNaN(n)?'—':(n>0?'+':'')+n.toFixed(1)}
function adxC(v){const n=parseFloat(v);return n>25?'g':n>15?'a':'r'}
function rsiC(v){const n=parseFloat(v);return n>60?'g':n<40?'r':'a'}
function tC(v){return!v?'m':v.includes('BULL')?'g':v.includes('BEAR')?'r':'a'}
function mC(v){return!v?'m':v.includes('BULL')?'g':v.includes('BEAR')?'r':'a'}
function bbC(v){return!v?'m':v.includes('ABOVE')?'g':v.includes('BELOW')?'r':'a'}
function wrC(v){const n=parseFloat(v);return n>-20?'r':n<-80?'g':'a'}
function sc(l,v){return`<div class="sc"><div class="sl">${l}</div><div class="sv">${v}</div></div>`}

function setState(s){
  const p=document.getElementById('pill'),t=document.getElementById('pillTxt');
  const ban=document.getElementById('banner');
  const labels={ok:'Live',bad:'Disconnected',wait:'Connecting…'};
  const icons={ok:'🟢',bad:'🔴',wait:'⏳'};
  const msgs={
    ok:'Connected — streaming live dashboard data from Supabase',
    bad:'⚡ Connection lost — will retry in 5s (or using HTTP poll fallback)',
    wait:'Connecting to Supabase live source…'};
  p.className='pill '+s;t.textContent=labels[s];
  ban.className=s;document.getElementById('bi').textContent=icons[s];
  document.getElementById('bm').textContent=msgs[s]}

function addLog(type,detail){
  const ts=new Date().toLocaleTimeString('en-GB',{hour12:false});
  log.unshift({ts,type,detail:String(detail).substring(0,180)});
  if(log.length>50)log=log.slice(0,50);
  const el=document.getElementById('log');
  el.innerHTML=log.map(e=>`<div class="re"><span class="rt">${e.ts}</span><span class="rtype">[${e.type}]</span>${esc(e.detail)}</div>`).join('')}
function clrLog(){log=[];document.getElementById('log').innerHTML='<div style="color:var(--muted)">Cleared.</div>'}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

connect();
// Refresh "Xs ago" counters every second
setInterval(()=>{Object.keys(latest).forEach(sym=>{
  const el=document.querySelector(`#c-${sym} .age`);
  const d=latest[sym];
  if(el&&d?._last_update)el.textContent=Math.round(Date.now()/1000-d._last_update)+'s ago'})},1000);

// ── Trade History & Analytics Updates ──────────────────────────────
let equityChart = null;

async function loadTradeHistory() {
  try {
    const res = await fetch('/api/trades/history');
    const data = await res.json();
    const trades = (data.trades || []).slice(0, 50);

    const tbody = document.getElementById('tradeRows');
    tbody.innerHTML = trades.length === 0
      ? '<tr><td colspan="11" style="text-align:center;padding:20px;color:var(--text-muted);">No trades yet</td></tr>'
      : trades.map(t => {
        const time = t.ts ? new Date(t.ts).toLocaleString() : '—';
        const pnlColor = t.outcome === 'WIN' ? 'color:var(--green)' : 'color:var(--red)';
        const rowBg = t.outcome === 'WIN' ? 'background:rgba(0,255,136,0.05)' : 'background:rgba(255,59,92,0.05)';
        return `<tr style="${rowBg};border-bottom:1px solid var(--border)">
          <td style="padding:10px">${time}</td>
          <td style="padding:10px;text-align:center">${t.symbol}</td>
          <td style="padding:10px;text-align:center;color:${t.direction==='BUY'?'var(--green)':'var(--red)'}">${t.direction}</td>
          <td style="padding:10px;text-align:center">${(t.volume || 0).toFixed(2)}</td>
          <td style="padding:10px;text-align:right">${t.entry_price.toFixed(2)}</td>
          <td style="padding:10px;text-align:right">${t.exit_price.toFixed(2)}</td>
          <td style="padding:10px;text-align:right">${t.pips.toFixed(1)}</td>
          <td style="padding:10px;text-align:right;${pnlColor}">${t.outcome === 'WIN' ? '+' : ''}${t.pnl_usd.toFixed(2)}</td>
          <td style="padding:10px;text-align:center">${Math.round(t.duration_min)}m</td>
          <td style="padding:10px;text-align:center">${(t.confidence*100).toFixed(0)}%</td>
          <td style="padding:10px;text-align:center;color:${t.outcome === 'WIN' ? 'var(--green)' : 'var(--red)'}">${t.outcome}</td>
        </tr>`;
      }).join('');
  } catch (e) {
    console.error('Trade history load failed:', e);
  }
}

async function loadPerformance() {
  try {
    const res = await fetch('/api/trades/performance');
    const p = await res.json();

    document.getElementById('perf-total').textContent = p.total_trades || '—';
    document.getElementById('perf-wr').textContent = `Win Rate: ${p.win_rate || 0}% (${p.wins || 0}W / ${p.losses || 0}L)`;
    document.getElementById('perf-avg').textContent = (p.avg_pips || 0).toFixed(1);
    document.getElementById('perf-total-pips').textContent = `Total: ${(p.total_pips || 0).toFixed(1)} pips`;
    document.getElementById('perf-best').textContent = (p.best_trade_pips || 0).toFixed(1);
    document.getElementById('perf-worst').textContent = (p.worst_trade_pips || 0).toFixed(1);

    const symHtml = Object.entries(p.by_symbol || {})
      .map(([sym, s]) => `<div>${sym}: ${s.win_rate}% (${s.wins}/${s.losses+s.wins})</div>`)
      .join('');
    document.getElementById('perf-symbols').innerHTML = symHtml || '—';

    // Draw equity curve
    drawEquityCurve();
  } catch (e) {
    console.error('Performance load failed:', e);
  }
}

async function drawEquityCurve() {
  try {
    const res = await fetch('/api/trades/history');
    const data = await res.json();
    const trades = data.trades || [];

    let cumPnL = 0;
    const cumulativePnL = [];
    const labels = [];

    trades.reverse().forEach((t, idx) => {
      cumPnL += (t.pnl_usd || 0);
      cumulativePnL.push(cumPnL);
      labels.push((idx + 1).toString());
    });

    const canvas = document.getElementById('equityChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    if (equityChart) equityChart.destroy();
    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Cumulative P&L (USD)',
          data: cumulativePnL,
          borderColor: cumulativePnL[cumulativePnL.length-1] > 0 ? 'var(--green)' : 'var(--red)',
          backgroundColor: cumulativePnL[cumulativePnL.length-1] > 0
            ? 'rgba(0,255,136,0.1)'
            : 'rgba(255,59,92,0.1)',
          tension: 0.4,
          fill: true,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 6,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { display: false } },
        scales: {
          y: {
            grid: { color: 'rgba(255,255,255,0.05)' },
            ticks: { color: 'var(--text-muted)' }
          },
          x: {
            grid: { display: false },
            ticks: { color: 'var(--text-muted)' }
          }
        }
      }
    });
  } catch (e) {
    console.error('Equity curve draw failed:', e);
  }
}

async function loadFactors() {
  try {
    const res = await fetch('/api/trades/factors');
    const factors = await res.json();

    const tbody = document.getElementById('factorRows');
    const entries = Object.entries(factors || {})
      .sort((a, b) => b[1].win_rate - a[1].win_rate)
      .slice(0, 15);

    tbody.innerHTML = entries.length === 0
      ? '<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-muted);">No factor data</td></tr>'
      : entries.map(([name, data]) => {
        const bgIntensity = Math.min(data.win_rate / 100 * 0.3, 0.3);
        return `<tr style="background:rgba(242,201,76,${bgIntensity});border-bottom:1px solid var(--border)">
          <td style="padding:10px">${name}</td>
          <td style="padding:10px;text-align:center;font-weight:600;color:${data.win_rate > 50 ? 'var(--green)' : 'var(--red)'}">${data.win_rate.toFixed(1)}%</td>
          <td style="padding:10px;text-align:center">${data.sample_size}</td>
          <td style="padding:10px;text-align:center">${(data.avg_when_win || 0).toFixed(2)}</td>
          <td style="padding:10px;text-align:center">${(data.avg_when_loss || 0).toFixed(2)}</td>
        </tr>`;
      }).join('');
  } catch (e) {
    console.error('Factors load failed:', e);
  }
}

// Load all analytics on page load
window.addEventListener('load', () => {
  loadTradeHistory();
  loadPerformance();
  loadFactors();
});

// Refresh every 30 seconds
setInterval(() => {
  loadTradeHistory();
  loadPerformance();
  loadFactors();
}, 30000);
</script>
</body>
</html>"""


# ── Trading Logs Viewer (/logs) ────────────────────────────────────────────────
LOGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Logs — MetatradeXM</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#06090f;--bg1:#0b1017;--bg2:rgba(255,255,255,.04);
  --border:rgba(255,255,255,.08);--border2:rgba(255,255,255,.13);
  --cyan:#00e5ff;--green:#00ff88;--amber:#fbbf24;--red:#ff3b5c;--purple:#a855f7;
  --text:#e2e8f0;--muted:#64748b;
  --font:'Inter',system-ui,sans-serif;--mono:'JetBrains Mono','Fira Code',monospace;
}
html{height:100%}body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:12px;height:100vh;display:flex;flex-direction:column;overflow:hidden;
  background-image:radial-gradient(ellipse 90% 55% at 50% -10%,rgba(0,229,255,.05) 0%,transparent 100%);}
header{background:rgba(6,9,15,.92);backdrop-filter:blur(14px);border-bottom:1px solid var(--border);
  padding:0 16px;height:44px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.logo{color:var(--cyan);font-family:var(--mono);font-size:13px;font-weight:500}
.back{color:var(--muted);text-decoration:none;font-size:11px;margin-left:10px;transition:color .2s}
.back:hover{color:var(--cyan)}
.hdr-r{margin-left:auto;display:flex;align-items:center;gap:10px;font-size:10px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.toolbar{display:flex;align-items:center;gap:8px;padding:8px 16px;background:var(--bg1);border-bottom:1px solid var(--border);flex-shrink:0}
.btn{padding:4px 10px;border:1px solid var(--border2);border-radius:5px;background:var(--bg2);color:var(--text);font-family:var(--mono);font-size:11px;cursor:pointer;transition:all .2s}
.btn:hover{border-color:var(--cyan);color:var(--cyan)}
.filter-btns{display:flex;gap:4px;margin-left:auto}
.fbtn{padding:3px 8px;border-radius:10px;font-size:10px;font-family:var(--mono);cursor:pointer;border:1px solid var(--border2);background:var(--bg2);color:var(--muted);transition:all .2s}
.fbtn.active{border-color:var(--cyan);color:var(--cyan);background:rgba(0,229,255,.1)}
#logs{flex:1;overflow-y:auto;padding:8px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px;align-content:start}
@media(max-width:768px){#logs{grid-template-columns:1fr}}
@media(max-width:480px){#logs{grid-template-columns:1fr;padding:6px 12px}}
.signal-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px;backdrop-filter:blur(6px)}
.signal-card:hover{border-color:var(--border2);box-shadow:0 0 12px rgba(0,229,255,.1)}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;border-bottom:1px solid var(--border);padding-bottom:6px}
.symbol{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--cyan)}
.time{font-family:var(--mono);font-size:9px;color:var(--muted)}
.signal-badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;font-family:var(--mono)}
.signal-badge.BUY{background:rgba(0,255,136,.2);color:var(--green);border:1px solid rgba(0,255,136,.5)}
.signal-badge.SELL{background:rgba(255,59,92,.2);color:var(--red);border:1px solid rgba(255,59,92,.5)}
.signal-badge.HOLD{background:rgba(251,191,36,.15);color:var(--amber);border:1px solid rgba(251,191,36,.4)}
.confidence-section{margin:8px 0}
.conf-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;display:flex;justify-content:space-between}
.conf-bar{height:6px;background:rgba(255,255,255,.05);border-radius:3px;overflow:hidden;border:1px solid var(--border)}
.conf-fill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));border-radius:3px;transition:width .3s ease}
.price-section{margin:8px 0;padding:8px;background:rgba(0,229,255,.03);border-radius:4px;border-left:2px solid var(--cyan)}
.price-row{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10px;margin:3px 0}
.label{color:var(--muted)}
.value{color:var(--text);font-weight:500}
.value.up{color:var(--green)}
.value.down{color:var(--red)}
.indicators{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin:8px 0;font-size:9px}
.ind-item{padding:4px;background:rgba(255,255,255,.02);border-radius:3px;border:1px solid var(--border)}
.ind-label{color:var(--muted);font-size:8px}
.ind-value{font-family:var(--mono);color:var(--text);font-weight:500}
.action-section{margin:8px 0;padding:6px;background:rgba(255,255,255,.02);border-radius:3px;border-left:2px solid var(--amber)}
.action{font-size:9px;color:var(--amber)}
.action.success{color:var(--green);border-left-color:var(--green)}
.action.skip{color:var(--muted);border-left-color:var(--muted)}
#status-bar{padding:6px 16px;background:var(--bg1);border-top:1px solid var(--border);font-family:var(--mono);font-size:10px;color:var(--muted);flex-shrink:0;display:flex;gap:20px}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:var(--bg1)}::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:3px}::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.2)}
</style>
</head>
<body>
<header>
  <div class="logo">📊 Trading Signals<a class="back" href="/">← Dashboard</a></div>
  <div class="hdr-r"><span id="lastUpdate">—</span><div class="dot"></div></div>
</header>
<div class="toolbar">
  <button class="btn" onclick="loadLogs()">⟳ Refresh</button>
  <button class="btn" onclick="downloadLogs()">⬇ Download</button>
  <div class="filter-btns">
    <button class="fbtn active" onclick="setSymbol('all',this)">ALL</button>
    <button class="fbtn" onclick="setSymbol('XAUUSD',this)">XAUUSD</button>
    <button class="fbtn" onclick="setSymbol('XAGUSD',this)">XAGUSD</button>
  </div>
</div>
<div id="positions-banner" style="background:linear-gradient(135deg,rgba(0,229,255,.1) 0%,rgba(168,85,247,.05) 100%);border-bottom:2px solid rgba(0,229,255,.3);padding:14px 16px;display:none;backdrop-filter:blur(8px)">
  <div style="font-size:10px;color:#64748b;margin-bottom:10px;text-transform:uppercase;letter-spacing:.12em;font-weight:600">💰 Open Trades</div>
  <div id="position-content" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;grid-auto-rows:max-content">Loading...</div>
</div>
<div style="display:flex;flex:1;overflow:hidden;gap:0">
  <div style="flex:0.6;display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--border)">
    <div style="padding:8px 16px;border-bottom:1px solid var(--border);font-size:10px;color:var(--muted);font-weight:600">📊 SIGNALS</div>
    <div id="logs" style="flex:1;overflow-y:auto;padding:8px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px;align-content:start">Loading signals...</div>
  </div>
  <div style="flex:0.4;display:flex;flex-direction:column;overflow:hidden">
    <div style="padding:8px 16px;border-bottom:1px solid var(--border);font-size:10px;color:var(--muted);font-weight:600">📋 RAW LOGS</div>
    <div id="raw-logs" style="flex:1;overflow-y:auto;padding:8px 16px;font-family:var(--mono);font-size:9px;color:var(--text);white-space:pre-wrap;word-break:break-word">Loading logs...</div>
  </div>
</div>
<div id="status-bar">
  <span id="count">0 signals</span>
  <span id="buy-count" style="color:var(--green)">0 BUY</span>
  <span id="sell-count" style="color:var(--red)">0 SELL</span>
</div>
<script>
let allLogs=[], symbolFilter='all';

function parseSignals(logs){
  // Walk backwards, build latest state per symbol by grouping nearby lines
  const signals={};
  let matched = 0;
  for(let i=logs.length-1;i>=0;i--){
    const line=logs[i];
    // Only process SIGNAL lines as anchors
    const sm=/SIGNAL.*?\|\s*([\w]+)\s*\|\s*(BUY|SELL|HOLD)\s*([\d.]+)%.*?score\s*([\+\-]?[\d.]+)/i.exec(line);
    if(!sm) continue;
    matched++;
    const symbol=sm[1], signal=sm[2], conf=parseFloat(sm[3]), score=parseFloat(sm[4]);
    if(signals[symbol]) continue; // already have latest for this symbol

    // Extract timestamp from this line
    const ts=/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/.exec(line);
    const time=ts?ts[1].split(' ')[1]:'--:--:--';
    const mtf=/\[MTF\s*[×x]([\d.]+)/.exec(line);
    const mtfMult=mtf?parseFloat(mtf[1]):null;

    // Scan nearby lines (within ±10) for same symbol's DETAIL/RISK/ACTION
    let adx=null,rsi=null,macd=null,bb=null,price=null,change=null,stoch=null,
        gate=null,regime=null,action=null,actionTxt=null,trend=null,atr=null,factors=null;

    for(let j=Math.max(0,i-1);j<=Math.min(logs.length-1,i+12);j++){
      const l=logs[j];
      if(!l.includes(symbol)) continue;

      // DETAIL line 1: trend
      if(l.includes('DETAIL')&&l.includes('trend ')){
        const m=/trend\s+([\w=,\s]+?)(?:\||\n|$)/.exec(l);
        if(m) trend=m[1].trim();
      }
      // DETAIL line 2: ADX, RSI, MACD, BB, ATR
      if(l.includes('DETAIL')&&l.includes('ADX')){
        const ma=/ADX\s*([\d.]+)\s*(\w+)?/.exec(l); if(ma){adx=parseFloat(ma[1]);adrStatus=ma[2];}
        const mr=/RSI\s*([\d.]+)/.exec(l); if(mr)rsi=parseFloat(mr[1]);
        const mm=/MACD\s+(\w+)/.exec(l); if(mm)macd=mm[1];
        const mb=/BB\s+([\w_]+)/.exec(l); if(mb)bb=mb[1];
        const mat=/ATR\s*([\d.]+)/.exec(l); if(mat)atr=parseFloat(mat[1]);
      }
      // DETAIL line 3: Price, Change, Stoch, Factors
      if(l.includes('DETAIL')&&l.includes('Price')){
        const mp=/Price\s+([\d.]+)/.exec(l); if(mp)price=parseFloat(mp[1]);
        const mc=/Change\s*([\+\-][\d.]+)%/.exec(l); if(mc)change=parseFloat(mc[1]);
        const ms=/Stoch\s*([\d.]+)\/([\d.]+)/.exec(l); if(ms)stoch=`${ms[1]}/${ms[2]}`;
        const mf=/Factors\s+(.+)$/.exec(l); if(mf)factors=mf[1].trim();
      }
      // RISK line
      if(l.includes('RISK')){
        const mg=/gate\s*([\d.]+)%/.exec(l); if(mg)gate=parseFloat(mg[1]);
        const mre=/regime\(([^)]+)\)\s*([\+\-][\d]+)%/.exec(l); if(mre)regime=`${mre[1]} ${mre[2]}%`;
      }
      // ACTION line
      if(l.includes('ACTION')){
        const mact=/ACTION.*?\|\s*[\w]+\s*\|\s*(.+?)(?:\||$)/.exec(l);
        if(mact){
          actionTxt=mact[1].trim();
          action=actionTxt.includes('placing')||actionTxt.includes('order')?'trade':'skip';
        }
      }
    }
    signals[symbol]={symbol,signal,conf,score,time,mtfMult,adx,rsi,macd,bb,price,change,stoch,gate,regime,action,actionTxt,trend,atr,factors};
  }
  console.log(`[parseSignals] Matched ${matched} SIGNAL lines, extracted ${Object.keys(signals).length} unique symbols: ${Object.keys(signals).join(', ')}`);
  return Object.values(signals);
}

function renderCard(s){
  const confPct=Math.round(s.conf);
  const sigColor=s.signal==='BUY'?'#00ff88':s.signal==='SELL'?'#ff3b5c':'#fbbf24';
  const scoreColor=s.score>0?'var(--green)':s.score<0?'var(--red)':'var(--muted)';
  const macdColor=s.macd==='BULLISH'?'var(--green)':s.macd==='BEARISH'?'var(--red)':'var(--amber)';
  const changeUp=s.change>=0;
  const adxStatus=s.adx?s.adx>=25?`<span style="color:var(--green)">TREND</span>`:`<span style="color:var(--amber)">RANGE</span>`:'—';
  const rsiColor=s.rsi?s.rsi>=70?'var(--red)':s.rsi<=30?'var(--green)':'var(--text)':'var(--muted)';
  const actionHtml=s.actionTxt?`<div style="margin-top:8px;padding:6px 8px;border-radius:4px;font-size:9px;font-family:var(--mono);
    background:${s.action==='trade'?'rgba(0,255,136,.1)':'rgba(100,116,139,.08)'};
    border-left:2px solid ${s.action==='trade'?'var(--green)':'var(--muted)'};
    color:${s.action==='trade'?'var(--green)':'var(--muted)'}">
    ${s.action==='trade'?'▶ TRADE':'⏸ SKIP'} — ${s.actionTxt.slice(0,60)}${s.actionTxt.length>60?'…':''}</div>`:'';
  const trendBadges=s.trend?s.trend.split(/\s+/).filter(t=>t.includes('=')).map(t=>{
    const[tf,dir]=t.split('=');
    const c=dir.includes('BULL')?'rgba(0,255,136,.15)':dir.includes('BEAR')?'rgba(255,59,92,.12)':'rgba(251,191,36,.12)';
    const tc=dir.includes('BULL')?'var(--green)':dir.includes('BEAR')?'var(--red)':'var(--amber)';
    return`<span style="font-size:8px;font-family:var(--mono);padding:1px 5px;border-radius:3px;background:${c};color:${tc}">${tf}</span>`;
  }).join(''):'';

  return`<div class="signal-card">
<div class="card-header">
  <div class="symbol">${s.symbol}</div>
  <div class="time">${s.time}</div>
</div>

<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
  <div class="signal-badge ${s.signal}">${s.signal}</div>
  <span style="font-size:20px;font-family:var(--mono);font-weight:700;color:${sigColor}">${confPct}%</span>
  ${s.mtfMult?`<span style="font-size:9px;font-family:var(--mono);color:var(--muted);margin-left:auto">MTF ×${s.mtfMult}</span>`:''}
</div>

<div class="conf-bar" style="margin-bottom:10px">
  <div class="conf-fill" style="width:${confPct}%;background:${confPct>=70?'linear-gradient(90deg,var(--green),#00c853)':confPct>=50?'linear-gradient(90deg,var(--cyan),var(--purple))':'linear-gradient(90deg,#f59e0b,var(--red))'}"></div>
</div>

${s.price?`<div style="display:flex;justify-content:space-between;margin-bottom:8px;padding:6px 8px;background:rgba(0,229,255,.04);border-radius:4px;border-left:2px solid rgba(0,229,255,.3)">
  <span style="font-family:var(--mono);font-size:12px;font-weight:600">${s.price.toFixed(s.price>100?2:4)}</span>
  <span style="font-family:var(--mono);font-size:11px;color:${changeUp?'var(--green)':'var(--red)'};font-weight:500">${changeUp?'▲':'▼'} ${Math.abs(s.change).toFixed(3)}%</span>
</div>`:''}

<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;margin-bottom:8px">
  <div class="ind-item"><div class="ind-label">SCORE</div><div class="ind-value" style="color:${scoreColor}">${s.score>=0?'+':''}${s.score.toFixed(1)}</div></div>
  <div class="ind-item"><div class="ind-label">RSI</div><div class="ind-value" style="color:${rsiColor}">${s.rsi?s.rsi.toFixed(1):'—'}</div></div>
  <div class="ind-item"><div class="ind-label">ADX</div><div class="ind-value">${s.adx?s.adx.toFixed(1):'—'}</div></div>
  <div class="ind-item"><div class="ind-label">MACD</div><div class="ind-value" style="color:${macdColor}">${s.macd||'—'}</div></div>
  <div class="ind-item"><div class="ind-label">BB</div><div class="ind-value" style="font-size:8px">${s.bb?s.bb.replace('_',' '):'—'}</div></div>
  <div class="ind-item"><div class="ind-label">TREND</div><div class="ind-value">${adxStatus}</div></div>
</div>

${s.gate||s.regime?`<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px">
  ${s.gate?`<div class="ind-item"><div class="ind-label">GATE</div><div class="ind-value">${s.gate.toFixed(0)}%</div></div>`:''}
  ${s.regime?`<div class="ind-item"><div class="ind-label">REGIME</div><div class="ind-value" style="font-size:8px">${s.regime}</div></div>`:''}
</div>`:''}

${trendBadges?`<div style="display:flex;gap:3px;flex-wrap:wrap;margin-bottom:6px">${trendBadges}</div>`:''}
${actionHtml}
</div>`;
}

function renderSignals(sigs){
  const filtered=symbolFilter==='all'?sigs:sigs.filter(s=>s.symbol===symbolFilter);
  const buyCount=filtered.filter(x=>x.signal==='BUY').length;
  const sellCount=filtered.filter(x=>x.signal==='SELL').length;
  console.log(`[renderSignals] Rendering ${filtered.length} of ${sigs.length} total signals (filter: ${symbolFilter})`);
  document.getElementById('count').textContent=`${filtered.length} signals`;
  document.getElementById('buy-count').textContent=`${buyCount} BUY`;
  document.getElementById('sell-count').textContent=`${sellCount} SELL`;
  document.getElementById('logs').innerHTML=filtered.length
    ?filtered.map(renderCard).join('')
    :`<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--muted);font-family:var(--mono)">No signals in last 150 log lines</div>`;
}

function renderFormattedLogs(logs){
  const container=document.getElementById('raw-logs');
  if(!logs || logs.length===0){
    container.innerHTML='<div style="color:var(--muted);padding:10px">No logs</div>';
    return;
  }

  let html='';
  for(let i=logs.length-1;i>=0;i--){
    const line=logs[i];
    const timeMatch=/(\d{2}:\d{2}:\d{2})/.exec(line);
    const time=timeMatch?timeMatch[1]:'--:--:--';

    let type='', color='var(--text)', icon='';
    let brief='';

    if(line.includes('SIGNAL')){
      type='SIG'; color='#00ff88'; icon='▶';
      const dirMatch=/(BUY|SELL|HOLD)/.exec(line);
      const confMatch=/(\d+)%/.exec(line);
      const scoreMatch=/score\s*([\+\-][\d.]+)/.exec(line);
      const dir=dirMatch?dirMatch[1]:'?';
      brief=`${dir} ${confMatch?confMatch[1]+'%':'—'} score ${scoreMatch?scoreMatch[1]:'—'}`;
    }else if(line.includes('DETAIL')&&line.includes('trend')){
      type='TRD'; color='#00e5ff'; icon='📊';
      const m=/trend\s+([D1=\w,\s]+)/.exec(line);
      brief=m?m[1].replace(/\s+/g,' ').substring(0,30):'—';
    }else if(line.includes('DETAIL')&&line.includes('ADX')){
      type='IND'; color='#00e5ff'; icon='📈';
      const adx=/ADX\s*([\d.]+)/.exec(line);
      const rsi=/RSI\s*([\d.]+)/.exec(line);
      brief=`ADX ${adx?adx[1]:'—'} RSI ${rsi?rsi[1]:'—'}`;
    }else if(line.includes('DETAIL')&&line.includes('Price')){
      type='PRC'; color='#fbbf24'; icon='💰';
      const price=/Price\s*([\d.]+)/.exec(line);
      const change=/Change\s*([\+\-][\d.]+)%/.exec(line);
      brief=`${price?price[1]:'—'} ${change?change[1]:'—'}%`;
    }else if(line.includes('ACTION')){
      type='ACT'; color='#00ff88'; icon='✓';
      brief='Trade action executed';
    }else if(line.includes('RISK')){
      type='RSK'; color='#ff3b5c'; icon='⚠';
      brief='Risk assessment';
    }else if(line.includes('bot_status')){
      type='STS'; color='#64748b'; icon='●';
      brief='Bot status update';
    }else{
      type='LOG'; color='#64748b'; icon='○';
      brief=line.substring(0,45);
    }

    html=`<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:7px;line-height:1.3;display:flex;gap:4px;align-items:center">
      <span style="color:${color};font-weight:600;width:20px;flex-shrink:0">${time}</span>
      <span style="color:${color};font-weight:700;width:25px;flex-shrink:0;text-align:center">${type}</span>
      <span style="color:${color}">${icon}</span>
      <span style="flex:1;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono)">${brief}</span>
    </div>`+html;
  }

  container.innerHTML=html;
}

async function loadLogs(){
  try{
    // Phase 2: Add Bearer token if available (embedded in page or from env)
    const token = window.__AUTH_TOKEN || 'changeme';
    console.log('[loadLogs] Fetching /api/logs with token...');
    const r = await fetch('/api/logs', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if(!r.ok)throw new Error(`HTTP ${r.status}: ${r.statusText}`);
    const data=await r.json();
    allLogs=data.logs||[];
    console.log(`[loadLogs] Loaded ${allLogs.length} log lines, source: ${data.source}`);
    const parsed = parseSignals(allLogs);
    console.log(`[loadLogs] Parsed ${parsed.length} signals`);
    renderSignals(parsed);
    // Display formatted logs in the logs section
    renderFormattedLogs(allLogs);
    document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString();
  }catch(e){
    console.error('[loadLogs] Error:', e);
    document.getElementById('logs').innerHTML=`<div style="grid-column:1/-1;color:var(--red);padding:20px;font-family:var(--mono)">Error: ${e.message}</div>`;
    document.getElementById('raw-logs').textContent=`Error loading logs: ${e.message}`;
  }
}
function setSymbol(sym,btn){
  symbolFilter=sym;
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderSignals(parseSignals(allLogs));
}
function downloadLogs(){
  // Phase 2: Include Bearer token in download URL (fallback for browser file downloads)
  const token = window.__AUTH_TOKEN || 'changeme';
  window.location.href = `/api/logs/download?token=${encodeURIComponent(token)}`;
}

async function loadPositions(){
  try{
    const token = window.__AUTH_TOKEN || 'changeme';
    const r=await fetch('/api/open-positions', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const data=await r.json();
    const positions=data.positions||[];
    console.log('✓ Positions loaded:',positions);

    const banner=document.getElementById('positions-banner');
    const content=document.getElementById('position-content');

    if(positions.length>0){
      banner.style.display='block';
      content.innerHTML=positions.map(p=>{
        const pnlColor=p.profit_loss_usd>=0?'#00ff88':'#ff3b5c';
        const dirColor=p.direction==='BUY'?'#00e5ff':'#ff3b5c';
        const entryTime=p.entry_time?new Date(p.entry_time).toLocaleString():'N/A';

        return`<div style="background:rgba(0,229,255,.06);border:1px solid rgba(0,229,255,.25);border-radius:8px;padding:12px;font-size:9px;font-family:var(--mono)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div style="font-weight:700;color:#00e5ff;font-size:11px">${p.symbol} #${p.ticket}</div>
            <div style="font-weight:700;color:${dirColor};font-size:11px">${p.direction}</div>
          </div>
          <div style="line-height:1.8;color:#e2e8f0">
            <div><span style="color:#64748b">Entry:</span> ${Number(p.entry_price).toFixed(5)}</div>
            <div><span style="color:#64748b">Current:</span> ${Number(p.current_price).toFixed(5)}</div>
            <div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,.08)">
              <div><span style="color:#64748b">P&L:</span> <span style="color:${pnlColor};font-weight:700">$${Number(p.profit_loss_usd).toFixed(2)}</span> <span style="color:${pnlColor};font-weight:700">(${p.profit_loss_pct>0?'+':''}${Number(p.profit_loss_pct).toFixed(2)}%)</span></div>
            </div>
            <div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,.08)">
              <div><span style="color:#64748b">Lot Size:</span> ${Number(p.lot_size).toFixed(2)}</div>
              <div><span style="color:#64748b">Entry Time:</span> ${entryTime}</div>
              <div><span style="color:#64748b">Status:</span> <span style="color:#00ff88">${p.status}</span></div>
            </div>
          </div>
        </div>`;
      }).join('');
    } else {
      banner.style.display='none';
    }
  }catch(e){
    console.error('✗ Position load error:',e);
    document.getElementById('positions-banner').innerHTML=`<div style="color:#ff3b5c">⚠ Error loading positions: ${e.message}</div>`;
  }
}

loadLogs();
loadPositions();
setInterval(()=>{loadLogs();loadPositions();},5000);
</script>
</body>
</html>"""


# ── HTTP Handler ───────────────────────────────────────────────────────────────
class DashHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request noise

    def _json(self, data, code=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        """Validate Bearer token. Return True if valid, else send 401 and return False."""
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            self._json({'error': 'Unauthorized: missing Bearer token'}, 401)
            return False
        token = auth_header[7:]  # strip "Bearer "
        if token != DASHBOARD_TOKEN:
            self._json({'error': 'Unauthorized: invalid token'}, 401)
            return False
        return True

    def do_GET(self):
        path = self.path.split('?')[0]

        if path in ('/', '/index.html'):
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == '/api/status':
            if not self._check_auth():
                return
            try:
                data = _get_cached("live_status", _status_from_supabase)
                self._json(data)
            except Exception as e:
                self._json({'state': 'error', 'source': 'supabase', 'error': str(e)}, 503)

        elif path == '/api/history':
            if not self._check_auth():
                return
            try:
                events = _require_supabase().get_live_events(limit=200)
                rows = []
                for event in events:
                    payload = event.get("payload") or {}
                    rows.append({
                        "ts": event.get("ts"),
                        "symbol": event.get("symbol") or payload.get("symbol"),
                        "direction": payload.get("direction") or payload.get("signal"),
                        "confidence": payload.get("confidence"),
                        "reason": payload.get("reason") or payload.get("message"),
                        "action": event.get("event_type"),
                        "ticket": payload.get("ticket"),
                    })
                self._json(rows)
            except Exception as e:
                self._json({'source': 'supabase', 'error': str(e)}, 503)

        elif path == '/api/candles':
            if not self._check_auth():
                return
            try:
                snap = _get_cached("live_snapshot", _live_snapshot_from_supabase)
                rows = _require_supabase().get_live_market_snapshots()
                data = {}
                for sym, payload in snap.get("symbols", {}).items():
                    raw = (rows.get(sym) or {}).get("raw_json") or {}
                    data[sym] = {
                        "candles": raw.get("candles", {}),
                        "updated": payload.get("_last_update"),
                        "source": "supabase",
                    }
                self._json(data)
            except Exception as e:
                self._json({'source': 'supabase', 'error': str(e)}, 503)

        elif path in ('/live', '/live.html'):
            # ── Live indicator stream viewer ──────────────────────────────────
            # Reads dashboard-ready live market/AI data from Supabase.
            from dotenv import load_dotenv
            from core.config import get_webhook_config as _get_wh
            load_dotenv(BASE_DIR / ".env")
            auth_token = _get_wh()["auth_token"]
            html_content = LIVE_HTML.replace("__AUTH_TOKEN__", auth_token)
            body = html_content.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == '/api/live-snapshot':
            if not self._check_auth():
                return
            try:
                self._json(_get_cached("live_snapshot", _live_snapshot_from_supabase))
            except Exception as e:
                self._json({'session': _current_session(), 'symbols': {}, 'source': 'supabase', 'error': str(e)}, 503)

        elif path in ('/logs', '/logs.html'):
            # ── Trading Logs Viewer ────────────────────────────────────────────
            # Inject auth token into page so JavaScript fetch calls use proper auth
            html = LOGS_HTML.replace(
                '<script>\nlet allLogs=[], symbolFilter=\'all\';',
                f'<script>\nwindow.__AUTH_TOKEN = \'{DASHBOARD_TOKEN}\';\nlet allLogs=[], symbolFilter=\'all\';'
            )
            body = html.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == '/api/logs':
            if not self._check_auth():
                return
            try:
                logs = _get_cached("live_logs", lambda: _logs_from_supabase(150))
                self._json({'logs': logs, 'source': 'supabase:live_events'})
            except Exception as e:
                self._json({'logs': [f'Supabase live_events error: {e}'], 'source': 'supabase', 'error': str(e)}, 503)

        elif path == '/api/pm2-logs':
            # Return raw PM2 bot logs
            if not self._check_auth():
                return
            try:
                from collections import deque
                lines = int(self.path.split('lines=')[1].split('&')[0]) if 'lines=' in self.path else 400
            except:
                lines = 400
            
            def tail_file(path, n):
                if not path.exists():
                    return f"[File not found: {path}]"
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        return ''.join(deque(f, n))
                except Exception as e:
                    return f"[Error reading {path}: {e}]"
            
            bot_out = tail_file(Path("/home/ubuntu/.pm2/logs/metatradeXM-bot-out.log"), lines)
            bot_err = tail_file(Path("/home/ubuntu/.pm2/logs/metatradeXM-bot-error.log"), lines)
            
            self._json({
                'logs': bot_out + "\n--- ERRORS ---\n" + bot_err,
                'source': 'pm2'
            })

        elif path == '/api/open-positions':
            if not self._check_auth():
                return
            try:
                positions = _get_cached("live_positions", _positions_from_supabase)
                self._json({'positions': positions, 'source': 'supabase'})
            except Exception as e:
                self._json({'positions': [], 'source': 'supabase', 'error': str(e)}, 503)

        elif path == '/api/trades/history':
            # ── API: Complete trade history (entries merged with outcomes) ──────────
            if not self._check_auth():
                return
            trades = []
            try:
                if _supabase:
                    def fetch_trades():
                        # Fetch all outcomes (completed trades)
                        outcomes = _supabase.get_all_outcomes(limit=100)
                        result = []
                        for outcome in outcomes:
                            try:
                                entry_price = _num(outcome.get('entry_price'), 0)
                                exit_price = _num(outcome.get('exit_price'), 0)
                                if entry_price <= 0:
                                    continue
                                trade_record = {
                                    'ticket': outcome.get('ticket'),
                                    'symbol': outcome.get('symbol'),
                                    'direction': outcome.get('direction'),
                                    'entry_price': round(entry_price, 5),
                                    'exit_price': round(exit_price, 5),
                                    'pips': _num(outcome.get('pips_result'), 0),
                                    'pnl_usd': round(_num(outcome.get('profit_usd'), 0), 2),
                                    'volume': _num(outcome.get('volume'), 0),
                                    'duration_min': _num(outcome.get('duration_min'), 0),
                                    'confidence': _num(outcome.get('confidence'), 0),
                                    'outcome': outcome.get('outcome', 'UNKNOWN'),
                                    'factors': outcome.get('factors_json', {}),
                                    'ts': outcome.get('ts'),
                                    'status': 'CLOSED'
                                }
                                result.append(trade_record)
                            except Exception as e:
                                print(f"[DASHBOARD] Error processing trade: {e}")
                        return result
                    trades = _get_cached('trades_history', fetch_trades)
            except Exception as e:
                print(f"[DASHBOARD] Error fetching trade history: {e}")
                trades = []
            self._json({'trades': trades, 'count': len(trades)})

        elif path == '/api/trades/performance':
            # ── API: Performance metrics (win rate, pips, P&L by category) ────────
            if not self._check_auth():
                return
            perf = {}
            try:
                if _supabase:
                    def fetch_performance():
                        outcomes = _supabase.get_all_outcomes(limit=500)
                        filtered_outcomes = []
                        for o in outcomes:
                            entry_price = _num(o.get('entry_price'), 0)
                            if entry_price <= 0:
                                continue
                            filtered_outcomes.append(o)
                        outcomes = filtered_outcomes

                        # Initialize counters
                        total_trades = len(outcomes)
                        wins = sum(1 for o in outcomes if o.get('outcome') == 'WIN')
                        losses = sum(1 for o in outcomes if o.get('outcome') == 'LOSS')
                        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

                        # Calculate total pips and P&L
                        total_pips = sum(_num(o.get('pips_result'), 0) for o in outcomes)
                        total_pnl = 0.0
                        for o in outcomes:
                            override = overrides.get(str(o.get('ticket')))
                            total_pnl += _num((override or {}).get('profit_usd'), _num(o.get('profit_usd'), _num(o.get('exit_price'), 0) - _num(o.get('entry_price'), 0)))
                        avg_pips = (total_pips / total_trades) if total_trades > 0 else 0

                        # Best/worst trades
                        if outcomes:
                            pips_list = [_num(o.get('pips_result'), 0) for o in outcomes]
                            best_trade = max(pips_list)
                            worst_trade = min(pips_list)
                        else:
                            best_trade = worst_trade = 0

                        # By symbol
                        by_symbol = {}
                        for o in outcomes:
                            sym = o.get('symbol', 'UNKNOWN')
                            if sym not in by_symbol:
                                by_symbol[sym] = {'wins': 0, 'losses': 0, 'total_pips': 0}
                            by_symbol[sym]['total_pips'] += _num(o.get('pips_result'), 0)
                            if o.get('outcome') == 'WIN':
                                by_symbol[sym]['wins'] += 1
                            else:
                                by_symbol[sym]['losses'] += 1

                        for sym in by_symbol:
                            total = by_symbol[sym]['wins'] + by_symbol[sym]['losses']
                            by_symbol[sym]['win_rate'] = round(by_symbol[sym]['wins'] / total * 100 if total > 0 else 0, 1)

                        return {
                            'total_trades': total_trades,
                            'wins': wins,
                            'losses': losses,
                            'win_rate': round(win_rate, 1),
                            'total_pips': round(total_pips, 1),
                            'avg_pips': round(avg_pips, 1),
                            'total_pnl_usd': round(total_pnl, 2),
                            'best_trade_pips': round(best_trade, 1),
                            'worst_trade_pips': round(worst_trade, 1),
                            'by_symbol': by_symbol
                        }
                    perf = _get_cached('trades_performance', fetch_performance)
            except Exception as e:
                print(f"[DASHBOARD] Error fetching performance: {e}")
                perf = {}
            self._json(perf)

        elif path == '/api/trades/factors':
            # ── API: Factor effectiveness analysis ───────────────────────────────
            factors = {}
            try:
                if _supabase:
                    def fetch_factors():
                        stats = _supabase.get_factor_stats()
                        result = {}
                        for factor_name, stats_data in stats.items():
                            result[factor_name] = {
                                'win_rate': round(stats_data['win_rate'] * 100, 1),
                                'sample_size': stats_data['sample_size'],
                                'avg_when_win': round(stats_data['avg_when_win'], 2),
                                'avg_when_loss': round(stats_data['avg_when_loss'], 2),
                            }
                        return result
                    factors = _get_cached('trades_factors', fetch_factors)
            except Exception as e:
                print(f"[DASHBOARD] Error fetching factors: {e}")
                factors = {}
            self._json(factors)

        else:
            self.send_response(404)
            self.end_headers()


# ── Threaded HTTP Server (handles concurrent connections) ──────────────────────
class ThreadedHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in a separate thread — no more connection timeouts."""
    daemon_threads = True
    allow_reuse_address = True  # prevents 'Address already in use' on fast restart


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    _start_live_bridge_sync()
    srv = ThreadedHTTPServer(('0.0.0.0', PORT), DashHandler)
    print(f"\n{'='*58}")
    print(f"  MT5 AI Trading Dashboard  v3.0  [THREADED]")
    print(f"  Open  →  http://92.4.71.177:{PORT}")
    print(f"  DB    →  Supabase")
    print(f"  Start: python3 continuous_trader.py")
    print(f"{'='*58}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
