#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TARGET_DIR="/etc/katashie_bot"
SERVER_HOST="${SERVER_HOST:-https://raw.githubusercontent.com/wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-2/main}"

log() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$EUID" -eq 0 ] || fail "Ce script doit être exécuté en root."

log "=== Téléchargement du Bot Telegram Principal KATASHIE VPN ==="

mkdir -p "$TARGET_DIR"

fetch() {
  local rel_path="$1"
  local dest="$2"
  local url="${SERVER_HOST}/${rel_path}"
  mkdir -p "$(dirname "$dest")"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 "$url" -o "$dest"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 -O "$dest" "$url"
  else
    fail "curl ou wget est requis pour télécharger le bot."
  fi
}

fetch "katashie_core_bot/katashie_bot.py" "$TARGET_DIR/katashie_bot.py"
fetch "katashie_core_bot/requirements.txt" "$TARGET_DIR/requirements.txt"
fetch "katashie_core_bot/config.example.json" "$TARGET_DIR/config.example.json"

mkdir -p "$TARGET_DIR/modules"
fetch "katashie_core_bot/modules/__init__.py" "$TARGET_DIR/modules/__init__.py"
fetch "katashie_core_bot/modules/admin_core.py" "$TARGET_DIR/modules/admin_core.py"
fetch "katashie_core_bot/modules/ssh_core.py" "$TARGET_DIR/modules/ssh_core.py"
fetch "katashie_core_bot/modules/system_core.py" "$TARGET_DIR/modules/system_core.py"
fetch "katashie_core_bot/modules/xray_core.py" "$TARGET_DIR/modules/xray_core.py"
fetch "katashie_core_bot/modules/zivpn_core.py" "$TARGET_DIR/modules/zivpn_core.py"

python3 -m pip install --upgrade pip
python3 -m pip install -r "$TARGET_DIR/requirements.txt"

if [ ! -f /etc/katashie_bot/config.json ]; then
  cp "$TARGET_DIR/config.example.json" /etc/katashie_bot/config.json
fi

cat > /etc/systemd/system/katashie-core-bot.service <<'EOF'
[Unit]
Description=KATASHIE VPN — Core Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/etc/katashie_bot
Environment=PYTHONUNBUFFERED=1
Environment=KATASHIE_BOT_CONFIG=/etc/katashie_bot/config.json
ExecStart=/usr/bin/python3 /etc/katashie_bot/katashie_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable katashie-core-bot >/dev/null 2>&1 || true

echo "Le bot a été téléchargé dans /etc/katashie_bot"
echo "N'oubliez pas de remplir /etc/katashie_bot/config.json"
echo "Lancez ensuite: systemctl start katashie-core-bot"
