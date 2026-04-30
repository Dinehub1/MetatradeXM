#!/bin/bash
# auto_recovery.sh — Process watchdog for dashboard + continuous trader
# Restarts processes if they crash. Run this in a tmux/screen session.
# Usage: bash auto_recovery.sh [--dry]

# Get absolute path to project root (two levels up from deployment/scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

DRY_FLAG=""
[[ "$1" == "--dry" ]] && DRY_FLAG="--dry"

MASTER_LOG="$SCRIPT_DIR/logs/system.log"
RESTART_COUNT_TRADER=0
RESTART_COUNT_DASH=0

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$MASTER_LOG"; }

# ── Start functions ──────────────────────────────────────────────────────────
start_dashboard() {
  log "🖥  Starting dashboard on port 8889..."
  nohup python3 -u "$SCRIPT_DIR/src/dashboard/dashboard.py" >> "$MASTER_LOG" 2>&1 &
  DASH_PID=$!
  echo $DASH_PID > /tmp/trading_dashboard.pid
  log "   Dashboard PID: $DASH_PID"
}

start_trader() {
  log "🚀 Starting continuous trader $DRY_FLAG..."
  nohup python3 -u "$SCRIPT_DIR/src/continuous_trader.py" $DRY_FLAG \
    >> "$MASTER_LOG" 2>&1 &
  TRADER_PID=$!
  echo $TRADER_PID > /tmp/trading_bot.pid
  log "   Trader PID: $TRADER_PID"
  sleep 3
}

# ── Initial start ────────────────────────────────────────────────────────────
log "============================================================"
log "  🛡  AUTO-RECOVERY WATCHDOG STARTED"
log "  Mode: ${DRY_FLAG:---live}"
log "  Trader log: $MASTER_LOG"
log "============================================================"

# Kill any existing instances first
pkill -f "dashboard/dashboard.py" 2>/dev/null
pkill -f "continuous_trader.py" 2>/dev/null
pkill -f "bot.py run" 2>/dev/null
rm -f /tmp/trading_bot.pid /tmp/trading_dashboard.pid /tmp/trading_bot_main.lock /tmp/trading_bot_pid.txt
sleep 2

start_dashboard
start_trader

# ── Watch loop (check every 15s) ─────────────────────────────────────────────
while true; do
  sleep 15

  # Check dashboard
  if ! kill -0 "$DASH_PID" 2>/dev/null; then
    RESTART_COUNT_DASH=$((RESTART_COUNT_DASH + 1))
    log "⚠️  Dashboard crashed (restart #$RESTART_COUNT_DASH) — restarting..."
    start_dashboard
  fi

  # Check dashboard is actually serving (not just running)
  if ! curl -s --max-time 3 http://localhost:8889/ > /dev/null 2>&1; then
    # Give it 30 more seconds before force-restart
    sleep 15
    if ! curl -s --max-time 3 http://localhost:8889/ > /dev/null 2>&1; then
      log "⚠️  Dashboard not responding — force restarting..."
      kill "$DASH_PID" 2>/dev/null
      sleep 1
      start_dashboard
    fi
  fi

  # Check trader
  if ! kill -0 "$TRADER_PID" 2>/dev/null; then
    RESTART_COUNT_TRADER=$((RESTART_COUNT_TRADER + 1))
    log "⚠️  Trader crashed (restart #$RESTART_COUNT_TRADER) — restarting in 10s..."
    sleep 10
    start_trader
  fi

done
