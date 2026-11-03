#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Installation du Bot WhatsApp KATASHIE VPN ==="

python3 -m pip install -r requirements.txt
mkdir -p /etc/katashie_whatsapp_bot
cp config.example.json /etc/katashie_whatsapp_bot/config.json
cp whatsapp_bot.py /etc/katashie_whatsapp_bot/
cp "$SCRIPT_DIR/../module/katashie-whatsapp-bot.service" /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable katashie-whatsapp-bot >/dev/null 2>&1 || true

echo "Éditez /etc/katashie_whatsapp_bot/config.json avec vos credentials Twilio"
echo "Configurez le webhook Twilio sur: https://VOTRE_DOMAINE:5001/whatsapp"
echo "Démarrez avec: systemctl start katashie-whatsapp-bot"
