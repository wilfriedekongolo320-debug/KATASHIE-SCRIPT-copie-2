#!/bin/bash

MYIP=$(curl -sS ipv4.icanhazip.com)
readonly SERVER_HOST="https://raw.githubusercontent.com/RootNexTPro/nexTPro-ScriptAll/main"
clear

LN='[34m'
BG='[44m'
NC='[0m'
GR='[32m'
RD='[31m'
GOLD_MAIN='\033[38;5;220m'  # Or pour le logo et les barres
BOLD='\033[1m'

# ==============================================================================
#  FONCTION : FONCTION BARRE DE PROGRESSION (10 BLOCS)
# ==============================================================================
draw_bar() {
    local val=${1:-0}
    local filled=$(( val / 10 ))
    local empty=$(( 10 - filled ))
    local bar=""
    
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done
    echo "$bar"
}

# ==============================================================================
#  RÉCUPÉRATION DES MÉTRIQUES & INFOS SYSTEME
# ==============================================================================
domain=$(cat /etc/xray/domain 2>/dev/null || echo "N/A")
uptime="$(uptime -p 2>/dev/null | cut -d " " -f 2-10)"
IPV4=$(curl -s -4 ifconfig.co)
IPV6=$(curl -s -6 ifconfig.co)
VERSION_FILE="/etc/version"
INSTALLED_VERSION=$(cat "$VERSION_FILE" 2>/dev/null || echo "0.0")
LATEST_VERSION=$(curl -sS "$SERVER_HOST/version" || echo "$INSTALLED_VERSION")
UPDATE_AVAILABLE=0

# Calcul CPU & RAM
CPU_USAGE=$(top -bn1 2>/dev/null | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print int(100 - $1)}')
RAM_USAGE=$(free 2>/dev/null | awk '/Mem:/ {print int($3/$2 * 100)}')

# Génération des barres graphiques
CPU_BAR=$(draw_bar ${CPU_USAGE:-0})
RAM_BAR=$(draw_bar ${RAM_USAGE:-0})

version_greater() {
[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]
}

if version_greater "$LATEST_VERSION" "$INSTALLED_VERSION"; then
UPDATE_AVAILABLE=1
wget -q -O /usr/local/sbin/update "$SERVER_HOST/menu/update.sh" && chmod +x /usr/local/sbin/update
fi

if [ -f /etc/os-release ]; then
. /etc/os-release
OS="$NAME"
VER="$VERSION_ID"
else
OS=$(uname -s)
VER=$(uname -r)
fi

nginx=$( systemctl is-active nginx 2>/dev/null )
if [[ $nginx == "active" ]]; then
status_nginx="${GR}RUN${NC}"
else
status_nginx="${RD}OFF${NC}"
fi

xray=$( systemctl is-active xray 2>/dev/null )
if [[ $xray == "active" ]]; then
status_xray="${GR}RUN${NC}"
else
status_xray="${RD}OFF${NC}"
fi

ssh_ws=$( systemctl is-active ws-stunnel 2>/dev/null )
if [[ $ssh_ws == "active" ]]; then
status_ws="${GR}RUN${NC}"
else
status_ws="${RD}OFF${NC}"
fi

clear

# ==============================================================================
#  1. BANNER ASCII 4K "THE_S" EN OR ET CENTRÉE
# ==============================================================================
read -r -d '' BANNER << 'EOF'
████████╗██╗  ██╗███████╗    ███████╗
╚══██╔══╝██║  ██║██╔════╝    ██╔════╝
   ██║   ███████║█████╗      ███████╗
   ██║   ██╔══██║██╔══╝      ╚════██║
   ██║   ██║  ██║███████╗    ███████║
   ╚═╝   ╚═╝  ╚═╝╚══════╝    ╚══════╝
EOF

TERM_WIDTH=$(tput cols 2>/dev/null || echo 80)
echo -e "${GOLD_MAIN}${BOLD}"
while IFS= read -r line; do
    LINE_LEN=${#line}
    PADDING=$(( (TERM_WIDTH - LINE_LEN) / 2 ))
    if [ $PADDING -gt 0 ]; then
        printf "%*s%s\n" "$PADDING" "" "$line"
    else
        echo "$line"
    fi
done <<< "$BANNER"
echo -e "${NC}"

# ==============================================================================
#  2. BLOCS DU MENU AVEC BARRES CPU/RAM
# ==============================================================================
echo -e "${LN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${LN}┃${NC}  OS         : $OS $VER"
echo -e "${LN}┃${NC}  UPTIME     : $uptime"
printf "${LN}┃${NC}  CPU USAGE  : [${GOLD_MAIN}%s${NC}] ${GOLD_MAIN}%3d%%${NC}\n" "$CPU_BAR" "${CPU_USAGE:-0}"
printf "${LN}┃${NC}  RAM USAGE  : [${GOLD_MAIN}%s${NC}] ${GOLD_MAIN}%3d%%${NC}\n" "$RAM_BAR" "${RAM_USAGE:-0}"
echo -e "${LN}┃${NC}  IPv4       : ${IPV4:-N/A}"
if [ -n "$IPV6" ]; then
echo -e "${LN}┃${NC}  IPv6       : $IPV6"
fi
echo -e "${LN}┃${NC}  DOMAIN     : $domain"
echo -e "${LN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e "${LN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${LN}┃${NC}   NGINX : [${status_nginx}]    XRAY : [${status_xray}]    WS : [${status_ws}]"
echo -e "${LN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e "${LN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${LN}┃${NC} ${BG}                       MENU                     ${NC} ${LN}┃${NC}"
echo -e "${LN}┃${NC}"
echo -e "${LN}┃${NC} [01] • SSH/WS MENU        [04] • TROJAN MENU"
echo -e "${LN}┃${NC} [02] • VMESS MENU         [05] • SOCKS MENU"
echo -e "${LN}┃${NC} [03] • VLESS MENU         [06] • ZIVPN MENU"
echo -e "${LN}┗━━━━━━━━━━━━━━━━━━━━━━━━ 🜲THE_S ━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e "${LN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${LN}┃${NC} ${BG}                      TOOLS                     ${NC} ${LN}┃${NC}"
echo -e "${LN}┃${NC}"
echo -e "${LN}┃${NC} [07] • DNS PANEL          [11] • NETGUARD PANEL"
echo -e "${LN}┃${NC} [08] • DOMAIN PANEL       [12] • VPN PORT INFO"
echo -e "${LN}┃${NC} [09] • IPV6 PANEL         [13] • CLEAN VPS LOGS"
echo -e "${LN}┃${NC} [10] • VPS STATUS         [14] • 🜲THE_S BOT PANEL"
echo -e "${LN}┃${NC} [15] • UNINSTALL THE_S    [16] • FAST DNS MENU"
echo -e "${LN}┃${NC}"
echo -e "${LN}┃${NC} [00] • EXIT               [88] • REBOOT VPS"
echo -e "${LN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e "${LN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${LN}┃${NC} ${BG}                   WEB PANEL                    ${NC} ${LN}┃${NC}"
echo -e "${LN}┃${NC}"
echo -e "${LN}┃${NC} [18] • 🜲THE_S PANEL"
echo -e "${LN}┃${NC}"
echo -e "${LN}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"

if [ "$UPDATE_AVAILABLE" -eq 1 ]; then
echo -e "${RD}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${RD}┃${NC} ${RD}[99] • UPDATE SCRIPT (v$LATEST_VERSION)${NC}"
echo -e "${RD}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
fi

# Footer en Rouge et Texte centré
VERSION=$(cat /etc/version 2>/dev/null || echo "2.1.0")
FOOTER_TEXT="VERSION:  ${VERSION}  |  SCRIPT BY: 🜲THE_S  
|  CONTACT Admin: +237 621 67 16 48"
FOOTER_LEN=${#FOOTER_TEXT}
FOOTER_PADDING=$(( (TERM_WIDTH - FOOTER_LEN) / 2 ))

echo -e "${RD}${BOLD}"
if [ $FOOTER_PADDING -gt 0 ]; then
    printf "%*s%s\n" "$FOOTER_PADDING" "" "$FOOTER_TEXT"
else
    echo "$FOOTER_TEXT"
fi
echo -e "${NC}"

read -p " Select menu :  "  opt
echo -e ""

case $opt in
1 | 01) clear ; ssh ;;
2 | 02) clear ; vmess ;;
3 | 03) clear ; vless ;;
4 | 04) clear ; trojan ;;
5 | 05) clear ; socks ;;
6 | 06) clear ; zivpn ;;
7 | 07) clear ; dns ;;
8 | 08) clear ; domain ;;
9 | 09) clear ; iptools ;;
10) clear ; status ;;
11) clear ; netguard ;;
12) clear ; port ;;
13) clear ; log ;;
14) clear ; tgbot ;;
15) clear ; uninstall ;;
16) clear ; fastdns ;;
18) clear ; web ;;
88) reboot ;;
99) clear ; update ;;
0 | 00) exit ;;
*) clear ; menu ;;
esac
