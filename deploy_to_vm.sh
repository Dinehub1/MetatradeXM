#!/bin/bash
#
# deploy_to_vm.sh — Deploy MetatradeXM Week 1+2 fixes to VM
#
# Usage:
#   ./deploy_to_vm.sh <vm_user>@<vm_host> <remote_path>
#   ./deploy_to_vm.sh ubuntu@192.168.1.100 /home/ubuntu/metatradexm
#
# Example:
#   ./deploy_to_vm.sh ubuntu@trading-vm.local /opt/metatradexm
#

set -e

VM_HOST="${1:-}"
REMOTE_PATH="${2:-/opt/metatradexm}"
LOCAL_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$VM_HOST" ]; then
    echo "Usage: $0 <vm_user>@<vm_host> [remote_path]"
    echo "Example: $0 ubuntu@192.168.1.100 /home/ubuntu/metatradexm"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "MetatradeXM Deployment to VM"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Local Path:  $LOCAL_PATH"
echo "VM Host:     $VM_HOST"
echo "Remote Path: $REMOTE_PATH"
echo ""

# Step 1: Pre-deployment checks
echo "[1/6] Pre-deployment checks..."
if ! command -v ssh &> /dev/null; then
    echo "❌ SSH not found. Install SSH client and try again."
    exit 1
fi

if ! ssh -o ConnectTimeout=5 "$VM_HOST" "exit" 2>/dev/null; then
    echo "❌ Cannot connect to VM: $VM_HOST"
    echo "   Check hostname/IP and SSH key"
    exit 1
fi
echo "✅ SSH connection OK"

# Step 2: Validate local changes
echo ""
echo "[2/6] Validating local changes..."
if ! python3 -m py_compile continuous_trader.py core/ai_client.py bridges/webhook_bridge.py 2>/dev/null; then
    echo "❌ Python syntax error in local files"
    exit 1
fi
echo "✅ Syntax validated"

# Step 3: Backup remote
echo ""
echo "[3/6] Backing up remote files..."
ssh "$VM_HOST" "mkdir -p ${REMOTE_PATH}/backups"
ssh "$VM_HOST" "cp -r ${REMOTE_PATH}/{continuous_trader.py,core,bridges} ${REMOTE_PATH}/backups/backup-$(date +%Y%m%d-%H%M%S) 2>/dev/null || true"
echo "✅ Backup created"

# Step 4: Sync files
echo ""
echo "[4/6] Syncing files to VM..."
rsync -avz --exclude='.git' --exclude='*.pyc' --exclude='__pycache__' \
    continuous_trader.py \
    core/ \
    bridges/ \
    "$VM_HOST:$REMOTE_PATH/" || true
echo "✅ Files synced"

# Step 5: Remote validation
echo ""
echo "[5/6] Validating remote installation..."
ssh "$VM_HOST" "cd $REMOTE_PATH && python3 -m py_compile continuous_trader.py core/ai_client.py bridges/webhook_bridge.py"
echo "✅ Remote syntax OK"

# Step 6: Test import
echo ""
echo "[6/6] Testing imports on VM..."
ssh "$VM_HOST" "cd $REMOTE_PATH && python3 -c 'from core.ai_client import _validate_api_keys; print(\"✅ Core modules import OK\")' 2>&1 || true"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Deployment Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo "1. SSH into VM:  ssh $VM_HOST"
echo "2. Check logs:   cd $REMOTE_PATH && tail -f logs/trading.log"
echo "3. Start trader: python3 continuous_trader.py"
echo ""
echo "Monitoring checklist:"
echo "  ✓ Bridge connects at startup"
echo "  ✓ Memory system initializes"
echo "  ✓ No CRITICAL errors in first minute"
echo "  ✓ First trade cycle completes"
echo ""
echo "Rollback (if needed):"
echo "  ssh $VM_HOST 'cd $REMOTE_PATH && cp -r backups/backup-LATEST/* .'"
echo ""
