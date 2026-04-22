import MetaTrader5 as mt5
if not mt5.initialize():
    print("MT5 Init Failed:", mt5.last_error())
else:
    print("MT5 Init Success")
    print(mt5.terminal_info())
    mt5.shutdown()
