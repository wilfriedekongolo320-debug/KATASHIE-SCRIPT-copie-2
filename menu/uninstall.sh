#!/bin/bash
clear
# Variables de couleurs du Nexus Tunnel Pro
LN='\e[36m'
NC='\e[0m'
BG='\e[44m'
RD='\e[31m'
GR='\e[32m'
YW='\e[33m'
WH='\e[37m'

echo -e "${RD}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${RD}┃${NC} ${WH}       DÉSINSTALLATION TOTALE : THE_S VPN      ${NC} ${RD}┃${NC}"
echo -e "${RD}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e ""
echo -e " ${YW}⚠️ ATTENTION : Cette action est IRRÉVERSIBLE.${NC}"
echo -e " Tous les comptes SSH, VLESS, VMESS, TROJAN et ZIVPN"
echo -e " seront détruits. Le Bot Telegram sera déconnecté."
echo -e " Le serveur redeviendra totalement vierge."
echo -e ""
echo -e "${LN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
read -p " ➔ Confirmer la destruction ? (y/N): " confirm
echo -e "${LN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo -e "\n ${GR}➔ Annulation du protocole. Retour au menu...${NC}"
    sleep 2
    menu
    exit
fi

echo -e "\n ${RD}► INITIALISATION DU PROTOCOLE DE PURGE...${NC}\n"

echo -ne " ${WH}▪ Purge des comptes clients VPN...${NC}"
users=$(awk -F: '($3 >= 1000 && $1 != "nobody" && $7 == "/bin/false") {print $1}' /etc/passwd)
for u in $users; do
    userdel -f "$u" 2>/dev/null
done
sleep 1
echo -e "${GR}[ OK ]${NC}"

echo -ne " ${WH}▪ Arrêt et neutralisation des Démons...${NC}"
systemctl stop xray zivpn katashie_bot dropbear stunnel4 ws-dropbear ws-stunnel >/dev/null 2>&1
systemctl disable xray zivpn katashie_bot dropbear stunnel4 ws-dropbear ws-stunnel >/dev/null 2>&1
sleep 1
echo -e "${GR}[ OK ]${NC}"

echo -ne " ${WH}▪ Éradication des bases de données...${NC}"
rm -rf /etc/xray /etc/zivpn /etc/slowdns /etc/katashie_bot /root/katashie_bot_engine /root/nexus_core_bot
rm -f /etc/systemd/system/katashie_bot.service
sleep 1
echo -e "${GR}[ OK ]${NC}"

echo -ne " ${WH}▪ Nettoyage profond du système Linux...${NC}"
apt-get remove --purge -y xray stunnel4 dropbear >/dev/null 2>&1
apt-get autoremove -y >/dev/null 2>&1
apt-get clean
sleep 1
echo -e "${GR}[ OK ]${NC}"

echo -e "\n${RD}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓${NC}"
echo -e "${RD}┃${NC} ${GR}      ✅ INFRASTRUCTURE DÉTRUITE AVEC SUCCÈS     ${NC} ${RD}┃${NC}"
echo -e "${RD}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛${NC}"
echo -e ""
echo -e " ${WH}Le serveur est redevenu vierge.${NC}"
echo -e " ${WH}Le menu va maintenant disparaître.${NC}\n"

# Destruction finale de l'accès menu
rm -f /usr/local/sbin/menu
rm -rf /root/Script-hysteria-and-any-other
exit
