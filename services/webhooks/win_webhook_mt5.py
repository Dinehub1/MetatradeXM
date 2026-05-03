"""
win_webhook_mt5.py — Windows MT5 Webhook Server

Full-featured REST API exposing MetaTrader 5 data and trading operations.
Runs on the Windows machine where MT5 is installed.

Endpoints:
  GET  /status                          Terminal connection status
  GET  /account                         Account info (balance, equity, margin)
  GET  /positions                       All open positions with P&L
  GET  /positions/<symbol>              Open positions for a specific symbol
  GET  /tick/<symbol>                   Current ask/bid/last price
  GET  /symbol/<symbol>                 Symbol specs (digits, contract size, volumes)
  GET  /candles/<symbol>/<tf>?count=200 OHLCV candle data
  GET  /history?days=7                  Trade history (closed deals)
  POST /webhook                         BUY/SELL/CLOSE orders
  POST /modify                          Modify SL/TP on an existing position

Usage on Windows:
  pip install flask MetaTrader5
  python win_webhook_mt5.py
"""

from flask import Flask, request, jsonify
import MetaTrader5 as mt5
import threading
import logging
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Always load .env from the same directory as this script, regardless of CWD.
# This fixes the "VM restart causes auth failure" issue when Windows Task Scheduler
# launches the script from a different working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env")
AUTH_TOKEN = os.getenv("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("mt5_webhook")

app = Flask(__name__)

@app.before_request
def require_auth():
    # Allow /status to be public if desired, but we will secure it for now
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {AUTH_TOKEN}":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

# Lock for MT5 terminal interactions (MT5 is single-threaded)
mt5_lock = threading.Lock()

# ── MT5 timeframe mapping ────────────────────────────────────────────────────
TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
    "W1":  mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def init_mt5():
    """Initialize MT5 connection (thread-safe)."""
    with mt5_lock:
        if not mt5.initialize():
            log.error("initialize() failed, error code = %s", mt5.last_error())
            return False
        info = mt5.account_info()
        term = mt5.terminal_info()
        if info:
            log.info(
                "MT5 connected: login=%s server=%s balance=%.2f",
                info.login, info.server, info.balance,
            )
        if term:
            log.info("MT5 terminal ready: %s", term.name)
        return True


def _ensure_symbol(symbol):
    """Make sure a symbol is visible in Market Watch. Returns symbol_info or None."""
    si = mt5.symbol_info(symbol)
    if si is None:
        return None
    if not si.visible:
        mt5.symbol_select(symbol, True)
    return si


# ══════════════════════════════════════════════════════════════════════════════
#  DATA ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/status', methods=['GET'])
def get_status():
    """Terminal connection status — auto-reconnects if MT5 API went stale."""
    with mt5_lock:
        term = mt5.terminal_info()
        connected = term is not None

        # Auto-reconnect: MT5 Python API can lose its handle while
        # the terminal itself is still running.  Re-initialize.
        if not connected:
            log.info("[STATUS] terminal_info() returned None — attempting re-init...")
            if mt5.initialize():
                term = mt5.terminal_info()
                connected = term is not None
                if connected:
                    log.info("[STATUS] Re-initialized MT5 successfully")
                else:
                    log.warning("[STATUS] Re-init succeeded but terminal still not connected")
            else:
                log.warning("[STATUS] Re-init failed: %s", mt5.last_error())

        info = mt5.account_info()
    resp = {"status": "running", "terminal_connected": connected}
    if info:
        resp["login"] = info.login
        resp["server"] = info.server
    return jsonify(resp), 200


@app.route('/account', methods=['GET'])
def get_account():
    """Full account information."""
    with mt5_lock:
        info = mt5.account_info()
    if info is None:
        return jsonify({"status": "error", "message": "Cannot get account info"}), 500

    return jsonify({
        "status": "ok",
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "leverage": info.leverage,
        "currency": info.currency,
        "profit": info.profit,
        "name": info.name,
    }), 200


@app.route('/positions', methods=['GET'])
@app.route('/positions/<symbol>', methods=['GET'])
def get_positions(symbol=None):
    """All open positions (optionally filtered by symbol)."""
    if not symbol:
        symbol = request.args.get('symbol')
    with mt5_lock:
        if symbol:
            _ensure_symbol(symbol)
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()

    if positions is None:
        positions = ()

    result = []
    for p in positions:
        result.append({
            "ticket": getattr(p, "ticket", 0),
            "symbol": getattr(p, "symbol", ""),
            "type": "BUY" if getattr(p, "type", 0) == 0 else "SELL",
            "type_id": getattr(p, "type", 0),
            "volume": getattr(p, "volume", 0.0),
            "price_open": getattr(p, "price_open", 0.0),
            "price_current": getattr(p, "price_current", 0.0),
            "sl": getattr(p, "sl", 0.0),
            "tp": getattr(p, "tp", 0.0),
            "profit": getattr(p, "profit", 0.0),
            "swap": getattr(p, "swap", 0.0),
            "comment": getattr(p, "comment", ""),
            "magic": getattr(p, "magic", 0),
            "time": int(getattr(p, "time", 0)),
            "time_update": int(getattr(p, "time_update", 0)),
            "identifier": getattr(p, "identifier", 0),
        })

    return jsonify({
        "status": "ok",
        "count": len(result),
        "positions": result,
    }), 200


@app.route('/tick', methods=['GET'])
@app.route('/tick/<symbol>', methods=['GET'])
def get_tick(symbol=None):
    """Current tick (ask/bid/last) for a symbol."""
    if not symbol:
        symbol = request.args.get('symbol')
    if not symbol:
        return jsonify({"status": "error", "message": "Missing symbol"}), 400

    with mt5_lock:
        _ensure_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        return jsonify({"status": "error", "message": f"No tick data for {symbol}"}), 404

    return jsonify({
        "status": "ok",
        "symbol": symbol,
        "ask": tick.ask,
        "bid": tick.bid,
        "last": tick.last,
        "volume": tick.volume,
        "time": int(tick.time),
    }), 200


@app.route('/symbol', methods=['GET'])
@app.route('/symbol/<symbol>', methods=['GET'])
def get_symbol_info(symbol=None):
    """Symbol specifications (digits, contract size, volume limits, etc)."""
    if not symbol:
        symbol = request.args.get('symbol')
    if not symbol:
        return jsonify({"status": "error", "message": "Missing symbol"}), 400

    with mt5_lock:
        si = mt5.symbol_info(symbol)

    if si is None:
        return jsonify({"status": "error", "message": f"Symbol {symbol} not found"}), 404

    return jsonify({
        "status": "ok",
        "name": si.name,
        "description": si.description,
        "digits": si.digits,
        "point": si.point,
        "trade_contract_size": si.trade_contract_size,
        "volume_min": si.volume_min,
        "volume_max": si.volume_max,
        "volume_step": si.volume_step,
        "trade_tick_size": si.trade_tick_size,
        "trade_tick_value": si.trade_tick_value,
        "spread": si.spread,
        "swap_long": si.swap_long,
        "swap_short": si.swap_short,
        "currency_base": si.currency_base,
        "currency_profit": si.currency_profit,
        "currency_margin": si.currency_margin,
    }), 200


@app.route('/candles', methods=['GET'])
@app.route('/candles/<symbol>/<tf>', methods=['GET'])
def get_candles(symbol=None, tf=None):
    """Historical OHLCV candle data."""
    if not symbol:
        symbol = request.args.get('symbol')
    if not tf:
        tf = request.args.get('tf')
    if not symbol or not tf:
        return jsonify({"status": "error", "message": "Missing symbol or tf"}), 400

    count = min(int(request.args.get('count', 200)), 5000)
    mt5_tf = TF_MAP.get(tf.upper())
    if mt5_tf is None:
        return jsonify({
            "status": "error",
            "message": f"Invalid timeframe '{tf}'. Use: {', '.join(TF_MAP.keys())}"
        }), 400

    with mt5_lock:
        _ensure_symbol(symbol)
        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)

    if rates is None or len(rates) == 0:
        return jsonify({
            "status": "error",
            "message": f"No candle data for {symbol}/{tf}"
        }), 404

    candles = []
    for r in rates:
        candles.append({
            "time": int(r['time']),
            "o": float(r['open']),
            "h": float(r['high']),
            "l": float(r['low']),
            "c": float(r['close']),
            "vol": int(r['tick_volume']),
            "spread": int(r['spread']),
            "real_vol": int(r['real_volume']),
        })

    return jsonify({
        "status": "ok",
        "symbol": symbol,
        "timeframe": tf.upper(),
        "count": len(candles),
        "candles": candles,
    }), 200


@app.route('/history', methods=['GET'])
def get_history():
    """Trade history (closed deals) for the past N days.
    
    Query params:
        days: number of days to look back (default 7, max 90)
        symbol: optional filter by symbol
    """
    days = min(int(request.args.get('days', 7)), 90)
    symbol_filter = request.args.get('symbol', None)

    date_from = datetime.now(timezone.utc) - timedelta(days=days)
    date_to = datetime.now(timezone.utc) + timedelta(days=1)

    with mt5_lock:
        if symbol_filter:
            deals = mt5.history_deals_get(date_from, date_to, group=f"*{symbol_filter}*")
        else:
            deals = mt5.history_deals_get(date_from, date_to)

    if deals is None:
        deals = ()

    result = []
    for d in deals:
        result.append({
            "ticket": d.ticket,
            "order": d.order,
            "time": int(d.time),
            "time_str": datetime.fromtimestamp(d.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "type": d.type,       # 0=BUY, 1=SELL, 2=balance, etc.
            "entry": d.entry,     # 0=IN, 1=OUT, 2=INOUT, 3=OUT_BY
            "symbol": d.symbol,
            "volume": d.volume,
            "price": d.price,
            "profit": d.profit,
            "swap": d.swap,
            "commission": d.commission,
            "fee": d.fee,
            "comment": d.comment,
            "magic": d.magic,
            "position_id": d.position_id,
        })

    # Calculate summary stats
    trade_deals = [d for d in result if d["entry"] == 1 and d["symbol"]]  # OUT deals = closed trades
    total_profit = sum(d["profit"] for d in trade_deals)
    winners = sum(1 for d in trade_deals if d["profit"] > 0)
    losers = sum(1 for d in trade_deals if d["profit"] < 0)

    return jsonify({
        "status": "ok",
        "days": days,
        "total_deals": len(result),
        "closed_trades": len(trade_deals),
        "total_profit": round(total_profit, 2),
        "winners": winners,
        "losers": losers,
        "win_rate": round(winners / len(trade_deals) * 100, 1) if trade_deals else 0,
        "deals": result,
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
#  TRADING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Execute BUY, SELL, or CLOSE orders."""
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload provided"}), 400

        action = data.get("action", "").upper()
        symbol = data.get("symbol")
        lot = data.get("lot", 0.01)
        sl = data.get("sl")     # optional stop loss price
        tp = data.get("tp")     # optional take profit price
        ticket = data.get("ticket")  # for closing specific position

        log.info(f"Webhook: {action} {symbol} lot={lot} sl={sl} tp={tp}")

        if not symbol:
            return jsonify({"status": "error", "message": "Symbol is required"}), 400

        # Initialize MT5 if not connected
        if not init_mt5():
            return jsonify({"status": "error", "message": "Failed to connect to MT5 terminal"}), 500

        with mt5_lock:
            si = _ensure_symbol(symbol)
            if si is None:
                return jsonify({"status": "error", "message": f"Symbol {symbol} not found"}), 404

            order_request_action = mt5.TRADE_ACTION_DEAL

            if action == "BUY":
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    return jsonify({"status": "error", "message": f"No tick data for {symbol}"}), 500
                price = tick.ask
                order_type = mt5.ORDER_TYPE_BUY

            elif action == "SELL":
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    return jsonify({"status": "error", "message": f"No tick data for {symbol}"}), 500
                price = tick.bid
                order_type = mt5.ORDER_TYPE_SELL

            elif action == "LIMIT_BUY":
                order_type = mt5.ORDER_TYPE_BUY_LIMIT
                price = float(data.get("price", 0))
                if price <= 0:
                    return jsonify({"status": "error", "message": "Price required for limit orders"}), 400
                order_request_action = mt5.TRADE_ACTION_PENDING

            elif action == "LIMIT_SELL":
                order_type = mt5.ORDER_TYPE_SELL_LIMIT
                price = float(data.get("price", 0))
                if price <= 0:
                    return jsonify({"status": "error", "message": "Price required for limit orders"}), 400
                order_request_action = mt5.TRADE_ACTION_PENDING

            elif action == "CLOSE":
                return _close_positions(symbol, ticket)

            elif action == "CLOSE_PARTIAL":
                close_volume = data.get("close_volume", 0)
                if not ticket or close_volume <= 0:
                    return jsonify({"status": "error", "message": "ticket and close_volume required for CLOSE_PARTIAL"}), 400
                return _close_position_partial(int(ticket), float(close_volume))

            else:
                return jsonify({"status": "error", "message": "Invalid action. Use BUY, SELL, LIMIT_BUY, LIMIT_SELL, CLOSE, or CLOSE_PARTIAL"}), 400

            # Build order request
            order_request = {
                "action": order_request_action,
                "symbol": symbol,
                "volume": float(lot),
                "type": order_type,
                "price": price,
                "deviation": 20,
                "magic": 100,
                "comment": data.get("comment", "Webhook Trade"),
                "type_time": mt5.ORDER_TIME_GTC,
            }
            if order_request_action == mt5.TRADE_ACTION_DEAL:
                order_request["type_filling"] = mt5.ORDER_FILLING_IOC
            if sl is not None:
                order_request["sl"] = float(sl)
            if tp is not None:
                order_request["tp"] = float(tp)

            result = mt5.order_send(order_request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                error_msg = f"Order failed, retcode={result.retcode}"
                log.error(error_msg)
                return jsonify({"status": "error", "message": error_msg, "retcode": result.retcode}), 500

            log.info(
                "Order OK: %s %s lot=%s ticket=%s price=%s",
                action, symbol, lot, result.order, result.price,
            )

            return jsonify({
                "status": "success",
                "message": "Order executed",
                "ticket": result.order,
                "volume": result.volume,
                "price": result.price,
            }), 200

    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _close_positions(symbol, ticket=None):
    """Close positions — specific ticket or all for a symbol. Called inside mt5_lock."""
    if ticket:
        # Close specific position
        positions = mt5.positions_get(ticket=int(ticket))
    else:
        positions = mt5.positions_get(symbol=symbol)

    if positions is None or len(positions) == 0:
        return jsonify({"status": "success", "message": f"No open positions", "closed": 0}), 200

    closed_count = 0
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            continue
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == 0 else tick.ask

        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 100,
            "comment": "Webhook Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(close_request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed_count += 1
            log.info("Closed: ticket=%s %s vol=%s profit=%.2f",
                     pos.ticket, pos.symbol, pos.volume, pos.profit)
        else:
            log.error("Close failed: ticket=%s retcode=%s", pos.ticket, result.retcode)

    return jsonify({"status": "success", "message": f"Closed {closed_count} positions", "closed": closed_count}), 200


def _close_position_partial(ticket: int, close_volume: float):
    """Partially close a position by reducing its volume. Called inside mt5_lock.

    Sends a counter-trade for only `close_volume` lots against the existing
    position. MT5 will reduce the position volume rather than open a new one
    because we reference the position ticket.
    """
    positions = mt5.positions_get(ticket=ticket)
    if positions is None or len(positions) == 0:
        return jsonify({"status": "error", "message": f"Position {ticket} not found"}), 404

    pos = positions[0]
    actual_volume = min(close_volume, pos.volume)
    if actual_volume <= 0:
        return jsonify({"status": "error", "message": f"Invalid close_volume {close_volume}"}), 400

    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return jsonify({"status": "error", "message": f"No tick for {pos.symbol}"}), 500

    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    close_price = tick.bid if pos.type == 0 else tick.ask

    close_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": round(actual_volume, 2),
        "type": close_type,
        "position": pos.ticket,
        "price": close_price,
        "deviation": 20,
        "magic": 100,
        "comment": "Webhook Partial Close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(close_request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info("Partial close: ticket=%s vol=%.2f/%.2f profit=%.2f",
                 pos.ticket, actual_volume, pos.volume, pos.profit)
        return jsonify({
            "status": "success",
            "message": f"Partially closed {actual_volume} of {pos.volume}",
            "ticket": pos.ticket,
            "closed_volume": actual_volume,
            "remaining_volume": round(pos.volume - actual_volume, 2),
        }), 200
    else:
        log.error("Partial close failed: ticket=%s retcode=%s", pos.ticket, result.retcode)
        return jsonify({"status": "error", "message": f"Partial close failed, retcode={result.retcode}"}), 500


@app.route('/modify', methods=['POST'])
def modify_position():
    """Modify SL/TP on an existing position.
    
    JSON body: {"ticket": 12345, "sl": 3320.50, "tp": 3340.00}
    """
    try:
        data = request.json
        if not data or "ticket" not in data:
            return jsonify({"status": "error", "message": "ticket is required"}), 400

        ticket = int(data["ticket"])
        sl = data.get("sl")
        tp = data.get("tp")

        if sl is None and tp is None:
            return jsonify({"status": "error", "message": "sl or tp required"}), 400

        with mt5_lock:
            # Get current position to find symbol
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                return jsonify({"status": "error", "message": f"Position {ticket} not found"}), 404

            pos = positions[0]
            modify_request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": ticket,
                "sl": float(sl) if sl is not None else pos.sl,
                "tp": float(tp) if tp is not None else pos.tp,
            }
            result = mt5.order_send(modify_request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return jsonify({
                    "status": "error",
                    "message": f"Modify failed, retcode={result.retcode}",
                    "retcode": result.retcode,
                }), 500

            log.info("Modified: ticket=%s sl=%s tp=%s", ticket, sl, tp)
            return jsonify({
                "status": "success",
                "message": "Position modified",
                "ticket": ticket,
                "sl": modify_request["sl"],
                "tp": modify_request["tp"],
            }), 200

    except Exception as e:
        log.error(f"Modify error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Indicator computation endpoint ──────────────────────────────────────────

def _compute_rsi(close, period=14):
    """Manual RSI computation (no external deps)."""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period, min_periods=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _compute_ema(series, span):
    """Exponential moving average."""
    return series.ewm(span=span, adjust=False).mean()


def _compute_atr(high, low, close, period=14):
    """Average True Range."""
    import numpy as np
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def _compute_indicators_from_rates(df):
    """Compute a full indicator set from an OHLCV DataFrame.
    
    Returns a dict with RSI, MACD, ADX, BB, Stochastic, EMA, ATR, Williams %R.
    Works with pure pandas — no external TA library required.
    """
    import numpy as np
    
    close = df['close']
    high = df['high']
    low = df['low']
    
    result = {}
    
    # RSI
    rsi = _compute_rsi(close, 14)
    result['rsi'] = round(float(rsi.iloc[-1]), 2) if not rsi.empty and pd.notna(rsi.iloc[-1]) else None
    
    # MACD
    ema12 = _compute_ema(close, 12)
    ema26 = _compute_ema(close, 26)
    macd_line = ema12 - ema26
    macd_signal = _compute_ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    hist_val = float(macd_hist.iloc[-1]) if pd.notna(macd_hist.iloc[-1]) else 0
    result['macd_hist'] = round(hist_val, 6)
    result['macd_signal'] = "BULLISH" if hist_val > 0 else "BEARISH"
    
    # ADX (+DI, -DI)
    try:
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        atr = _compute_atr(high, low, close, 14)
        plus_di = 100 * _compute_ema(plus_dm, 14) / atr
        minus_di = 100 * _compute_ema(minus_dm, 14) / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).abs()
        adx = _compute_ema(dx, 14)
        result['adx'] = round(float(adx.iloc[-1]), 1) if pd.notna(adx.iloc[-1]) else None
        result['plus_di'] = round(float(plus_di.iloc[-1]), 1) if pd.notna(plus_di.iloc[-1]) else None
        result['minus_di'] = round(float(minus_di.iloc[-1]), 1) if pd.notna(minus_di.iloc[-1]) else None
    except Exception:
        result['adx'] = None
        result['plus_di'] = None
        result['minus_di'] = None
    
    # Stochastic
    try:
        low_14 = low.rolling(14).min()
        high_14 = high.rolling(14).max()
        stoch_k = 100 * (close - low_14) / (high_14 - low_14)
        stoch_d = stoch_k.rolling(3).mean()
        result['stoch_k'] = round(float(stoch_k.iloc[-1]), 1) if pd.notna(stoch_k.iloc[-1]) else None
        result['stoch_d'] = round(float(stoch_d.iloc[-1]), 1) if pd.notna(stoch_d.iloc[-1]) else None
    except Exception:
        result['stoch_k'] = None
        result['stoch_d'] = None
    
    # Bollinger Bands
    try:
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        price = float(close.iloc[-1])
        bb_u = float(bb_upper.iloc[-1])
        bb_l = float(bb_lower.iloc[-1])
        bb_m = float(bb_mid.iloc[-1])
        
        if price >= bb_u: bb_pos = "ABOVE_UPPER"
        elif price <= bb_l: bb_pos = "BELOW_LOWER"
        elif price > bb_m: bb_pos = "ABOVE_MID"
        else: bb_pos = "BELOW_MID"
        
        # BB squeeze: bandwidth < 20th percentile of last 50 bars
        bandwidth = ((bb_upper - bb_lower) / bb_mid).tail(50)
        current_bw = float(bandwidth.iloc[-1])
        threshold = float(bandwidth.quantile(0.20))
        bb_squeeze = current_bw < threshold
        
        result['bb_position'] = bb_pos
        result['bb_squeeze'] = bb_squeeze
    except Exception:
        result['bb_position'] = "UNKNOWN"
        result['bb_squeeze'] = False
    
    # EMA 20/50/200
    ema20 = _compute_ema(close, 20)
    ema50 = _compute_ema(close, 50)
    result['ema20'] = round(float(ema20.iloc[-1]), 5) if pd.notna(ema20.iloc[-1]) else None
    result['ema50'] = round(float(ema50.iloc[-1]), 5) if pd.notna(ema50.iloc[-1]) else None
    
    if len(close) >= 200:
        ema200 = _compute_ema(close, 200)
        result['ema200'] = round(float(ema200.iloc[-1]), 5) if pd.notna(ema200.iloc[-1]) else None
    else:
        result['ema200'] = None
    
    # EMA trend
    e20 = result['ema20'] or 0
    e50 = result['ema50'] or 0
    e200 = result['ema200']
    if e200:
        if e20 > e50 > e200: ema_trend = "BULLISH"
        elif e20 < e50 < e200: ema_trend = "BEARISH"
        elif e20 > e50: ema_trend = "MILD_BULL"
        elif e20 < e50: ema_trend = "MILD_BEAR"
        else: ema_trend = "MIXED"
    else:
        ema_trend = "MILD_BULL" if e20 > e50 else "MILD_BEAR"
    result['ema_trend'] = ema_trend
    
    # ATR
    atr = _compute_atr(high, low, close, 14)
    result['atr'] = round(float(atr.iloc[-1]), 5) if pd.notna(atr.iloc[-1]) else None
    
    # Williams %R
    try:
        high_14 = high.rolling(14).max()
        low_14 = low.rolling(14).min()
        willr = -100 * (high_14 - close) / (high_14 - low_14)
        result['williams_r'] = round(float(willr.iloc[-1]), 1) if pd.notna(willr.iloc[-1]) else None
    except Exception:
        result['williams_r'] = None
    
    result['price'] = round(float(close.iloc[-1]), 5)
    
    return result


@app.route('/indicators', methods=['GET'])
@app.route('/indicators/<symbol>', methods=['GET'])
@app.route('/indicators/<symbol>/<tf>', methods=['GET'])
def get_indicators(symbol=None, tf=None):
    """Multi-timeframe technical indicators computed from MT5 candle data.
    
    Query params:
        symbol: e.g. "GOLD.i#"
        tf: comma-separated timeframes (default: M1,M15,H1,H4,D1)
        tf: comma-separated timeframes (default: M1,M15,H1,H4,D1)
        count: number of candles to fetch (default: 300)
    
    Returns RSI, MACD, ADX, Stochastic, Bollinger Bands, EMA, ATR, Williams %R.
    """
    if not symbol:
        symbol = request.args.get('symbol')
    if not symbol:
        return jsonify({"status": "error", "message": "Missing symbol"}), 400

    timeframes = tf.split(',') if tf else request.args.get('tf', 'M1,M15,H1,H4,D1').split(',')
    count = int(request.args.get('count', '300'))
    result_data = {"status": "ok", "symbol": symbol, "timeframes": {}}
    
    with mt5_lock:
        _ensure_symbol(symbol)
        for tf_str in timeframes:
            tf_key = tf_str.strip().upper()
            mt5_tf = TF_MAP.get(tf_key)
            if mt5_tf is None:
                continue
            
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
            if rates is None or len(rates) < 30:
                continue
            
            df = pd.DataFrame(rates)
            df.columns = [c.lower() for c in df.columns]
            # MT5 uses 'tick_volume' not 'volume'
            if 'tick_volume' in df.columns and 'volume' not in df.columns:
                df['volume'] = df['tick_volume']
            
            try:
                indicators = _compute_indicators_from_rates(df)
                result_data["timeframes"][tf_key] = indicators
            except Exception as e:
                log.warning(f"Indicator computation failed for {symbol}/{tf_key}: {e}")
                continue
    
    return jsonify(result_data), 200


# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET STREAMING SERVER — Real-time data to Ubuntu bot
# ══════════════════════════════════════════════════════════════════════════════

import asyncio
import json
import time as _time

try:
    import websockets
    import websockets.server
    _HAS_WEBSOCKETS = True
except ImportError:
    _HAS_WEBSOCKETS = False
    log.warning("websockets not installed — pip install websockets (WS server disabled)")

# Tracked symbols for streaming (same as bot trades)
WS_SYMBOLS = ["GOLD.i#", "SILVER.i#"]
WS_PORT = 5002

# Connected WebSocket clients
_ws_clients = set()
_ws_lock = threading.Lock()


def _gather_tick_data(symbol):
    """Get current tick data for a symbol (called under mt5_lock)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return {
        "type": "tick",
        "symbol": symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "time": tick.time,
        "spread": round(tick.ask - tick.bid, 5),
    }


def _gather_positions():
    """Get all open positions (called under mt5_lock)."""
    pos_list = mt5.positions_get()
    if pos_list is None:
        return {"type": "positions", "positions": []}
    positions = []
    for p in pos_list:
        positions.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type_id": p.type,  # 0=BUY, 1=SELL
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "comment": p.comment,
            "time": p.time,
        })
    return {"type": "positions", "positions": positions}


def _gather_account():
    """Get account info (called under mt5_lock)."""
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "type": "account",
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "leverage": info.leverage,
        "currency": info.currency,
        "time": int(_time.time()),
    }


async def _ws_handler(websocket):
    """Handle a single WebSocket client connection."""
    client_addr = websocket.remote_address
    
    # Check Auth Header
    # Support both newer websockets (websocket.request.headers) and older (websocket.request_headers)
    try:
        if hasattr(websocket, 'request') and hasattr(websocket.request, 'headers'):
            auth_header = websocket.request.headers.get("Authorization")
        else:
            auth_header = websocket.request_headers.get("Authorization")
    except Exception:
        auth_header = None
        
    if not auth_header or auth_header != f"Bearer {AUTH_TOKEN}":
        log.warning(f"[WS] Unauthorized connection attempt from {client_addr}")
        await websocket.close(1008, "Unauthorized")
        return
        
    log.info(f"[WS] Client connected: {client_addr}")

    with _ws_lock:
        _ws_clients.add(websocket)

    try:
        # Gather all initial data under the lock (no async I/O while lock is held)
        with mt5_lock:
            acct = _gather_account()
            positions = _gather_positions()
            initial_msgs = []
            for sym in WS_SYMBOLS:
                _ensure_symbol(sym)
                for tf in ["M1", "M5", "M15", "H1", "H4"]:
                    rates = mt5.copy_rates_from_pos(sym, TF_MAP.get(tf), 0, 200)
                    if rates is not None and len(rates) > 0:
                        candles = [{"time": int(r['time']), "o": float(r['open']),
                                    "h": float(r['high']), "l": float(r['low']),
                                    "c": float(r['close']), "vol": int(r['tick_volume'])}
                                   for r in rates]
                        initial_msgs.append(json.dumps({
                            "type": "candles", "symbol": sym,
                            "timeframe": tf, "candles": candles
                        }))

        # Lock released — send all initial data asynchronously
        if acct:
            await websocket.send(json.dumps(acct))
        await websocket.send(json.dumps(positions))
        for msg in initial_msgs:
            await websocket.send(msg)

        # Keep connection alive and listen for client messages
        async for message in websocket:
            # Client can send "subscribe" messages or ping
            try:
                msg = json.loads(message)
                if msg.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except websockets.exceptions.ConnectionClosed:
        log.info(f"[WS] Client disconnected: {client_addr}")
    except Exception as e:
        log.warning(f"[WS] Client error ({client_addr}): {e}")
    finally:
        with _ws_lock:
            _ws_clients.discard(websocket)


async def _broadcast(message_json: str):
    """Broadcast a JSON message to all connected WebSocket clients."""
    with _ws_lock:
        clients = list(_ws_clients)

    if not clients:
        return

    disconnected = []
    for ws in clients:
        try:
            await ws.send(message_json)
        except Exception:
            disconnected.append(ws)

    if disconnected:
        with _ws_lock:
            for ws in disconnected:
                _ws_clients.discard(ws)


async def _streaming_loop():
    """Main streaming loop — broadcasts ticks, positions, account at intervals."""
    tick_interval = 0.5      # ticks every 500ms
    position_interval = 5.0  # positions every 5s
    account_interval = 30.0  # account every 30s
    heartbeat_interval = 15.0

    last_position = 0
    last_account = 0
    last_heartbeat = 0
    
    last_candle_times = {} # Track last closed candle time

    log.info(f"[WS] Streaming loop started (tick={tick_interval}s, pos={position_interval}s)")

    while True:
        now = _time.time()

        with _ws_lock:
            has_clients = len(_ws_clients) > 0

        if not has_clients:
            await asyncio.sleep(1.0)
            continue

        # ── Tick data (high frequency) and Bar Close ────────────────────
        try:
            tick_messages = []
            bar_close_messages = []
            with mt5_lock:
                for sym in WS_SYMBOLS:
                    _ensure_symbol(sym)
                    tick_data = _gather_tick_data(sym)
                    if tick_data:
                        tick_messages.append(json.dumps(tick_data))
                        
                    if sym not in last_candle_times:
                        last_candle_times[sym] = {}
                        
                    for tf in ["M1", "M5", "M15", "H1", "H4"]:
                        mt5_tf = TF_MAP.get(tf)
                        rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, 2)
                        if rates is not None and len(rates) >= 2:
                            # rates[0] is the last closed candle, rates[1] is the current open candle
                            closed_candle = rates[0]
                            closed_time = int(closed_candle['time'])
                            
                            if tf not in last_candle_times[sym]:
                                last_candle_times[sym][tf] = closed_time
                            elif closed_time > last_candle_times[sym][tf]:
                                last_candle_times[sym][tf] = closed_time
                                bar_close_messages.append(json.dumps({
                                    "type": "bar_close",
                                    "symbol": sym,
                                    "timeframe": tf,
                                    "candle": {
                                        "time": closed_time,
                                        "o": float(closed_candle['open']),
                                        "h": float(closed_candle['high']),
                                        "l": float(closed_candle['low']),
                                        "c": float(closed_candle['close']),
                                        "vol": int(closed_candle['tick_volume'])
                                    }
                                }))

            for msg in tick_messages:
                await _broadcast(msg)
            for msg in bar_close_messages:
                await _broadcast(msg)
        except Exception as e:
            log.debug(f"[WS] Tick/Bar broadcast error: {e}")

        # ── Positions (medium frequency) ────────────────────────────────
        if now - last_position >= position_interval:
            try:
                with mt5_lock:
                    pos_data = _gather_positions()
                await _broadcast(json.dumps(pos_data))
                last_position = now
            except Exception as e:
                log.debug(f"[WS] Position broadcast error: {e}")

        # ── Account (low frequency) ─────────────────────────────────────
        if now - last_account >= account_interval:
            try:
                with mt5_lock:
                    acct_data = _gather_account()
                if acct_data:
                    await _broadcast(json.dumps(acct_data))
                last_account = now
            except Exception as e:
                log.debug(f"[WS] Account broadcast error: {e}")

        # ── Heartbeat ───────────────────────────────────────────────────
        if now - last_heartbeat >= heartbeat_interval:
            await _broadcast(json.dumps({"type": "heartbeat", "time": int(now)}))
            last_heartbeat = now

        await asyncio.sleep(tick_interval)


async def _start_ws_server():
    """Start the WebSocket server and streaming loop."""
    server = await websockets.serve(
        _ws_handler,
        "0.0.0.0",
        WS_PORT,
        ping_interval=30,
        ping_timeout=10,
    )
    log.info(f"[WS] WebSocket server running on ws://0.0.0.0:{WS_PORT}")

    # Run streaming loop forever — don't tie it to server.wait_closed()
    # which can exit when all clients disconnect
    await _streaming_loop()


def _run_ws_server_thread():
    """Run the async WebSocket server in its own event loop (for threading).
    Auto-restarts on crash to keep streaming alive."""
    while True:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_start_ws_server())
        except Exception as e:
            log.error(f"[WS] Server crashed: {e} — restarting in 5s")
            import time as _t
            _t.sleep(5)
        finally:
            try:
                loop.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if not init_mt5():
        log.error("MT5 initialization failed during startup.")
    else:
        log.info("MT5 Webhook server ready on port 5001")
        log.info("Endpoints: /status /account /positions /tick /candles /history /webhook /modify /indicators")

    # Start WebSocket server in background thread (if websockets is installed)
    if _HAS_WEBSOCKETS:
        ws_thread = threading.Thread(
            target=_run_ws_server_thread,
            name="ws-server",
            daemon=True,
        )
        ws_thread.start()
        log.info(f"WebSocket streaming server started on port {WS_PORT}")
    else:
        log.warning("WebSocket server DISABLED — install websockets: pip install websockets")

    # Start Flask HTTP server (main thread — blocking)
    # Wrap in restart loop so the whole server never exits
    while True:
        try:
            app.run(host='0.0.0.0', port=5001, threaded=True)
        except KeyboardInterrupt:
            log.info("Server stopped by user (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"Flask server crashed: {e} — restarting in 5s")
            import time as _t
            _t.sleep(5)
