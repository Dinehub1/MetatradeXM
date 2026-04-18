#!/bin/bash
echo "=== Cloudflare Tunnel Status ==="
if systemctl is-active --quiet cf-tunnel.service; then
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /home/ubuntu/trading-bot/cf-tunnel.log | tail -1)
    echo "Status: ACTIVE"
    echo "URL:    $URL"
    echo ""
    echo "Login:  admin / mt5bot2026!"
else
    echo "Status: STOPPED"
    sudo systemctl status cf-tunnel.service --no-pager | head -5
fi
