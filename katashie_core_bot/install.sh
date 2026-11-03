#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "=== Installation du Bot Telegram Principal KATASHIE VPN ==="
python3 -m pip install -r requirements.txt
python3 -m pip check
python3 -m compileall -q katashie_bot.py modules
install -d -m 0750 /etc/katashie_bot /var/lib/katashie_bot
# Keep the deployed token, Telegram IDs and existing config intact on upgrades.
if [ ! -f /etc/katashie_bot/config.json ]; then
  install -m 0600 config.example.json /etc/katashie_bot/config.json
  echo "Configurez /etc/katashie_bot/config.json avec le vrai token et les identifiants Telegram."
fi
install -m 0755 katashie_bot.py /etc/katashie_bot/katashie_bot.py
install -d -m 0755 /etc/katashie_bot/modules
cp -a modules/. /etc/katashie_bot/modules/
install -m 0644 "$SCRIPT_DIR/../module/katashie-core-bot.service" /etc/systemd/system/katashie-core-bot.service
systemctl daemon-reload
systemctl enable katashie-core-bot
systemctl restart katashie-core-bot
echo "Bot relancé. Logs: journalctl -u katashie-core-bot -f"
