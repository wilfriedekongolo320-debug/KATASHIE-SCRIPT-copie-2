#!/usr/bin/env bash
set -euo pipefail

# Installation du bot de surveillance uniquement. Ce programme ne gère pas
# les comptes SSH/VPN et ne lance aucune commande d'administration système.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/install.sh" "$@"
