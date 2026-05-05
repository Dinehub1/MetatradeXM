#!/bin/bash
# start_trading_cycle.sh — One-command start for the full trading system
# Usage:
#   bash start_trading_cycle.sh          # live trading
#   bash start_trading_cycle.sh --dry    # paper trading
#   bash start_trading_cycle.sh --stop   # stop everything
#   bash start_trading_cycle.sh --status # show status

# Get absolute path to project root (two levels up from deployment/scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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
║       Powered by Windows Bridge + NVIDIA AI              ║
╚══════════════════════════════════════════════════════════╝
EOF
}

stop_all() {
  echo -e "${YLW}🛑  Stopping all trading processes...${RST}"
  pkill -f "continuous_trader.py" 2>/dev/null && echo "   ✓ Trader stopped"
  pkill -f "scripts/glm51_cron.py" 2>/dev/null && echo "   ✓ GLM analyzer stopped"
  pkill -f "auto_recovery.sh"     2>/dev/null && echo "   ✓ Watchdog stopped"
  pkill -f "dashboard.py"         2>/dev/null && echo "   ✓ Dashboard stopped"
  pkill -f "bot.py run"           2>/dev/null && echo "   ✓ Old bot stopped"
  lsof -ti tcp:${PORT} | xargs kill -9 2>/dev/null || true
  rm -f /tmp/trading_bot.pid /tmp/trading_dashboard.pid
  rm -f /tmp/trading_bot_main.lock /tmp/trading_bot_pid.txt
  sleep 1  # give OS time to release the port
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
  if pgrep -f "scripts/glm51_cron.py --loop" > /dev/null 2>&1; then
    echo -e "  ${GRN}🟢 GLM 5.1:   RUNNING${RST} (PID $(pgrep -f 'scripts/glm51_cron.py --loop'))"
  else
    echo -e "  ${RED}🔴 GLM 5.1:   STOPPED${RST}"
  fi
  if pgrep -f "auto_recovery.sh" > /dev/null 2>&1; then
    echo -e "  ${GRN}🟢 Watchdog:  RUNNING${RST} (PID $(pgrep -f auto_recovery.sh))"
  else
    echo -e "  ${YLW}⚠️  Watchdog:  NOT RUNNING${RST}"
  fi

  echo -e "\n${BLD}═══ Account Status ═══${RST}"
  if [ -f "$SCRIPT_DIR/data/bot_status.json" ]; then
    python3 -c "
import json
d = json.loads(open('data/bot_status.json').read())
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
  if [ -f "$SCRIPT_DIR/logs/system.log" ]; then
    tail -8 "$SCRIPT_DIR/logs/system.log"
  fi
  echo -e "\n${BLD}═══ GLM Analysis ═══${RST}"
  if [ -f "$SCRIPT_DIR/state/glm51_status.json" ]; then
    python3 -c "
import json
from pathlib import Path
p = Path('$SCRIPT_DIR/state/glm51_status.json')
d = json.loads(p.read_text())
print(f\"  State:      {d.get('state', '—')}\")
print(f\"  Last run:   {d.get('last_run_at', d.get('started_at', '—'))}\")
print(f\"  Last report:{d.get('last_report', '—')}\")
ts = d.get('trade_summary', {})
if ts:
    print(f\"  Trades:     {ts.get('total_closed',0)} closed | WR {ts.get('win_rate',0)}% | Pips {ts.get('total_pips',0)}\")
mr = d.get('market_readiness', {})
if mr:
    print(f\"  Bias:       {mr.get('bias', '—')}\")
err = d.get('last_error')
if err:
    print(f\"  Last error: {err}\")
" 2>/dev/null
  else
    echo "  No GLM status file yet — wait for the first analysis cycle."
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
mkdir -p "$SCRIPT_DIR/logs"
nohup python3 -u "$SCRIPT_DIR/src/dashboard/dashboard.py" >> "$SCRIPT_DIR/logs/system.log" 2>&1 &
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
nohup python3 -u "$SCRIPT_DIR/src/continuous_trader.py" $DRY_FLAG \
  >> "$SCRIPT_DIR/logs/system.log" 2>&1 &
TRADER_PID=$!
echo $TRADER_PID > /tmp/trading_bot.pid
sleep 3

if kill -0 "$TRADER_PID" 2>/dev/null; then
  echo -e "   ${GRN}✅ Trader running (PID $TRADER_PID)${RST}"
else
  echo -e "   ${RED}❌ Trader failed to start — check trading.log${RST}"
fi

# ── Start GLM 5.1 deep market analyzer ──────────────────────────────────────
echo -e "${CYN}🧠 Starting GLM 5.1 market analyzer (every 10 min)...${RST}"
cd "$SCRIPT_DIR"
nohup python3 -u scripts/glm51_cron.py --loop --interval 10 \
  >> "$SCRIPT_DIR/logs/glm51_analysis.log" 2>&1 &
GLM_PID=$!
sleep 3

if kill -0 "$GLM_PID" 2>/dev/null; then
  echo -e "   ${GRN}✅ GLM 5.1 analyzer running (PID $GLM_PID)${RST}"
else
  echo -e "   ${YLW}⚠️  GLM 5.1 analyzer failed — check logs/glm51_analysis.log${RST}"
fi

# ── Start watchdog in background ─────────────────────────────────────────────
echo -e "${CYN}🛡  Starting watchdog...${RST}"
nohup bash "$SCRIPT_DIR/deployment/scripts/auto_recovery.sh" $DRY_FLAG > /tmp/watchdog.log 2>&1 &
echo -e "   ${GRN}✅ Watchdog running${RST}"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}${BLD}═══════════════════════════════════════════${RST}"
echo -e "${GRN}${BLD}  ✅ TRADING SYSTEM STARTED${RST}"
echo -e "${GRN}${BLD}═══════════════════════════════════════════${RST}"
echo -e "  📊 Dashboard:  http://$SERVER_IP:$PORT"
echo -e "  📝 Trade log:  tail -f $SCRIPT_DIR/logs/system.log"
echo -e "  🧠 GLM log:    tail -f $SCRIPT_DIR/logs/glm51_analysis.log"
echo -e "  🔍 Status:     bash start_trading_cycle.sh --status"
echo -e "  🛑 Stop all:   bash start_trading_cycle.sh --stop"
echo -e "  🎯 Close pos:  bash start_trading_cycle.sh --close"
echo ""
echo -e "Monitoring: every 30s | Trader analysis: every 2min | GLM deep analysis: every 10min"
echo -e "Profit target: +2% | Loss limit: -1%"
echo ""

# Follow the log
echo -e "${CYN}─── Live Log (Ctrl+C to detach) ───${RST}"
sleep 1
tail -f "$SCRIPT_DIR/logs/system.log"
