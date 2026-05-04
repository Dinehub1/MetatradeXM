import sys, os
sys.path.append(os.path.join(os.getcwd(), 'src'))
from bridges.mt5_bridge import MT5Bridge
bridge = MT5Bridge()
print("Tick:", bridge.get_tick('GOLD.i#'))
print("D1:", bridge.get_candles('GOLD.i#', 'D1', 1))
