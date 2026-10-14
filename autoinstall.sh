#!/bin/bash
# ============================================================
#   KATASHIE VPN — Auto-Installeur One-Line
#   BUG FIX: SERVER_HOST pointait sur 'YOUR_GITHUB' placeholder.
#   Maintenant corrigé vers le dépôt officiel KATASHIE VPN.
#   GitHub: https://github.com/abesskamer237/KATASHIE_VPN
# ============================================================

export DEBIAN_FRONTEND=noninteractive

# ─── BUG FIX: Dépôt GitHub corrigé ──────────────────────────
export SERVER_HOST="https://github.com/wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-/main"
export TIMEZONE="${TIMEZONE:-Africa/Douala}"

RED='\033[0;31m'
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
WHITE='\033[0;37m'
NC='\033[0m'

log_info()    { printf "%b\n" "${BLUE}[INFO]${NC} $*"; }
log_success() { printf "%b\n" "${GREEN}[OK]${NC}   $*"; }
log_error()   { printf "%b\n" "${RED}[ERROR]${NC} $*" >&2; exit 1; }

progress_bar() {
    local duration=${1:-2}
    local label="${2:-Traitement}"
    local width=40
    echo -ne "  ${BLUE}${label}${NC} ["
    for ((i=0; i<=width; i++)); do
        echo -ne "${BLUE}=${NC}"
        sleep "$(echo "scale=4; $duration/$width" | bc 2>/dev/null || echo 0.05)"
    done
    echo -e "] ${GREEN}DONE${NC}"
}

clear
echo -e "${BLUE}"
cat << 'BANNER'
 ██╗  ██╗ █████╗ ████████╗ █████╗ ███████╗██╗  ██╗██╗███████╗
 ██║ ██╔╝██╔══██╗╚══██╔══╝██╔══██╗██╔════╝██║  ██║██║██╔════╝
 █████╔╝ ███████║   ██║   ███████║███████╗███████║██║█████╗
 ██╔═██╗ ██╔══██║   ██║   ██╔══██║╚════██║██╔══██║██║██╔══╝
 ██║  ██╗██║  ██║   ██║   ██║  ██║███████║██║  ██║██║███████╗
 ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝╚══════╝
                       KATASHIE VPN
BANNER
echo -e "${NC}"
echo -e "${WHITE}════════════════════════════════════════════════════${NC}"
echo -e "${WHITE}   Commercial VPN Auto-Installer — KATASHIE VPN     ${NC}"
echo -e "${WHITE}   GitHub: github.com/abesskamer237/KATASHIE_VPN    ${NC}"
echo -e "${WHITE}════════════════════════════════════════════════════${NC}"
echo ""

# ─── Prérequis ───────────────────────────────────────────────
[ "$EUID" -ne 0 ] && log_error "Exécutez en root : sudo bash autoinstall.sh"

log_info "Mise à jour des outils de base..."
apt-get update -y >/dev/null 2>&1
apt-get install -y wget curl bc >/dev/null 2>&1
log_success "Outils installés."

log_info "Optimisation réseau (IPv4 prioritaire)..."
echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf 2>/dev/null || true
sysctl -w net.ipv6.conf.all.disable_ipv6=1     >/dev/null 2>&1
sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1
log_success "Réseau optimisé."

log_info "Téléchargement du script principal KATASHIE VPN..."
if wget -qO /root/katashie.sh "${SERVER_HOST}/katashie.sh" 2>/dev/null; then
    log_success "Script principal téléchargé."
    chmod +x /root/katashie.sh
    log_info "Lancement de l'installation KATASHIE VPN..."
    bash /root/katashie.sh
else
    log_error "ERREUR FATALE: Impossible de contacter GitHub.
Vérifiez votre connexion internet et réessayez.
URL: ${SERVER_HOST}/katashie.sh"
fi
