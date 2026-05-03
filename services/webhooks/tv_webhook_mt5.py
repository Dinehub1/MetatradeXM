"""
Standalone TradingView Webhook Receiver for MT5 via MetaApi
Runs on port 5001 and does not interfere with the Delta Trading Bot.
"""
import os
import time
import sys
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Add project root to sys.path so 'bridges' can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bridges.metaapi_bridge import MetaApiBridge

# Load environment variables from the project root
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

app = Flask(__name__)
AUTH_TOKEN = os.getenv("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026")

@app.before_request
def require_auth():
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {AUTH_TOKEN}":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

bridge = MetaApiBridge()

def init_bridge():
    print("Initializing MetaApi Bridge for TV Webhook...")
    if not bridge.connect():
        print("FATAL: Could not connect to MetaApi. Check METAAPI_TOKEN and account status.")
    else:
        print("✅ TV Webhook successfully connected to MT5 via MetaApi")

# Connect on startup
init_bridge()

@app.route('/webhook', methods=['POST'])
def tv_webhook():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Missing JSON payload"}), 400

        print(f"\n[Webhook Received] {data}")

        # Check connection
        if not bridge.connected:
            print("  ⚠️ Bridge disconnected, attempting reconnect...")
            bridge.connect()
            if not bridge.connected:
                return jsonify({"status": "error", "message": "Broker disconnected"}), 503

        action = data.get("action", "").upper()
        symbol = data.get("symbol")

        if not action or not symbol:
            return jsonify({"status": "error", "message": "action and symbol are required"}), 400

        # Action: CLOSE
        if action == "CLOSE":
            print(f"  --> Processing CLOSE for {symbol}")
            positions = bridge.get_open_positions(symbol)
            if not positions:
                print("  --> No open positions to close.")
                return jsonify({"status": "success", "message": "No open positions"}), 200
            
            closed_tickets = []
            for p in positions:
                if bridge.close_position(p.ticket):
                    closed_tickets.append(p.ticket)
                    print(f"  --> Closed ticket #{p.ticket}")
                else:
                    print(f"  --> Failed to close ticket #{p.ticket}")
                    
            return jsonify({"status": "success", "closed_tickets": closed_tickets}), 200

        # Action: BUY / SELL
        elif action in ["BUY", "SELL"]:
            lot = float(data.get("lot", 0.01))
            sl = data.get("sl")
            tp = data.get("tp")
            comment = data.get("comment", "TV-Webhook")

            print(f"  --> Processing {action} for {symbol} | Lot: {lot} | SL: {sl} | TP: {tp}")
            
            params = {
                "symbol": symbol,
                "direction": action,
                "lot": lot,
                "comment": comment
            }
            if sl is not None:
                params["sl"] = float(sl)
            if tp is not None:
                params["tp"] = float(tp)

            res = bridge.place_order(params)
            if res and hasattr(res, "order"):
                print(f"  ✅ Order placed successfully! ID: {res.order}")
                return jsonify({"status": "success", "order_id": res.order}), 200
            else:
                print("  ❌ Order failed")
                return jsonify({"status": "error", "message": "Failed to place order"}), 500

        else:
            return jsonify({"status": "error", "message": f"Invalid action: {action}"}), 400

    except Exception as e:
        print(f"  ❌ Webhook Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "broker_connected": bridge.connected
    }), 200

if __name__ == "__main__":
    print("\n🚀 Starting TradingView MT5 Webhook Receiver on port 5001...")
    # Use host=0.0.0.0 to expose the port externally (needed for TV to hit the VPS)
    app.run(host="0.0.0.0", port=5001)
