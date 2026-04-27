#!/bin/bash
# deploy.sh
# Quickly syncs local changes to the VPS and restarts the bot

set -e

VPS_USER="ubuntu"
VPS_IP="92.4.71.177"
SSH_KEY="$HOME/Documents/sshkey/ssh-key-2026-03-16.key"
TARGET_DIR="~/MetatradeXM"

echo "============================================"
echo "🚀 Deploying MetatradeXM to Production VPS"
echo "============================================"

# Optional: Add and push to Git automatically
read -p "Do you want to commit and push changes to GitHub first? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    git add .
    read -p "Enter commit message: " commit_msg
    git commit -m "${commit_msg:-"Auto-deployment update"}"
    git push
    echo "✅ Pushed to GitHub"
fi

echo "📦 Syncing files to VPS ($VPS_IP)..."
rsync -avz --exclude '.git' --exclude 'node_modules' --exclude '__pycache__' --exclude 'venv' --exclude '.venv' \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    ./ $VPS_USER@$VPS_IP:$TARGET_DIR/

echo "🔄 Restarting metatradeXM-bot on VPS..."
ssh -i $SSH_KEY -o StrictHostKeyChecking=no $VPS_USER@$VPS_IP "cd $TARGET_DIR && pm2 restart metatradeXM-bot"

echo "============================================"
echo "✅ Deployment Complete! The bot is live."
echo "============================================"
