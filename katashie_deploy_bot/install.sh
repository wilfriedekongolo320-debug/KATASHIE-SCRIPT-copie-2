#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Installation du Bot Deploy KATASHIE VPN ==="

python3 -m pip install -r requirements.txt
mkdir -p /etc/katashie_deploy_bot
cp config.example.json /etc/katashie_deploy_bot/config.json
cp deploy_bot.py /etc/katashie_deploy_bot/
cp "$SCRIPT_DIR/../module/katashie-deploy-bot.service" /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable katashie-deploy-bot >/dev/null 2>&1 || true

echo "Éditez /etc/katashie_deploy_bot/config.json avec votre token et votre ID Telegram"
echo "Démarrez avec: systemctl start katashie-deploy-bot"
