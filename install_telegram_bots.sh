#!/usr/bin/env bash
set -euo pipefail

# Download and install the two Telegram bots from the same raw GitHub ref
# as autoinstall.sh / katashie.sh.
SERVER_HOST="${SERVER_HOST:-https://raw.githubusercontent.com/wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-2/main}"
INSTALL_ROOT="/opt/katashie-bots"

log() { printf '[KATASHIE BOTS] %s\n' "$*"; }
fail() { printf '[KATASHIE BOTS] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    fail "Exécutez ce script en root."
fi

command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || fail "curl ou wget est requis."

log "Installation des prérequis Python..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ca-certificates

fetch() {
    local relative_path="$1"
    local destination="$2"
    mkdir -p "$(dirname "$destination")"
    local url="${SERVER_HOST%/}/${relative_path}"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --retry 3 "$url" --output "$destination" \
            || fail "Téléchargement échoué: ${relative_path}"
    else
        wget --quiet --tries=3 "$url" --output-document="$destination" \
            || fail "Téléchargement échoué: ${relative_path}"
    fi
    [ -s "$destination" ] || fail "Fichier téléchargé vide: ${relative_path}"
}

fetch_bot() {
    local name="$1"
    shift
    local target="${INSTALL_ROOT}/${name}"
    mkdir -p "$target"
    log "Téléchargement des fichiers du bot ${name}..."
    while [ "$#" -gt 0 ]; do
        fetch "$1" "${target}/${1##*/}"
        shift
    done
}

fetch_bot core \
    katashie_core_bot/katashie_bot.py \
    katashie_core_bot/requirements.txt \
    katashie_core_bot/config.example.json \
    katashie_core_bot/modules/__init__.py \
    katashie_core_bot/modules/admin_core.py \
    katashie_core_bot/modules/ssh_core.py \
    katashie_core_bot/modules/system_core.py \
    katashie_core_bot/modules/xray_core.py \
    katashie_core_bot/modules/zivpn_core.py

fetch_bot deploy \
    katashie_deploy_bot/deploy_bot.py \
    katashie_deploy_bot/requirements.txt \
    katashie_deploy_bot/config.example.json

log "Création des environnements Python et installation des dépendances..."
for name in core deploy; do
    python3 -m venv "${INSTALL_ROOT}/${name}/venv"
    "${INSTALL_ROOT}/${name}/venv/bin/python" -m pip install --upgrade pip
    "${INSTALL_ROOT}/${name}/venv/bin/python" -m pip install -r "${INSTALL_ROOT}/${name}/requirements.txt"
done

# Preserve user configuration when this installer is run again.
install -d -m 0750 /etc/katashie_bot /etc/katashie_deploy_bot
if [ ! -s /etc/katashie_bot/config.json ]; then
    install -m 0600 "${INSTALL_ROOT}/core/config.example.json" /etc/katashie_bot/config.json
fi
if [ ! -s /etc/katashie_deploy_bot/config.json ]; then
    install -m 0600 "${INSTALL_ROOT}/deploy/config.example.json" /etc/katashie_deploy_bot/config.json
fi

cat > /etc/systemd/system/katashie-core-bot.service <<UNIT
[Unit]
Description=KATASHIE VPN Core Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_ROOT}/core
Environment=PYTHONUNBUFFERED=1
Environment=KATASHIE_BOT_CONFIG=/etc/katashie_bot/config.json
ExecStart=${INSTALL_ROOT}/core/venv/bin/python -u ${INSTALL_ROOT}/core/katashie_bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/katashie-deploy-bot.service <<UNIT
[Unit]
Description=KATASHIE VPN Deploy Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_ROOT}/deploy
Environment=PYTHONUNBUFFERED=1
Environment=KATASHIE_DEPLOY_CONFIG=/etc/katashie_deploy_bot/config.json
ExecStart=${INSTALL_ROOT}/deploy/venv/bin/python -u ${INSTALL_ROOT}/deploy/deploy_bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

# Don't start bots with sample/empty credentials. Enable/start only once a real
# token and at least one numeric admin ID are present in each config.
configured() {
    local config="$1"
    python3 - "$config" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    token = cfg.get("bot_token", "")
    admins = cfg.get("admin_ids", [])
    valid_token = isinstance(token, str) and ":" in token and "VOTRE" not in token.upper()
    valid_admins = isinstance(admins, list) and any(str(x).isdigit() for x in admins)
    raise SystemExit(0 if valid_token and valid_admins else 1)
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
PY
}

for spec in "core:/etc/katashie_bot/config.json:katashie-core-bot" "deploy:/etc/katashie_deploy_bot/config.json:katashie-deploy-bot"; do
    IFS=: read -r name config service <<< "$spec"
    if configured "$config"; then
        systemctl enable --now "$service"
        log "${service} activé et démarré."
    else
        log "${service} installé mais non démarré: renseignez bot_token et admin_ids dans ${config}, puis lancez systemctl enable --now ${service}."
    fi
done

log "Téléchargement et installation des fichiers des bots terminés."
