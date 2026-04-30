import json
from continuous_trader import build_order_params

# Dummy configuration
sym_cfg = {
    "symbol": "XAUUSD",
    "broker": "XAUUSD",
    "lot": 0.1,  # base lot
    "sl": 3.0,   # default SL in points (ignored if using ATR)
    "tp": 6.0,   # default TP in points
    "sl_pips": 30.0,
    "tp_pips": 60.0,
    "pip": 1.0,
    "digits": 2,
    "points_multiplier": 1.0
}

class DummyTick:
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask

tick = DummyTick(4700.00, 4700.20)

directions = ["BUY", "SELL"]
confidences = [0.40, 0.60, 0.85]
atr = 5.0

print("Testing Kelly Sizing & Regime-based Risk/Reward\\n" + "-"*60)

for conf in confidences:
    # 1. Test in a standard regime (e.g. RANGING)
    print(f"\\n[CONFIDENCE: {conf*100:.0f}%] - Standard Ranging Regime")
    regime_data = {"regime": "RANGING_CHOP", "volatility_state": "COMPRESSED"}
    order = build_order_params(sym_cfg, tick, "BUY", conf, atr, 1.0, regime_data)
    print(f"  -> LOT: {order['lot']}, SL_pips: {round(4700.20 - order['sl'], 2)}, TP_pips: {round(order['tp'] - 4700.20, 2)}")

    # 2. Test in a strong trend (High Risk:Reward + Kelly scale up)
    print(f"[CONFIDENCE: {conf*100:.0f}%] - Strong Trend Regime (Expanding Volatility)")
    regime_data = {"regime": "STRONG_TREND_UP", "volatility_state": "EXPANDING"}
    order = build_order_params(sym_cfg, tick, "BUY", conf, atr, 1.0, regime_data)
    print(f"  -> LOT: {order['lot']}, SL_pips: {round(4700.20 - order['sl'], 2)}, TP_pips: {round(order['tp'] - 4700.20, 2)}")
