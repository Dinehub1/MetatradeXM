# Operations & Monitoring

MetatradeXM is designed to run completely autonomously. 

## The Start Script (`start_trading_cycle.sh`)

This script is the control panel. It manages three processes:
1. `dashboard.py` (Flask UI)
2. `continuous_trader.py` (Core Logic)
3. `auto_recovery.sh` (Process Watchdog)

**Commands:**
- Start Live Trading: `bash start_trading_cycle.sh`
- Start Paper Trading: `bash start_trading_cycle.sh --dry`
- Stop All Associated Processes: `bash start_trading_cycle.sh --stop`
- Display PID Status: `bash start_trading_cycle.sh --status`
- Close All Open Positions Immediately: `bash start_trading_cycle.sh --close`

*Note: The script outputs logs to `trading.log` by default.*

## The Dashboard

Available at `http://localhost:8889` (or your server's IP).

The dashboard displays:
- MT5 Account Status (Balance/Equity/Free Margin)
- A list of all Open Positions (with unrealized P&L updating automatically)
- The latest AI Reasoning explaining why a BUY/SELL/HOLD decision was made
- Overall bot Win/Loss record for the session

## Log Inspection

The core operations are logged to `trading.log`. Use tail to monitor decisions in real time:

```bash
tail -f trading.log
```

If the `SmartExitManager` triggers an exit, it will prepend logs with emojis (e.g. ⏰ for Time Decay, 🔄 for Momentum Reversal, 🛡️ for Breakeven).
