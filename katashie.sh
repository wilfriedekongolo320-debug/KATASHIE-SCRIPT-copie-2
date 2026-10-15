#!/bin/bash
# ============================================================
#   KATASHIE VPN — Main Installer (katashie.sh)
#   Remplace: nexus.sh
# ============================================================

# ─── Couleurs ────────────────────────────────────────────────
export RED='\033[0;31m'
export BLUE='\033[0;34m'
export GREEN='\033[0;32m'
export YELLOW='\033[0;33m'
export WHITE='\033[0;37m'
export CYAN='\033[0;36m'
export BOLD='\033[1m'
export BG_BLUE='\033[44m'
export NC='\033[0m'

# ─── Compatibilité héritage (scripts qui utilisent les anciens noms) ──
export LN="${BLUE}"
export BG="${BG_BLUE}"
export GR="${GREEN}"
export RD="${RED}"

# ─── Configuration ────────────────────────────────────────────
readonly SERVER_HOST="https://raw.githubusercontent.com/wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-2/main"
readonly KATASHIE_VERSION="2.0.0"
readonly TIMEZONE="Africa/Douala"
export MYIP=$(wget -qO- ipv4.icanhazip.com 2>/dev/null || curl -s4 https://ipv4.icanhazip.com || hostname -I | awk '{print $1}')

# ─── Helpers ──────────────────────────────────────────────────
TIMESTAMP() { date '+%Y-%m-%d %H:%M:%S'; }
log_info()    { echo -e "${BLUE}[$(TIMESTAMP)] [INFO]${NC}    $*"; }
log_success() { echo -e "${GREEN}[$(TIMESTAMP)] [✔]${NC}      $*"; }
log_warn()    { echo -e "${YELLOW}[$(TIMESTAMP)] [WARN]${NC}    $*"; }
log_error()   { echo -e "${RED}[$(TIMESTAMP)] [✘]${NC}      $*"; }
log_step()    { echo -e "${CYAN}[$(TIMESTAMP)] [STEP]${NC}   ${BOLD}$*${NC}"; }

progress_bar() {
    local duration=${1:-2}
    local label="${2:-Traitement}"
    local width=40
    echo -ne "  ${BLUE}${label}${NC} ["
    for ((i=0; i<=width; i++)); do
        echo -ne "${BLUE}▓${NC}"
        sleep "$(echo "scale=4; $duration/$width" | bc 2>/dev/null || echo 0.05)"
    done
    echo -e "] ${GREEN}✔${NC}"
}

show_banner() {
    clear
    echo -e "${BLUE}"
    cat << 'BANNER'
 ██╗  ██╗ █████╗ ████████╗ █████╗ ███████╗██╗  ██╗██╗███████╗
 ██║ ██╔╝██╔══██╗╚══██╔══╝██╔══██╗██╔════╝██║  ██║██║██╔════╝
 █████╔╝ ███████║   ██║   ███████║███████║███████║██║█████╗
 ██╔═██╗ ██╔══██║   ██║   ██╔══██║╚════██║██╔══██║██║██╔══╝
 ██║  ██╗██║  ██║   ██║   ██║  ██║███████║██║  ██║██║███████╗
 ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝╚══════╝
BANNER
    echo -e "${NC}"
    echo -e "${BLUE}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${BLUE}┃${NC} ${BG_BLUE}          KATASHIE VPN — Auto-Installer v${KATASHIE_VERSION}       ${NC} ${BLUE}┃${NC}"
    echo -e "${BLUE}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo ""
}

# ─── Vérifications préliminaires ─────────────────────────────
check_root_virt() {
    [ "$EUID" -ne 0 ] && { log_error "Ce script doit être exécuté en tant que root."; exit 1; }
    if [ "$(systemd-detect-virt 2>/dev/null)" = "openvz" ]; then
        log_error "OpenVZ n'est pas supporté. Utilisez KVM ou LXC."
        exit 1
    fi
    log_success "Vérification root/virtualisation OK."
}

check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
            log_success "OS supporté: $NAME $VERSION_ID"
            return 0
        else
            log_error "OS non supporté: $ID. Utilisez Ubuntu 22.04 ou Debian 11+."
            exit 1
        fi
    else
        log_error "Impossible de détecter l'OS."
        exit 1
    fi
}

setup_host_time() {
    log_step "Configuration de l'hôte et du fuseau horaire..."
    local localip hst host_entry
    localip=$(hostname -I | awk '{print $1}')
    hst=$(hostname)
    host_entry=$(awk '{print $2}' /etc/hosts | grep -w "$hst" || true)
    [ "$hst" != "$host_entry" ] && echo "$localip $hst" >> /etc/hosts
    ln -fs "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1
    log_success "Fuseau horaire: $TIMEZONE / IPv6 désactivé."
}

prepare_env() {
    log_step "Préparation de l'environnement KATASHIE VPN..."
    mkdir -p /etc/xray /etc/katashie
    touch /etc/xray/domain
    echo "$KATASHIE_VERSION" > /etc/katashie/version
    log_success "Répertoires créés."
}

# ─── Mise à jour système ──────────────────────────────────────
update_system() {
    log_step "Mise à jour du système..."
    apt-get update -y
    apt-get upgrade -y
    apt-get dist-upgrade -y
    apt-get remove --purge -y ufw firewalld exim4 nginx* dropbear* apache2* 2>/dev/null || true
    apt autoremove -y
    log_success "Système mis à jour."
}

install_packages() {
    log_step "Installation des paquets requis..."
    apt-get install -y \
        screen curl jq bzip2 gzip vnstat coreutils rsyslog iftop zip unzip git \
        apt-transport-https build-essential wget figlet ruby-full python3 make cmake \
        net-tools nano sed gnupg gnupg1 bc shc libxml-parser-perl neofetch lsof \
        libsqlite3-dev libz-dev gcc g++ libreadline-dev zlib1g-dev libssl-dev \
        dropbear fail2ban nginx certbot iptables-persistent python3-pip
    if command -v gem >/dev/null; then
        gem install lolcat >/dev/null 2>&1 || true
    fi
    if ! dpkg -s nginx >/dev/null 2>&1; then
        log_error "nginx n'a pas pu être installé. Abandon."
        exit 1
    fi
    log_success "Tous les paquets installés."
}

# ─── Terms & Conditions ───────────────────────────────────────
show_tns() {
    clear
    show_banner
    echo -e "${BLUE}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${BLUE}┃${NC} ${BG_BLUE}              TERMES & CONDITIONS               ${NC} ${BLUE}┃${NC}"
    echo -e "${BLUE}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo -e "${BLUE}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${BLUE}┃${NC} ${GREEN}Bienvenue dans KATASHIE VPN!${NC}"
    echo -e "${BLUE}┃${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] Lisez attentivement les conditions ci-dessous${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] KATASHIE VPN est fourni tel quel, sans garantie.${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] N'utilisez pas ce service pour des activités illégales.${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] KATASHIE VPN n'est pas responsable des pertes de données.${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] Vous devez respecter toutes les lois applicables.${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}[*] Les conditions peuvent changer sans préavis.${NC}"
    echo -e "${BLUE}●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●${NC}"
    echo -e "${BLUE}┃${NC} ${GREEN}[01]${NC} • Accepter les Conditions"
    echo -e "${BLUE}┃${NC} ${RED}[02]${NC} • Refuser et Quitter"
    echo -e "${BLUE}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo -e "${BLUE}●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●${NC}"
    echo ""

    local opt=""
    while true; do
        read -rp "  Votre choix [1/2] : " opt </dev/tty
        opt=$(echo "$opt" | tr -d '\r\n' | xargs)
        
        case "$opt" in
            1|01)
                log_success "Conditions acceptées. Démarrage de l'installation..."
                sleep 2
                add_domain
                break
                ;;
            2|02)
                log_warn "Conditions refusées. Annulation..."
                rm -f /tmp/*.sh
                exit 0
                ;;
            *)
                log_error "Choix invalide. Veuillez entrer 1 pour accepter ou 2 pour refuser."
                ;;
        esac
    done
}

add_domain() {
    clear
    show_banner
    echo -e "${BLUE}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${BLUE}┃${NC} ${BG_BLUE}                 CONFIGURATION DOMAINE          ${NC} ${BLUE}┃${NC}"
    echo -e "${BLUE}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo -e "${BLUE}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${BLUE}┃${NC} ${YELLOW}IP de ce VPS: ${MYIP}${NC}"
    echo -e "${BLUE}┃${NC} ${WHITE}Assurez-vous que votre domaine pointe vers cette IP.${NC}"
    echo -e "${BLUE}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo ""
    while true; do
        read -rp "  Hostname / Domaine : " host </dev/tty
        host=$(echo "$host" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [[ -z "$host" ]]; then
            log_error "Le domaine ne peut pas être vide."
            continue
        fi
        domain_ip=$(getent ahosts "$host" 2>/dev/null | awk '{print $1; exit}')
        if [[ "$domain_ip" == "$MYIP" ]]; then
            break
        else
            log_error "Le domaine ne pointe pas vers ce VPS!"
            echo -e "  ${RED}Domaine résout vers: ${domain_ip:-N/A}${NC}"
            echo -e "  ${RED}IP de ce VPS       : ${MYIP}${NC}"
            echo -e "  ${YELLOW}Corrigez vos DNS et réessayez.${NC}"
            echo ""
        fi
    done
    echo "$host" > /root/domain
    echo "$host" > /etc/xray/domain
    domain=$(cat /etc/xray/domain)
    clear
    log_success "Domaine configuré: ${domain}"
    sleep 3
    log_step "Démarrage de l'installation complète KATASHIE VPN..."
    sleep 2
}

# ─── Scripts core ─────────────────────────────────────────────
run_scripts() {
    log_step "Installation des composants VPN..."
    local scripts=("sshws.sh" "xray.sh" "vpn.sh" "websocket.sh" "setup_zivpn.sh" "setup_dns.sh" "setup_udp.sh" "validator.sh")
    local total=${#scripts[@]}
    local current=0
    for script in "${scripts[@]}"; do
        current=$((current + 1))
        url="${SERVER_HOST}/core/${script}"
        echo -ne "  ${BLUE}[${current}/${total}]${NC} Téléchargement ${script}... "
        if wget -q "$url" -O "/tmp/${script}"; then
            chmod +x "/tmp/${script}"
            echo -e "${GREEN}✔${NC}"
            log_info "Exécution de ${script}..."
            
            # Execution interactive forcée avec </dev/tty
            bash "/tmp/${script}" </dev/tty
            
            log_success "${script} terminé."
        else
            echo -e "${RED}✘${NC}"
            log_warn "Échec du téléchargement de ${script} (non fatal)."
        fi
    done
}

install_menu() {
    log_step "Installation des scripts de menu..."
    mkdir -p "/usr/local/sbin"
    local menus=(dns zivpn expiry domain iptools menu socks ssh status trojan vless vmess vmess_new vlesstls netguard openvpn shadowsocks hysteria2 speedtest tuic wireguard port log tgbot uninstall update web fastdns)
    local failed=0

    for script in "${menus[@]}"; do
        local url="${SERVER_HOST}/menu/${script}.sh"
        local target="/usr/local/sbin/${script}.sh"
        if wget -q -O "$target" "$url"; then
            chmod +x "$target"
            ln -sf "$target" "/usr/local/sbin/${script}" 2>/dev/null || true
            echo -ne "${GREEN}✔${NC} ${script} "
        else
            echo -ne "${RED}✘${NC} ${script} "
            failed=1
        fi
    done

    if wget -q -O "/usr/local/sbin/ui.sh" "${SERVER_HOST}/menu/ui.sh"; then
        chmod +x "/usr/local/sbin/ui.sh"
        echo -ne "${GREEN}✔${NC} ui.sh "
    else
        echo -ne "${RED}✘${NC} ui.sh "
        failed=1
    fi

    if [ -f "/usr/local/sbin/menu.sh" ]; then
        ln -sf "/usr/local/sbin/menu.sh" "/usr/local/bin/menu" 2>/dev/null || true
    fi

    echo ""
    if [ "$failed" -eq 0 ]; then
        log_success "Scripts de menu installés dans /usr/local/sbin/"
    else
        log_warn "Certains scripts de menu n'ont pas pu être installés."
    fi
}

setup_profile() {
    local profile_file="/root/.profile"
    local bashrc_file="/root/.bashrc"

    mkdir -p "/root"

    if ! grep -q '^export PATH="/usr/local/sbin:\$PATH"' "$profile_file" 2>/dev/null; then
        cat >> "$profile_file" << 'PROFILE_EOF'
export PATH="/usr/local/sbin:$PATH"
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi
clear
menu
PROFILE_EOF
    fi

    if ! grep -q '^export PATH="/usr/local/sbin:\$PATH"' "$bashrc_file" 2>/dev/null; then
        cat >> "$bashrc_file" << 'BASHRC_EOF'
export PATH="/usr/local/sbin:$PATH"
BASHRC_EOF
    fi

    log_success "Profil configuré (menu auto au login)."
}

setup_crons() {
    log_step "Configuration des tâches planifiées..."
    grep -q "/usr/local/sbin/log" /etc/crontab || \
        echo "*/30 * * * * root /usr/local/sbin/log" >> /etc/crontab
    grep -q "/usr/local/sbin/expiry" /etc/crontab || \
        echo "55 23 * * * root /usr/local/sbin/expiry" >> /etc/crontab
    log_success "Crons configurés (log@30min, expiry@23:55)."
}

enable_bbr() {
    log_step "Activation de TCP BBR..."
    sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1
    sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1
    grep -q "net.core.default_qdisc" /etc/sysctl.conf || \
        echo "net.core.default_qdisc = fq" >> /etc/sysctl.conf
    grep -q "net.ipv4.tcp_congestion_control" /etc/sysctl.conf || \
        echo "net.ipv4.tcp_congestion_control = bbr" >> /etc/sysctl.conf
    sysctl -p >/dev/null 2>&1
    log_success "TCP BBR activé."
}

restart_services() {
    log_step "Activation et redémarrage des services..."
    local SERVICES=(ssh dropbear stunnel5 cron nginx vnstat fail2ban ws-dropbear ws-stunnel xray runn squid openvpn ohp zivpn dnstt udp-custom)
    for svc in "${SERVICES[@]}"; do
        if systemctl list-unit-files 2>/dev/null | grep -Eq "^${svc}(\\.service)?"; then
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl enable "$svc" >/dev/null 2>&1 || true
            if systemctl restart "$svc" >/dev/null 2>&1 || service "$svc" restart >/dev/null 2>&1; then
                echo -ne "${GREEN}✔${NC} ${svc} "
            else
                echo -ne "${YELLOW}⚠${NC} ${svc} "
            fi
        fi
    done
    echo ""
    log_success "Services démarrés."
}

set_version() {
    echo "$KATASHIE_VERSION" > /etc/katashie/version
    echo "$KATASHIE_VERSION" > /etc/version
    wget -q "$SERVER_HOST/port_info" -O /etc/xray/port_info 2>/dev/null || true
    log_success "Version enregistrée: $KATASHIE_VERSION"
}

katashie_completed() {
    clear
    show_banner
    local domain
    domain=$(cat /etc/xray/domain 2>/dev/null || echo "N/A")
    echo -e "${GREEN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${GREEN}┃${NC} ${BG_BLUE}           INSTALLATION TERMINÉE AVEC SUCCÈS      ${NC} ${GREEN}┃${NC}"
    echo -e "${GREEN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo -e "${GREEN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
    echo -e "${GREEN}┃${NC} ${GREEN}✔ KATASHIE VPN est prêt à l'emploi!${NC}"
    echo -e "${GREEN}┃${NC}"
    echo -e "${GREEN}┃${NC} ${WHITE}Domaine  : ${domain}${NC}"
    echo -e "${GREEN}┃${NC} ${WHITE}IP VPS   : ${MYIP}${NC}"
    echo -e "${GREEN}┃${NC} ${WHITE}Version  : ${KATASHIE_VERSION}${NC}"
    echo -e "${GREEN}┃${NC}"
    echo -e "${GREEN}┃${NC} ${YELLOW}Le serveur va redémarrer dans 10 secondes.${NC}"
    echo -e "${GREEN}┃${NC} ${YELLOW}Reconnectez-vous pour accéder au menu.${NC}"
    echo -e "${GREEN}┃${NC}"
    echo -e "${GREEN}┃${NC} ${WHITE}Script by KATASHIE TEAM${NC}"
    echo -e "${GREEN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
    echo -e "${GREEN}●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●${NC}"
}

cleanner() {
    rm -f /tmp/*.sh 2>/dev/null || true
    log_success "Fichiers temporaires supprimés."
}

main() {
    show_banner
    check_root_virt
    check_os
    setup_host_time
    prepare_env
    update_system
    install_packages
    show_tns
    run_scripts
    install_menu
    setup_profile
    setup_crons
    enable_bbr
    restart_services
    set_version
    katashie_completed
    cleanner
    log_step "Redémarrage dans 10 secondes..."
    sleep 10
    reboot
}

main
