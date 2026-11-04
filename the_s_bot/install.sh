#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="/etc/the_s_bot"
CONFIG_FILE="$CONFIG_DIR/config.json"
APP_DIR="/opt/the-s-bot"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Exécutez ce script en root (sudo $0)." >&2
  exit 1
fi

apt-get update -y
apt-get install -y python3 python3-venv

install -d -o root -g root -m 0750 "$CONFIG_DIR"
install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o root -g root -m 0755 /var/lib/the-s-bot
install -m 0644 "$REPO_ROOT/Thes.py" "$CONFIG_DIR/Thes.py"
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$CONFIG_DIR/requirements.txt"

if ! getent group the-s-bot >/dev/null; then
  groupadd --system the-s-bot
fi
if ! id -u the-s-bot >/dev/null 2>&1; then
  useradd --system --gid the-s-bot --home-dir /nonexistent --shell /usr/sbin/nologin the-s-bot
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  bot_token="${THE_S_BOT_TOKEN:-}"
  admin_id="${THE_S_BOT_SUPER_ADMIN:-}"
  if [[ -z "$bot_token" ]]; then
    read -r -s -p "Token du bot Telegram : " bot_token
    echo
  fi
  if [[ -z "$admin_id" ]]; then
    read -r -p "ID Telegram du super administrateur : " admin_id
  fi
  if [[ ! "$admin_id" =~ ^[0-9]+$ || -z "$bot_token" ]]; then
    echo "Token ou ID super administrateur invalide." >&2
    exit 1
  fi
  BOT_TOKEN="$bot_token" SUPER_ADMIN="$admin_id" python3 - "$CONFIG_FILE" <<'PY'
import json
import os
import sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"bot_token": os.environ["BOT_TOKEN"], "super_admin": int(os.environ["SUPER_ADMIN"])}, handle, indent=2)
    handle.write("\n")
PY
  chmod 0640 "$CONFIG_FILE"
fi

python3 - "$CONFIG_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    cfg = json.load(handle)
if not cfg.get("bot_token") or not str(cfg.get("super_admin", "")).isdigit():
    raise SystemExit("config.json doit contenir bot_token et un super_admin numérique")
PY

if [[ ! -f "$CONFIG_DIR/resellers.json" ]]; then
  printf '{}\n' > "$CONFIG_DIR/resellers.json"
fi
chown root:the-s-bot "$CONFIG_DIR" "$CONFIG_FILE" "$CONFIG_DIR/resellers.json"
chmod 0750 "$CONFIG_DIR"
chmod 0640 "$CONFIG_FILE" "$CONFIG_DIR/resellers.json"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$CONFIG_DIR/requirements.txt"
install -m 0644 "$REPO_ROOT/module/the-s-bot.service" /etc/systemd/system/the-s-bot.service
systemctl daemon-reload
systemctl enable --now the-s-bot.service
systemctl --no-pager --full status the-s-bot.service
