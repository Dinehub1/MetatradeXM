# VM Deployment — Quick Start

**Status:** ✅ Commit created (224c7f6)  
**Ready to deploy to VM**

---

## Option 1: Using Deploy Script (Recommended)

### Prerequisites
- SSH access to VM
- `rsync` installed on both machines
- Python 3.8+ on VM
- .env file with API keys already on VM

### Deploy
```bash
# Make script executable
chmod +x deploy_to_vm.sh

# Deploy to your VM
./deploy_to_vm.sh ubuntu@192.168.1.100 /opt/metatradexm

# Or with custom path:
./deploy_to_vm.sh ubuntu@trading-vm.local /home/ubuntu/metatradexm
```

The script will:
1. ✅ Check SSH connectivity
2. ✅ Validate local Python syntax
3. ✅ Backup remote files
4. ✅ Sync updated files (continuous_trader.py, core/, bridges/)
5. ✅ Validate remote syntax
6. ✅ Test imports

---

## Option 2: Manual Deployment

### Step 1: SSH to VM
```bash
ssh ubuntu@your-vm-ip
cd /path/to/metatradexm
```

### Step 2: Backup current version
```bash
mkdir -p backups
cp -r continuous_trader.py core/ bridges/ backups/backup-$(date +%Y%m%d-%H%M%S)
```

### Step 3: Copy files locally (on your machine)
```bash
scp continuous_trader.py ubuntu@192.168.1.100:/path/to/metatradexm/
scp -r core/ ubuntu@192.168.1.100:/path/to/metatradexm/
scp -r bridges/ ubuntu@192.168.1.100:/path/to/metatradexm/
```

### Step 4: Validate on VM
```bash
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && python3 -m py_compile continuous_trader.py core/ai_client.py"
```

---

## Option 3: Using Git (if VM has git access)

### On VM:
```bash
cd /path/to/metatradexm
git pull origin main
python3 -m py_compile continuous_trader.py core/ai_client.py
```

---

## Post-Deployment Validation

### SSH to VM and test
```bash
ssh ubuntu@your-vm-ip

# Navigate to project
cd /path/to/metatradexm

# Run syntax check
python3 -m py_compile continuous_trader.py core/ai_client.py bridges/webhook_bridge.py

# Run test suite
python3 test_stability_fixes.py

# Expected: 6/6 tests pass
```

### Start trader in test mode (dry-run)
```bash
# Test mode (no real orders)
python3 continuous_trader.py --dry

# Watch logs
tail -f logs/trading.log

# Expected output:
# [INFO] [MEMORY] Trade memory system initialized
# [INFO] ✅ Bridge connected
# [INFO] [AI] Active tiers: T1-NVIDIA → T2-NVIDIA-B...
```

---

## First-Hour Monitoring (After Deploy)

### Check logs in real-time
```bash
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && tail -f logs/trading.log"

# Watch for expected output:
# [INFO] Trade memory system initialized ✅
# [INFO] Bridge connected ✅
# [INFO] Active AI tiers ✅
# [INFO] First trade cycle starting ✅
```

### Monitor state files
```bash
ssh ubuntu@192.168.1.100 "watch -n 1 'cat /path/to/metatradexm/state/bot_status.json | jq .cycle, .stats'"
```

### Check for critical errors
```bash
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && grep CRITICAL logs/trading.log || echo 'No critical errors'"
```

---

## Troubleshooting

### "Cannot connect to SSH"
```bash
# Test connectivity first
ssh -v ubuntu@192.168.1.100 "echo OK"

# Check SSH key
ssh-add ~/.ssh/id_rsa
```

### "Python syntax error on VM"
```bash
# SSH to VM and check
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && python3 -c 'import continuous_trader'"

# If error, rollback:
cp -r backups/backup-LATEST/* .
```

### "Bridge connection fails"
```bash
# Check .env on VM
ssh ubuntu@192.168.1.100 "grep WIN_WEBHOOK_URL /path/to/metatradexm/.env"

# Verify MetaTrader webhook is running
# Check if webhook server is accessible
```

### "AI key validation fails"
```bash
# Check NVIDIA key in .env
ssh ubuntu@192.168.1.100 "grep NVIDIA_API_KEY /path/to/metatradexm/.env"

# Key should start with "nvapi-" and be > 20 chars
```

---

## Rollback Plan

### If deployment fails:
```bash
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && ls -la backups/"

# Restore latest backup
ssh ubuntu@192.168.1.100 "cd /path/to/metatradexm && cp -r backups/backup-LATEST/* ."

# Restart trader
ssh ubuntu@192.168.1.100 "pkill -f continuous_trader.py; sleep 2; cd /path/to/metatradexm && python3 continuous_trader.py &"
```

---

## Configuration (On VM)

Ensure `.env` has these keys set:

```bash
# Required
NVIDIA_API_KEY=nvapi-...
INVOKE_URL=https://integrate.api.nvidia.com/v1/chat/completions

# Broker connection (one of these)
WIN_WEBHOOK_URL=http://localhost:5001      # Local webhook
WIN_WS_URL=ws://localhost:5002             # WebSocket

# Optional but recommended
GEMINI_API_KEY=AIza...  # For fallback
ANALYSIS_INTERVAL_S=60
```

---

## Success Criteria (After Deploy)

✅ Trader starts without errors  
✅ Bridge connects at startup  
✅ Memory system initializes  
✅ First trade cycle completes (0-5 min)  
✅ No CRITICAL errors in logs  
✅ State files created (cooldown, streaks, peaks)  
✅ Peak profits tracked  
✅ Loss cooldowns persist (on restart)  

---

## What Changed

**278 lines of defensive code** across 3 files:
- `continuous_trader.py`: Race condition fix, async I/O, validation
- `core/ai_client.py`: Schema validation, fallback chain, API key checks
- `bridges/webhook_bridge.py`: SSRF vulnerability fix

**Backward compatible:** No API changes, existing config works as-is

---

## Next Steps

After successful deployment:
1. Monitor for 24 hours
2. Verify trades execute correctly
3. Check state file persistence
4. Proceed to Week 2 MEDIUM optimization fixes

---

**Questions? Check:**
- `DEPLOYMENT_GUIDE_FINAL.md` — Full deployment guide
- `WEEK2_HIGH_PRIORITY_FIXES.md` — Details of fixes
- `test_stability_fixes.py` — Validation tests (run on VM)

---

**Ready to deploy! Use:** `./deploy_to_vm.sh <your-vm-host> <remote-path>`
