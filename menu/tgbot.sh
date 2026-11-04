#!/usr/bin/env bash
set -euo pipefail

# Option 14 du menu principal : installer ou mettre à jour le bot Thes.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${THE_S_BOT_REPO:-wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-2}"
REF="${THE_S_BOT_REF:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Exécutez l'installation en root (sudo)." >&2
  exit 1
fi

if [[ -f "$SCRIPT_DIR/../the_s_bot/install.sh" ]]; then
  THE_S_BOT_REPO="$REPO" THE_S_BOT_REF="$REF" bash "$SCRIPT_DIR/../the_s_bot/install.sh"
else
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  url="https://raw.githubusercontent.com/${REPO}/${REF}/the_s_bot/install.sh"
  curl --fail --location --silent --show-error --retry 3 "$url" -o "$installer"
  THE_S_BOT_REPO="$REPO" THE_S_BOT_REF="$REF" bash "$installer"
fi

printf '\nBot Thes installé/mis à jour. Appuyez sur Entrée pour revenir au menu.\n'
read -r _ || true
if [[ -x "$SCRIPT_DIR/menu.sh" ]]; then
  exec bash "$SCRIPT_DIR/menu.sh"
fi
