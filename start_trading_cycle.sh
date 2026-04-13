#!/bin/bash
# start_trading_cycle.sh — One-command start for the full trading system
# Usage:
#   bash start_trading_cycle.sh          # live trading
#   bash start_trading_cycle.sh --dry    # paper trading
#   bash start_trading_cycle.sh --stop   # stop everything
#   bash start_trading_cycle.sh --status # show status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8889
SERVER_IP="92.4.71.177"

# ── ANSI colors ──────────────────────────────────────────────────────────────
GRN='\033[0;32m'; RED='\033[0;31m'; YLW='\033[1;33m'
CYN='\033[0;36m'; BLD='\033[1m';   RST='\033[0m'

header() {
cat << 'EOF'
╔══════════════════════════════════════════════════════════╗
║         MT5 AI TRADING SYSTEM — XAUUSD + XAGUSD         ║
║            Powered by MetaApi + Ollama AI                ║
╚══════════════════════════════════════════════════════════╝
EOF
}

stop_all() {
  echo -e "${YLW}🛑  Stopping all trading processes...${RST}"
  pkill -f "continuous_trader.py" 2>/dev/null && echo "   ✓ Trader stopped"
  pkill -f "auto_recovery.sh"     2>/dev/null && echo "   ✓ Watchdog stopped"
  pkill -f "dashboard.py"         2>/dev/null && echo "   ✓ Dashboard stopped"
  pkill -f "bot.py run"           2>/dev/null && echo "   ✓ Old bot stopped"
  rm -f /tmp/trading_bot.pid /tmp/trading_dashboard.pid
  echo -e "${GRN}   All stopped.${RST}"
}

show_status() {
  echo -e "\n${BLD}═══ Process Status ═══${RST}"
  if pgrep -f "continuous_trader.py" > /dev/null 2>&1; then
    echo -e "  ${GRN}🟢 Trader:    RUNNING${RST} (PID $(pgrep -f continuous_trader.py))"
  else
    echo -e "  ${RED}🔴 Trader:    STOPPED${RST}"
  fi
  if pgrep -f "dashboard.py" > /dev/null 2>&1; then
    echo -e "  ${GRN}🟢 Dashboard: RUNNING${RST} (PID $(pgrep -f dashboard.py))"
  else
    echo -e "  ${RED}🔴 Dashboard: STOPPED${RST}"
  fi
  if pgrep -f "auto_recovery.sh" > /dev/null 2>&1; then
    echo -e "  ${GRN}🟢 Watchdog:  RUNNING${RST} (PID $(pgrep -f auto_recovery.sh))"
  else
    echo -e "  ${YLW}⚠️  Watchdog:  NOT RUNNING${RST}"
  fi

  echo -e "\n${BLD}═══ Account Status ═══${RST}"
  if [ -f "$SCRIPT_DIR/bot_status.json" ]; then
    python3 -c "
import json
d = json.loads(open('bot_status.json').read())
a = d.get('account', {})
st = d.get('stats', {})
print(f\"  Balance:  \${a.get('balance', '—')}\")
print(f\"  Equity:   \${a.get('equity', '—')}\")
print(f\"  Session:  {d.get('session','—')}\")
print(f\"  Cycle:    #{d.get('cycle','—')}\")
print(f\"  Trades:   {st.get('total_trades',0)} ({st.get('wins',0)}W/{st.get('losses',0)}L)\")
pos = d.get('open_positions', [])
print(f\"  Open pos: {len(pos)}\")
for p in pos:
    pr = p.get('profit',0)
    sign = '+' if pr>=0 else ''
    print(f\"    #{p['ticket']} {p['symbol']} {p['direction']} P&L: {sign}{pr:.2f}\")
" 2>/dev/null
  else
    echo "  No status file yet — start the bot first."
  fi

  echo -e "\n${BLD}═══ Recent Log ═══${RST}"
  if [ -f "$SCRIPT_DIR/trading.log" ]; then
    tail -8 "$SCRIPT_DIR/trading.log"
  fi
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
header

case "${1:-}" in
  --stop)
    stop_all
    exit 0
    ;;
  --status)
    show_status
    exit 0
    ;;
  --close)
    echo -e "${YLW}🎯  Closing all open positions...${RST}"
    python3 "$SCRIPT_DIR/continuous_trader.py" --close
    exit 0
    ;;
esac

DRY_FLAG=""
[[ "$1" == "--dry" ]] && DRY_FLAG="--dry"
MODE="${DRY_FLAG:+PAPER TRADE}"; MODE="${MODE:-LIVE TRADING}"

echo -e "${BLD}Mode: ${CYN}$MODE${RST}"
echo -e "Starting all services...\n"

# Stop any existing instances
stop_all
sleep 2

# ── Start dashboard ───────────────────────────────────────────────────────────
echo -e "${CYN}🖥  Starting dashboard...${RST}"
nohup python3 "$SCRIPT_DIR/dashboard.py" > /tmp/dashboard.log 2>&1 &
DASH_PID=$!
echo $DASH_PID > /tmp/trading_dashboard.pid
sleep 2

# Verify dashboard started
if curl -s --max-time 5 http://localhost:$PORT/ > /dev/null 2>&1; then
  echo -e "   ${GRN}✅ Dashboard running at http://$SERVER_IP:$PORT${RST}"
else
  echo -e "   ${YLW}⚠️  Dashboard starting (may take a moment)...${RST}"
fi

# ── Start continuous trader ───────────────────────────────────────────────────
echo -e "${CYN}🚀 Starting continuous trader ($MODE)...${RST}"
nohup python3 "$SCRIPT_DIR/continuous_trader.py" $DRY_FLAG \
  >> "$SCRIPT_DIR/trading.log" 2>&1 &
TRADER_PID=$!
echo $TRADER_PID > /tmp/trading_bot.pid
sleep 3

if kill -0 "$TRADER_PID" 2>/dev/null; then
  echo -e "   ${GRN}✅ Trader running (PID $TRADER_PID)${RST}"
else
  echo -e "   ${RED}❌ Trader failed to start — check trading.log${RST}"
fi

# ── Start watchdog in background ─────────────────────────────────────────────
echo -e "${CYN}🛡  Starting watchdog...${RST}"
nohup bash "$SCRIPT_DIR/auto_recovery.sh" $DRY_FLAG > /tmp/watchdog.log 2>&1 &
echo -e "   ${GRN}✅ Watchdog running${RST}"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}${BLD}═══════════════════════════════════════════${RST}"
echo -e "${GRN}${BLD}  ✅ TRADING SYSTEM STARTED${RST}"
echo -e "${GRN}${BLD}═══════════════════════════════════════════${RST}"
echo -e "  📊 Dashboard:  http://$SERVER_IP:$PORT"
echo -e "  📝 Trade log:  tail -f $SCRIPT_DIR/trading.log"
echo -e "  🔍 Status:     bash start_trading_cycle.sh --status"
echo -e "  🛑 Stop all:   bash start_trading_cycle.sh --stop"
echo -e "  🎯 Close pos:  bash start_trading_cycle.sh --close"
echo ""
echo -e "Monitoring: every 30s | Analysis: every 2min"
echo -e "Profit target: +2% | Loss limit: -1%"
echo ""

# Follow the log
echo -e "${CYN}─── Live Log (Ctrl+C to detach) ───${RST}"
sleep 5
tail -f "$SCRIPT_DIR/trading.log"
