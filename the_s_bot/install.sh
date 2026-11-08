#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="/etc/the_s_bot"
CONFIG_FILE="$CONFIG_DIR/config.json"
RESELLERS_FILE="$CONFIG_DIR/resellers.json"
CONVS_FILE="$CONFIG_DIR/convs.json"
VISITORS_FILE="$CONFIG_DIR/visitors.json"
APP_DIR="/opt/the-s-bot"
DEFAULT_BRAND="🔥THE_S-TUNNEL-PRO☄️📶🛜"

if [[ "${EUID}" -ne 0 ]]; then
  echo "❌ Exécutez ce script en root (sudo bash install.sh)." >&2
  exit 1
fi

# Question posée au clavier. On lit /dev/tty uniquement pour la réponse : le script, lui,
# continue d'être lu normalement (indispensable pour « curl ... | bash »).
ask() { # ask <variable> <question> [secret]
  local __answer=""
  if ! { : < /dev/tty; } 2>/dev/null; then
    echo "❌ Aucun terminal disponible. Définissez THE_S_BOT_TOKEN et THE_S_BOT_SUPER_ADMIN, puis relancez." >&2
    exit 1
  fi
  if [[ "${3:-}" == "secret" ]]; then
    read -r -s -p "$2" __answer < /dev/tty
    echo > /dev/tty
  else
    read -r -p "$2" __answer < /dev/tty
  fi
  printf -v "$1" '%s' "$__answer"
}

echo "📦 Mise à jour du système et installation des dépendances..."
apt-get update -y
apt-get install -y ca-certificates curl cron python3 python3-venv python3-pip

# 1. Création de l'arborescence des dossiers nécessaires
install -d -o root -g root -m 0700 "$CONFIG_DIR"
install -d -o root -g root -m 0700 "$CONFIG_DIR/modules"
install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o root -g root -m 0755 /var/lib/the-s-bot

# Écriture du fichier requirements.txt
printf 'pyTelegramBotAPI\n' > "$CONFIG_DIR/requirements.txt"

# 2. Écriture du fichier principal Thes.py
echo "⚙️ Écriture du fichier Thes.py..."
cat << 'EOF' > "$CONFIG_DIR/Thes.py"
import telebot
import subprocess
import shlex
import html
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import json
import os
import logging
import threading
import re
from datetime import datetime, timedelta
from modules import system_core, ssh_core, admin_core, xray_core, zivpn_core

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
CONFIG_DIR     = '/etc/the_s_bot'
CONFIG_FILE    = f'{CONFIG_DIR}/config.json'
RESELLERS_FILE = f'{CONFIG_DIR}/resellers.json'
CONVS_FILE     = f'{CONFIG_DIR}/convs.json'
VISITORS_FILE  = f'{CONFIG_DIR}/visitors.json'

def load_config():
    if not os.path.exists(CONFIG_FILE): return None
    with open(CONFIG_FILE, 'r') as f: return json.load(f)

config = load_config()
if not config: raise SystemExit("config.json introuvable ou vide : relancez install.sh")

bot         = telebot.TeleBot(config.get('bot_token'))
SUPER_ADMIN = int(config.get('super_admin'))
DEFAULT_BRAND = '🔥THE_S-TUNNEL-PRO☄️📶🛜'

def clean_brand(value):
    """La marque est insérée dans des messages HTML : on retire < > &."""
    value = re.sub(r'<[^>]*>', '', str(value or ''))
    value = re.sub(r'[<>&]', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value or DEFAULT_BRAND

BRAND       = clean_brand(config.get('brand', DEFAULT_BRAND))

def own_kwargs(uid):
    """Un revendeur est restreint à ses propres comptes ; un admin ne l'est pas."""
    return {"requester_id": uid, "restrict": not is_admin(uid)}

def hx(value):
    """Échappe un texte (nom, mot de passe, message...) avant de l'insérer dans un message HTML Telegram."""
    return html.escape(str(value), quote=False)

def validate_username(username):
    if not username or len(username) < 4:
        return False, "❌ Minimum 4 caractères.\n📌 Exemple : <code>jean01</code>"
    if " " in username:
        return False, "❌ Pas d'espaces autorisés.\n📌 Exemple : <code>jean01</code>"
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return False, "❌ Caractères autorisés : lettres, chiffres, <code>_</code> et <code>-</code> uniquement.\n📌 Exemple : <code>jean_01</code>"
    return True, ""

def validate_password(password):
    if len(password) < 4:
        return False, (
            f"❌ Mot de passe trop court.\n\n"
            f"➡️ <code>{hx(password)}</code> ⬅️ ({len(password)} caractère(s))\n\n"
            f"📌 Minimum 4 caractères. Exemple : <code>pass1234</code>"
        )
    return True, ""

def validate_days(text, max_days):
    if not text.isdigit():
        return False, (
            f"❌ Valeur invalide : <code>{hx(text)}</code>\n\n"
            f"📌 Entrez un nombre entier. Exemple : <code>7</code>\n"
            f"⏳ Maximum autorisé : <b>{max_days} jour(s)</b>"
        )
    days_int = int(text)
    if days_int < 1:
        return False, (
            f"❌ Le nombre de jours doit être au moins 1.\n"
            f"📌 Exemple : <code>7</code> | Max : <b>{max_days}j</b>"
        )
    if days_int > max_days:
        return False, (
            f"❌ Durée refusée : <b>{days_int}j</b> dépasse votre maximum.\n"
            f"⏳ Maximum autorisé : <b>{max_days} jour(s)</b>\n"
            f"📌 Exemple : <code>{max_days}</code>"
        )
    return True, ""

def cancel_keyboard(back_menu):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data=f"cancel_to_{back_menu}"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_to_"))
def handle_cancel(call):
    uid      = call.from_user.id
    target   = call.data[len("cancel_to_"):]
    user_sessions.pop(uid, None)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(
        call.message.chat.id,
        "❌ <b>Opération annulée.</b>",
        parse_mode="HTML",
        reply_markup=protocol_menu_keyboard(target, uid) if target != "home" else main_menu_keyboard(uid)
    )

def load_resellers():
    if not os.path.exists(RESELLERS_FILE): return {}
    with open(RESELLERS_FILE, 'r') as f: return json.load(f)

def save_resellers(data):
    os.makedirs(os.path.dirname(RESELLERS_FILE), exist_ok=True)
    with open(RESELLERS_FILE, 'w') as f: json.dump(data, f, indent=2)

def load_convs():
    if not os.path.exists(CONVS_FILE): return {}
    with open(CONVS_FILE, 'r') as f: return json.load(f)

def save_convs(data):
    os.makedirs(os.path.dirname(CONVS_FILE), exist_ok=True)
    with open(CONVS_FILE, 'w') as f: json.dump(data, f, indent=2)

def load_visitors():
    if not os.path.exists(VISITORS_FILE): return {}
    with open(VISITORS_FILE, 'r') as f: return json.load(f)

def save_visitors(data):
    os.makedirs(os.path.dirname(VISITORS_FILE), exist_ok=True)
    with open(VISITORS_FILE, 'w') as f: json.dump(data, f, indent=2)

def add_conv_msg(uid, alias, text, direction):
    convs = load_convs()
    key   = str(uid)
    if key not in convs:
        convs[key] = {"alias": alias, "messages": []}
    convs[key]["alias"] = alias
    convs[key]["messages"].append({
        "direction": direction,
        "sender":    alias if direction == "from" else BRAND,
        "text":      text,
        "time":      datetime.now().strftime("%d/%m %H:%M")
    })
    convs[key]["messages"] = convs[key]["messages"][-50:]
    save_convs(convs)

def register_visitor(user):
    visitors = load_visitors()
    key = str(user.id)
    if key not in visitors:
        visitors[key] = {
            "first_name": user.first_name or "N/A",
            "username":   user.username or "N/A",
            "joined_at":  datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        save_visitors(visitors)
        try:
            bot.send_message(
                SUPER_ADMIN,
                f"🆕 <b>Nouvel utilisateur a lancé le bot {BRAND}</b>\n\n"
                f"👤 Nom : {html.escape(user.first_name or 'N/A')}\n"
                f"🔗 Username : @{html.escape(user.username or 'N/A')}\n"
                f"🆔 ID : <code>{user.id}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass

def get_reseller(uid):
    return load_resellers().get(str(uid))

def is_active(uid):
    r = get_reseller(uid)
    if not r: return False
    added_str = r.get("added_datetime") or (r["added_date"] + " 00:00:00")
    added     = datetime.strptime(added_str, "%Y-%m-%d %H:%M:%S")
    max_hours = r.get("max_hours", 48 if r["type"] == "trial" else r.get("max_days", 30) * 24)
    return datetime.now() <= added + timedelta(hours=max_hours)

def fmt_remain(uid):
    r = get_reseller(uid)
    if not r: return "N/A"
    added_str = r.get("added_datetime") or (r["added_date"] + " 00:00:00")
    added     = datetime.strptime(added_str, "%Y-%m-%d %H:%M:%S")
    max_hours = r.get("max_hours", 48 if r["type"] == "trial" else r.get("max_days", 30) * 24)
    expiry    = added + timedelta(hours=max_hours)
    remain    = expiry - datetime.now()
    if remain.total_seconds() <= 0: return "Expiré"
    days  = remain.days
    hours = remain.seconds // 3600
    return f"{days}j {hours}h" if days > 0 else f"{hours}h"

def days_remaining(uid):
    if is_admin(uid): return 9999
    r = get_reseller(uid)
    if not r: return 0
    added_str = r.get("added_datetime") or (r["added_date"] + " 00:00:00")
    added     = datetime.strptime(added_str, "%Y-%m-%d %H:%M:%S")
    max_hours = r.get("max_hours", 48 if r["type"] == "trial" else r.get("max_days", 30) * 24)
    expiry    = added + timedelta(hours=max_hours)
    remain    = expiry - datetime.now()
    return max(0, remain.days)

def _expiration_loop():
    while True:
        try:
            resellers = load_resellers()
            to_remove = []
            for rid, info in resellers.items():
                uid_int   = int(rid)
                added_str = info.get("added_datetime") or (info["added_date"] + " 00:00:00")
                added     = datetime.strptime(added_str, "%Y-%m-%d %H:%M:%S")
                max_hours = info.get("max_hours", 48 if info["type"] == "trial" else info.get("max_days", 30) * 24)
                alias     = info.get("alias", rid)
                if datetime.now() > added + timedelta(hours=max_hours):
                    to_remove.append(rid)
                    try:
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_pps"))
                        bot.send_message(
                            uid_int,
                            f"⛔ <b>Abonnement expiré à {BRAND}</b>\n\n"
                            f"Votre abonnement est arrivé à son terme.\n"
                            f"Contactez {BRAND} pour le renouveler.",
                            parse_mode="HTML",
                            reply_markup=markup
                        )
                    except Exception:
                        pass
                    try:
                        bot.send_message(
                            SUPER_ADMIN,
                            f"ℹ️ Revendeur <b>{html.escape(str(alias))}</b> (<code>{rid}</code>) expiré — supprimé automatiquement.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            if to_remove:
                for rid in to_remove: del resellers[rid]
                save_resellers(resellers)
        except Exception:
            pass
        threading.Event().wait(300)

threading.Thread(target=_expiration_loop, daemon=True).start()

def is_admin(user_id):
    cfg = load_config()
    return user_id == SUPER_ADMIN or user_id in cfg.get('admins', [])

def has_access(user_id):
    return is_admin(user_id) or (get_reseller(user_id) is not None and is_active(user_id))

def notify_owner_account_created(creator_id, proto, username, days, password=""):
    if creator_id == SUPER_ADMIN:
        return
    try:
        r     = get_reseller(creator_id)
        alias = hx(r.get("alias", str(creator_id)) if r else "OWNER")
        msg   = (
            f"🔔 <b>Nouveau compte créé sur {BRAND}</b>\n\n"
            f"👤 Revendeur : <b>{alias}</b> (<code>{creator_id}</code>)\n"
            f"🔌 Protocole : <b>{proto.upper()}</b>\n"
            f"🧑 Username  : <code>{hx(username)}</code>\n"
        )
        if password:
            msg += f"🔑 Mot de passe : <code>{hx(password)}</code>\n"
        msg += f"⏳ Durée : <b>{days} jour(s)</b>"
        bot.send_message(SUPER_ADMIN, msg, parse_mode="HTML")
    except Exception:
        pass

def get_system_info():
    ip          = subprocess.getoutput("curl -s -m 5 ifconfig.me").strip()
    domain      = subprocess.getoutput("cat /etc/xray/domain 2>/dev/null || echo 'N/A'").strip()
    slowdns_pub = subprocess.getoutput("cat /etc/slowdns/server.pub 2>/dev/null || echo 'N/A'").strip()
    return ip, domain, slowdns_pub

def main_menu_keyboard(uid):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔑 SSH/WS",      callback_data="menu_ssh"),
        InlineKeyboardButton("🔰 VMESS",       callback_data="menu_vmess"),
        InlineKeyboardButton("🔰 VLESS",       callback_data="menu_vless"),
        InlineKeyboardButton("🔰 TROJAN",      callback_data="menu_trojan"),
        InlineKeyboardButton("🔥 SLOW DNS",    callback_data="menu_slowdns"),
        InlineKeyboardButton("🚀 UDP FAST",    callback_data="menu_udpfast"),
        InlineKeyboardButton("🔌 SOCKS",       callback_data="menu_socks"),
        InlineKeyboardButton("📱 ZIVPN",       callback_data="menu_zivpn"),
        InlineKeyboardButton("📊 VPS STATUS",  callback_data="menu_status"),
        InlineKeyboardButton("🧹 CLEAN LOGS",  callback_data="menu_log"),
    )
    if is_admin(uid):
        markup.add(
            InlineKeyboardButton("👥 Revendeurs",  callback_data="menu_resellers"),
            InlineKeyboardButton("💬 Messagerie",  callback_data="msg_panel"),
            InlineKeyboardButton("🔄 REBOOT VPS",  callback_data="action_reboot"),
            InlineKeyboardButton("👑 ADMINS",       callback_data="menu_admins"),
        )
    if uid == SUPER_ADMIN:
        markup.add(InlineKeyboardButton("🏷️ Définir ma marque", callback_data="set_brand_menu"))
    else:
        markup.add(InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_pps"))
    if not is_admin(uid):
        markup.add(InlineKeyboardButton("📂 Mes comptes", callback_data="my_accounts"))
    return markup

def protocol_menu_keyboard(proto, uid):
    markup = InlineKeyboardMarkup(row_width=1)
    d_rem  = days_remaining(uid)
    note   = f"  (max {d_rem}j)" if not is_admin(uid) and d_rem < 9999 else ""
    markup.add(
        InlineKeyboardButton(f"➕ Créer compte {proto.upper()}{note}", callback_data=f"add_{proto}"),
        InlineKeyboardButton(f"🔄 Renouveler compte {proto.upper()}",  callback_data=f"renew_{proto}"),
        InlineKeyboardButton(f"🗑️ Supprimer compte {proto.upper()}",   callback_data=f"del_{proto}"),
        InlineKeyboardButton(f"📋 Liste des comptes {proto.upper()}",  callback_data=f"list_{proto}"),
    )
    if proto == 'ssh':
        markup.add(
            InlineKeyboardButton("🔒 Verrouiller un compte",   callback_data="lock_ssh"),
            InlineKeyboardButton("🔓 Déverrouiller un compte", callback_data="unlock_ssh"),
        )
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    return markup

def _show_submenu(call, text, markup):
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    uid = message.from_user.id
    register_visitor(message.from_user)
    if not has_access(uid):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_pps_unauth"))
        bot.send_message(
            uid,
            f"⛔ <b>Accès refusé à {BRAND}</b>\n\n"
            f"Vous n'êtes pas autorisé à utiliser ce bot.\n"
            f"Contactez <b>{BRAND}</b> pour obtenir un accès.",
            parse_mode="HTML",
            reply_markup=markup
        )
        return
    r      = get_reseller(uid)
    rtype  = r['type'].upper() if r else "OWNER"
    remain = fmt_remain(uid) if r else ""
    extra  = f"\n🏷️ Type : <b>{rtype}</b> | ⏳ Restant : <b>{remain}</b>" if r else ""
    bot.send_message(
        uid,
        f"<b>{BRAND}</b>{extra}\n\nSélectionnez un Protocole ou un module :",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(uid)
    )

@bot.callback_query_handler(func=lambda call: call.data == "action_home")
def home_callback(call):
    uid = call.from_user.id
    if not has_access(uid): return
    r      = get_reseller(uid)
    rtype  = r['type'].upper() if r else "OWNER"
    remain = fmt_remain(uid) if r else ""
    extra  = f"\n🏷️ Type : <b>{rtype}</b> | ⏳ Restant : <b>{remain}</b>" if r else ""
    _show_submenu(call, f"<b>{BRAND}</b>{extra}\n\nSélectionnez un module :", main_menu_keyboard(uid))

@bot.callback_query_handler(func=lambda call: call.data == "set_brand_menu")
def set_brand_callback(call):
    if call.from_user.id != SUPER_ADMIN:
        bot.answer_callback_query(call.id, "⛔ Réservé au Super Admin.")
        return
    bot.answer_callback_query(call.id, "🏷️ Utilisez /setbrand <nom>")
    bot.send_message(
        call.message.chat.id,
        f"🏷️ <b>Définir votre marque personnalisée</b>\n\n"
        f"Marque actuelle : <b>{BRAND}</b>\n\n"
        f"📌 <b>Utilisez la commande :</b>\n"
        f"<code>/setbrand VOTRE_NOM</code>\n\n"
        f"👉 Exemple : <code>/setbrand 🔥THE_S-TUNNEL-PRO☄️📶🛜</code>\n\n"
        f"✅ Le nouveau nom remplacera <b>{BRAND}</b> partout dans le bot.",
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data in (
    "menu_ssh","menu_vmess","menu_vless","menu_trojan","menu_socks","menu_zivpn"
))
def protocol_submenu(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    _show_submenu(call, f"<b>Module {proto.upper()} — {BRAND}</b>\nChoisissez une action :",
                  protocol_menu_keyboard(proto, uid))

user_sessions = {}

@bot.callback_query_handler(func=lambda call: call.data in ("contact_pps", "contact_pps_unauth"))
def contact_pps_callback(call):
    uid = call.from_user.id
    user_sessions[uid] = {"state": "contact_pps"}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data="cancel_to_home"))
    try:
        bot.edit_message_text(
            f"📩 <b>Contacter {BRAND}</b>\n\nÉcrivez votre message en une seule fois :",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception:
        bot.send_message(uid,
            f"📩 <b>Contacter {BRAND}</b>\n\nÉcrivez votre message en une seule fois :",
            parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "contact_pps")
def handle_contact_message(message):
    uid   = message.from_user.id
    name  = message.from_user.first_name or str(uid)
    uname = message.from_user.username or "N/A"
    r     = get_reseller(uid)
    alias = r.get("alias", name) if r else name
    rtype = r['type'].upper() if r else "NON REVENDEUR"
    user_sessions.pop(uid, None)
    add_conv_msg(uid, alias, message.text, "from")
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"↩️ Répondre à {alias}", callback_data=f"reply_{uid}"))
    try:
        bot.send_message(
            SUPER_ADMIN,
            f"📩 <b>Nouveau Message reçu 💬</b>\n\n"
            f"👤 {hx(alias)} | 🏷️ {rtype}\n"
            f"🆔 <code>{uid}</code> | @{hx(uname)}\n\n"
            f"{hx(message.text or '')}",
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception: pass
    bot.send_message(uid, f"✅ Message envoyé à <b>{BRAND}</b>.", parse_mode="HTML",
                     reply_markup=main_menu_keyboard(uid) if has_access(uid) else None)

@bot.callback_query_handler(func=lambda call: call.data == "msg_panel" and is_admin(call.from_user.id))
def msg_panel(call):
    resellers = load_resellers()
    convs     = load_convs()
    kb = [[InlineKeyboardButton("📢 Broadcast — Tous", callback_data="msg_broadcast")]]
    for rid, info in resellers.items():
        alias  = info.get("alias", rid)
        rtype  = info.get("type", "?").upper()
        unread = len(convs.get(rid, {}).get("messages", []))
        kb.append([InlineKeyboardButton(
            f"💬 {alias} ({rtype}) | ID:{rid} — {unread} msgs",
            callback_data=f"msg_conv_{rid}"
        )])
    visitors = load_visitors()
    for vid, vinfo in visitors.items():
        if vid in resellers or int(vid) == SUPER_ADMIN: continue
        kb.append([InlineKeyboardButton(
            f"👀 {vinfo['first_name']} (visiteur) | ID:{vid}",
            callback_data=f"msg_conv_{vid}"
        )])
    kb.append([InlineKeyboardButton("🔙 Retour", callback_data="action_home")])
    _show_submenu(call,
        f"<b>💬 Messagerie de {BRAND}</b>\n\nChoisissez un destinataire :",
        InlineKeyboardMarkup(kb))

@bot.callback_query_handler(func=lambda call: call.data.startswith("msg_conv_") and is_admin(call.from_user.id))
def msg_conv(call):
    uid   = call.from_user.id
    tid   = call.data[9:]
    convs = load_convs()
    info  = convs.get(tid, {})
    r     = load_resellers().get(tid, {})
    alias = info.get("alias") or r.get("alias", tid)
    rtype = r.get("type", "visiteur").upper()
    msgs  = info.get("messages", [])
    hist  = ""
    for m in msgs[-15:]:
        arrow = "➡️" if m["direction"] == "to" else "⬅️"
        hist += f"{arrow} {hx(m['sender'])} [{m['time']}]\n{hx(m['text'])}\n\n"
    if not hist: hist = "Aucun message précédent.\n\n"
    user_sessions[uid] = {"state": "msg_to_user", "target_id": int(tid), "alias": alias}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data="msg_panel"))
    _show_submenu(call,
        f"<b>💬 Conversation avec {hx(alias)} ({rtype})</b>\n"
        f"🆔 <code>{tid}</code>\n\n{hist}"
        f"Écrivez votre message :",
        markup)

@bot.callback_query_handler(func=lambda call: call.data == "msg_broadcast" and is_admin(call.from_user.id))
def msg_broadcast(call):
    uid = call.from_user.id
    user_sessions[uid] = {"state": "broadcast"}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data="msg_panel"))
    _show_submenu(call,
        f"<b>📢 Broadcast — Tous les revendeurs</b>\n\nÉcrivez votre message :",
        markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reply_") and is_admin(call.from_user.id))
def reply_to_user(call):
    uid   = call.from_user.id
    tid   = int(call.data[6:])
    r     = load_resellers().get(str(tid), {})
    v     = load_visitors().get(str(tid), {})
    alias = r.get("alias") or v.get("first_name", str(tid))
    user_sessions[uid] = {"state": "msg_to_user", "target_id": tid, "alias": alias}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data="msg_panel"))
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception: pass
    bot.send_message(uid,
        f"↩️ <b>Répondre à {hx(alias)}</b>\n\nÉcrivez votre réponse :",
        parse_mode="HTML", reply_markup=markup)

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") in ("msg_to_user", "broadcast"))
def handle_owner_message(message):
    uid   = message.from_user.id
    state = user_sessions.pop(uid, {})
    if state.get("state") == "broadcast":
        resellers = load_resellers()
        sent = 0
        for rid in resellers:
            try:
                bot.send_message(int(rid), f"📢 <b>Message de {BRAND}</b>\n\n{hx(message.text)}", parse_mode="HTML")
                add_conv_msg(int(rid), resellers[rid].get("alias", rid), message.text, "to")
                sent += 1
            except Exception: pass
        bot.send_message(uid, f"✅ Message envoyé à {sent} revendeur(s).", reply_markup=main_menu_keyboard(uid))
    elif state.get("state") == "msg_to_user":
        tid   = state["target_id"]
        alias = state["alias"]
        add_conv_msg(tid, alias, message.text, "to")
        try:
            bot.send_message(tid, f"💬 <b>Message de {BRAND}</b>\n\n{hx(message.text)}", parse_mode="HTML")
            bot.send_message(uid, f"✅ Message envoyé à <b>{hx(alias)}</b>.",
                             parse_mode="HTML", reply_markup=main_menu_keyboard(uid))
        except Exception as e:
            bot.send_message(uid, f"❌ Erreur : {e}")

@bot.callback_query_handler(func=lambda call: call.data == "menu_resellers" and is_admin(call.from_user.id))
def menu_resellers(call):
    resellers = load_resellers()
    lines = []
    for rid, info in resellers.items():
        remain = fmt_remain(int(rid))
        lines.append(
            f"• <b>{hx(info.get('alias', rid))}</b> | ID: <code>{rid}</code>\n"
            f"  Type: {info.get('type','?').upper()} | Max: {info.get('max_days',30)}j | Restant: {remain}"
        )
    txt = "\n\n".join(lines) if lines else "Aucun revendeur."
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour", callback_data="action_home"))
    text = (
        f"<b>👥 Revendeurs de {BRAND}</b>\n\n{txt}\n\n"
        f"➕ /addreseller &lt;ID&gt; &lt;Alias&gt; &lt;trial|premium&gt; [jours]\n"
        f"➖ /delreseller &lt;ID&gt;\n"
        f"✏️ /setmaxdays &lt;ID&gt; &lt;jours&gt;\n"
        f"⏳ /extendtrial &lt;ID&gt; &lt;heures&gt;"
    )
    _show_submenu(call, text, markup)

@bot.message_handler(commands=['addreseller'])
def cmd_add_reseller(message):
    uid = message.from_user.id
    if not is_admin(uid):
        bot.reply_to(message, f"🔒 Réservé au propriétaire {BRAND}."); return
    args = message.text.split()[1:]
    if len(args) < 3:
        bot.reply_to(message,
            f"Usage :\n/addreseller &lt;ID&gt; &lt;Alias&gt; &lt;trial|premium&gt; [max_jours]\n\n"
            f"Exemple :\n/addreseller 123456789 Jean premium 30",
            parse_mode="HTML"); return
    try:
        new_id   = int(args[0])
        alias    = re.sub(r'[<>&]', '', args[1]) or str(new_id)
        rtype    = args[2].lower()
        if rtype not in ["trial", "premium"]:
            bot.reply_to(message, "❌ Type invalide. Utilisez trial ou premium."); return
        max_days = int(args[3]) if len(args) >= 4 else (1 if rtype == "trial" else 30)
        now_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resellers = load_resellers()
        resellers[str(new_id)] = {
            "alias": alias, "type": rtype,
            "added_date":     datetime.now().strftime("%Y-%m-%d"),
            "added_datetime": now_str,
            "max_days":       max_days,
            "max_hours":      48 if rtype == "trial" else max_days * 24
        }
        save_resellers(resellers)
        bot.reply_to(message,
            f"✅ Revendeur ajouté à {BRAND}\n\n"
            f"• Alias : {alias}\n• ID : {new_id}\n"
            f"• Type : {rtype.upper()}\n• Max jours : {max_days}j")
        try:
            if rtype == "premium":
                welcome = (
                    f"🎉 Félicitations <b>{alias}</b> !\n\n"
                    f"Vous avez été ajouté en tant que revendeur <b>PREMIUM</b> par {BRAND}.\n\n"
                    f"📅 Vous pouvez créer des comptes jusqu'à {max_days} jours.\n"
                    f"⏳ Votre abonnement est valable {max_days} jours.\n\n"
                    f"Tapez /start pour commencer. 🚀"
                )
            else:
                welcome = (
                    f"✅ Bienvenue <b>{alias}</b> !\n\n"
                    f"Vous avez été ajouté en tant que revendeur <b>TRIAL</b> par {BRAND}.\n\n"
                    f"⏳ Votre accès est valable 48 heures.\n"
                    f"📅 Vous pouvez créer des comptes de 1 jour maximum.\n\n"
                    f"Tapez /start pour commencer."
                )
            bot.send_message(new_id, welcome, parse_mode="HTML")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "❌ ID invalide.")

@bot.message_handler(commands=['delreseller'])
def cmd_del_reseller(message):
    uid = message.from_user.id
    if not is_admin(uid): return
    args = message.text.split()[1:]
    if not args:
        bot.reply_to(message, "Usage : /delreseller &lt;ID&gt;", parse_mode="HTML"); return
    try:
        rem_id    = int(args[0])
        resellers = load_resellers()
        if str(rem_id) not in resellers:
            bot.reply_to(message, f"❌ {rem_id} n'est pas revendeur."); return
        alias = resellers[str(rem_id)].get("alias", str(rem_id))
        del resellers[str(rem_id)]
        save_resellers(resellers)
        bot.reply_to(message, f"✅ Revendeur {alias} retiré.")
        try:
            bot.send_message(rem_id,
                f"⛔ <b>Accès retiré à {BRAND}</b>\n\n"
                f"Votre accès au bot a été révoqué par {BRAND}.\n"
                f"Contactez {BRAND} pour plus d'informations.",
                parse_mode="HTML")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "❌ ID invalide.")

@bot.message_handler(commands=['setmaxdays'])
def cmd_set_max_days(message):
    uid = message.from_user.id
    if not is_admin(uid): return
    args = message.text.split()[1:]
    if len(args) < 2:
        bot.reply_to(message, "Usage : /setmaxdays &lt;ID&gt; &lt;jours&gt;", parse_mode="HTML"); return
    try:
        tid  = str(int(args[0]))
        days = int(args[1])
        resellers = load_resellers()
        if tid not in resellers:
            bot.reply_to(message, "❌ Revendeur introuvable."); return
        resellers[tid]["max_days"]  = days
        resellers[tid]["max_hours"] = days * 24
        save_resellers(resellers)
        alias = resellers[tid].get("alias", tid)
        bot.reply_to(message, f"✅ Max jours pour {alias} mis à jour : {days}j")
    except ValueError:
        bot.reply_to(message, "❌ Valeur invalide.")

@bot.message_handler(commands=['extendtrial'])
def cmd_extend_trial(message):
    uid = message.from_user.id
    if not is_admin(uid): return
    args = message.text.split()[1:]
    if len(args) < 2:
        bot.reply_to(message, "Usage : /extendtrial &lt;ID&gt; &lt;heures&gt;\nEx: /extendtrial 123456 72", parse_mode="HTML"); return
    try:
        tid   = str(int(args[0]))
        hours = int(args[1])
        resellers = load_resellers()
        if tid not in resellers:
            bot.reply_to(message, "❌ Revendeur introuvable."); return
        old_hours = resellers[tid].get("max_hours", 48)
        resellers[tid]["max_hours"] = old_hours + hours
        if resellers[tid]["type"] == "premium":
            resellers[tid]["max_days"] = int((old_hours + hours) / 24)
        save_resellers(resellers)
        alias = resellers[tid].get("alias", tid)
        new_h = resellers[tid]["max_hours"]
        new_d = int(new_h / 24)
        bot.reply_to(message,
            f"✅ Abonnement de {alias} prolongé de {hours}h.\n"
            f"Nouveau total : {new_d}j {new_h%24}h")
        try:
            bot.send_message(int(tid),
                f"🎉 <b>Abonnement prolongé sur {BRAND}</b>\n\n"
                f"Votre abonnement a été prolongé de {hours} heure(s) par {BRAND}.\n"
                f"Nouveau temps restant : {fmt_remain(int(tid))}.",
                parse_mode="HTML")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "❌ Valeur invalide.")

@bot.message_handler(commands=['setbrand'])
def cmd_setbrand(message):
    global BRAND
    uid = message.from_user.id
    if not is_admin(uid):
        bot.reply_to(message, f"🔒 Réservé au propriétaire {BRAND}.")
        return
    args = message.text.split(None, 1)
    if len(args) < 2:
        bot.reply_to(message,
            "Usage : /setbrand &lt;nouveau nom&gt;\n\n"
            "Exemple : /setbrand 🔥THE_S-TUNNEL-PRO☄️📶🛜",
            parse_mode="HTML")
        return
    new_brand = clean_brand(args[1])
    cfg = load_config()
    cfg['brand'] = new_brand
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
    BRAND = new_brand
    bot.reply_to(message, f"✅ Marque mise à jour : <b>{new_brand}</b>", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "add_ssh")
def add_ssh_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur  {BRAND}\nContactez {BRAND}.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_ssh")]])); return
    bot.edit_message_text("⚙️ Module SSH — Création",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Étape 1/3 — Nom d'utilisateur SSH</b>\n\n"
        f"📌 Min 4 caractères, sans espaces\n"
        f"📌 Exemple : <code>jean01</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_get_user, uid)

def _ssh_get_user(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    valid, err = validate_username(user)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n👤 <b>Entrez le nom d'utilisateur SSH :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("ssh"))
        bot.register_next_step_handler(msg, _ssh_get_user, creator_id)
        return
    msg = bot.send_message(message.chat.id,
        f"🔑 <b>Étape 2/3 — Mot de passe</b>\n\n"
        f"📌 Minimum 4 caractères\n"
        f"📌 Exemple : <code>pass1234</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_get_pass, user, creator_id)

def _ssh_get_pass(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    password = message.text.strip()
    valid, err = validate_password(password)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n🔑 <b>Entrez le mot de passe :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("ssh"))
        bot.register_next_step_handler(msg, _ssh_get_pass, user, creator_id)
        return
    d_rem = days_remaining(creator_id)
    msg = bot.send_message(message.chat.id,
        f"⏳ <b>Étape 3/3 — Durée</b>\n\n"
        f"📌 Entrez un nombre entier\n"
        f"📌 Exemple : <code>7</code>\n"
        f"⏳ Maximum autorisé : <b>{d_rem} jour(s)</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_get_days, user, password, creator_id)

def _ssh_get_days(message, user, password, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("ssh"))
        bot.register_next_step_handler(msg, _ssh_get_days, user, password, creator_id)
        return
    days_int = int(message.text.strip())
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{hx(user)}</b>...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "SSH", user, days_int, password)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "renew_ssh")
def renew_ssh_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🔄 Module SSH — Renouvellement",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur SSH à renouveler :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_renew_get_days, uid)

def _ssh_renew_get_days(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    d_rem = days_remaining(creator_id)
    msg   = bot.send_message(message.chat.id,
        f"⏳ <b>Jours à ajouter pour</b> <code>{hx(user)}</code>\n\n"
        f"📌 Exemple : <code>7</code> | Max : <b>{d_rem}j</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_renew_execute, user, creator_id)

def _ssh_renew_execute(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("ssh"))
        bot.register_next_step_handler(msg, _ssh_renew_execute, user, creator_id)
        return
    days_int = int(message.text.strip())
    success, res = ssh_core.renew_ssh_account(user, days_int, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "del_ssh")
def del_ssh_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🗑️ Module SSH — Suppression",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur SSH à supprimer :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_del_execute, uid)

def _ssh_del_execute(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    success, res = ssh_core.delete_ssh_account(user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data in ("lock_ssh", "unlock_ssh"))
def lock_unlock_ssh_start(call):
    uid    = call.from_user.id
    if not has_access(uid): return
    action = call.data
    label  = "verrouiller" if action == "lock_ssh" else "déverrouiller"
    bot.edit_message_text(f"🔒 SSH — {label.capitalize()}",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Nom d'utilisateur SSH à {label} :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("ssh"))
    bot.register_next_step_handler(msg, _ssh_lock_execute, action, uid)

def _ssh_lock_execute(message, action, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    if action == "lock_ssh":
        success, res = ssh_core.lock_ssh_account(user, **own_kwargs(creator_id))
    else:
        success, res = ssh_core.unlock_ssh_account(user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "list_ssh")
def handle_list_ssh(call):
    uid = call.from_user.id
    if not has_access(uid): return
    if not is_admin(uid):
        _show_submenu(call,
            f"🔒 <b>Accès restreint ✋</b>\n\nSeul le propriétaire peut consulter la liste.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_ssh")]]))
        return
    users  = ssh_core.get_ssh_usernames()
    markup = InlineKeyboardMarkup(row_width=1)
    if users:
        for u in users:
            markup.add(InlineKeyboardButton(f"👤 {u}", callback_data=f"view_ssh_{u}"))
        text = f"📋 <b>LISTE DES COMPTES SSH sur {BRAND}</b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte SSH trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_ssh_"))
def view_ssh_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_ssh_"):]
    ip, domain, _ = get_system_info()
    password = "*******"
    expiry_raw = subprocess.getoutput(
        f"chage -l {shlex.quote(user)} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip()
    expiry = expiry_raw if expiry_raw else "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃       SSH/WS ACCOUNT\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{hx(user)}</code>\n"
        f"🔑 Password: <code>{hx(password)}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Domain: <code>{domain}</code>\n"
        f"🔌 Port SSH: <code>22</code>\n"
        f"🔌 Port WS: <code>80</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ {BRAND}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_ssh"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

@bot.callback_query_handler(func=lambda call: call.data == "menu_slowdns")
def menu_slowdns(call):
    uid = call.from_user.id
    if not has_access(uid): return
    _show_submenu(call,
        f"<b>🔥 Module SLOW DNS </b>\nChoisissez une action :",
        protocol_menu_keyboard("slowdns", uid))

@bot.callback_query_handler(func=lambda call: call.data == "add_slowdns")
def add_slowdns_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur {BRAND}.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_slowdns")]]))
        return
    bot.edit_message_text("⚙️ Module SLOW DNS — Création",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Étape 1/3 — Nom d'utilisateur SLOW DNS</b>\n\n"
        f"📌 Min 4 caractères, sans espaces\n"
        f"📌 Exemple : <code>jean01</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_get_user, uid)

def _slowdns_get_user(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    valid, err = validate_username(user)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n👤 <b>Entrez le nom d'utilisateur SLOW DNS :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("slowdns"))
        bot.register_next_step_handler(msg, _slowdns_get_user, creator_id)
        return
    msg = bot.send_message(message.chat.id,
        f"🔑 <b>Étape 2/3 — Mot de passe</b>\n\n"
        f"📌 Minimum 4 caractères\n"
        f"📌 Exemple : <code>pass1234</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_get_pass, user, creator_id)

def _slowdns_get_pass(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    password = message.text.strip()
    valid, err = validate_password(password)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n🔑 <b>Entrez le mot de passe :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("slowdns"))
        bot.register_next_step_handler(msg, _slowdns_get_pass, user, creator_id)
        return
    d_rem = days_remaining(creator_id)
    msg = bot.send_message(message.chat.id,
        f"⏳ <b>Étape 3/3 — Durée</b>\n\n"
        f"📌 Entrez un nombre entier\n"
        f"📌 Exemple : <code>7</code>\n"
        f"⏳ Maximum autorisé : <b>{d_rem} jour(s)</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_get_days, user, password, creator_id)

def _slowdns_get_days(message, user, password, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("slowdns"))
        bot.register_next_step_handler(msg, _slowdns_get_days, user, password, creator_id)
        return
    days_int = int(message.text.strip())
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{hx(user)}</b> (SLOW DNS)...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "SLOW DNS", user, days_int, password)
        ip, domain, slowdns_pub = get_system_info()
        ns_domain = subprocess.getoutput("cat /etc/slowdns/nsdomain 2>/dev/null || echo 'N/A'").strip()
        expiry = subprocess.getoutput(f"chage -l {shlex.quote(user)} | grep 'Account expires' | cut -d: -f2").strip()
        res = (
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃      SLOW DNS ACCOUNT \n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"👤 Username: <code>{hx(user)}</code>\n"
            f"🔑 Password: <code>{hx(password)}</code>\n"
            f"⏳ Expiry: <code>{expiry}</code>\n"
            f"🖥️ IP: <code>{ip}</code>\n"
            f"🌐 Domain: <code>{domain}</code>\n"
            f"📛 NS Domain: <code>{ns_domain}</code>\n"
            f"🔌 Port DNS: <code>53</code>\n"
            f"🔥 PUB Key:\n<code>{slowdns_pub}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ {BRAND}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "renew_slowdns")
def renew_slowdns_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🔄 SLOW DNS — Renouvellement",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur SLOW DNS à renouveler :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_renew_days, uid)

def _slowdns_renew_days(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    d_rem = days_remaining(creator_id)
    msg   = bot.send_message(message.chat.id,
        f"⏳ <b>Jours à ajouter pour</b> <code>{hx(user)}</code>\n\n"
        f"📌 Exemple : <code>7</code> | Max : <b>{d_rem}j</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_renew_exec, user, creator_id)

def _slowdns_renew_exec(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("slowdns"))
        bot.register_next_step_handler(msg, _slowdns_renew_exec, user, creator_id)
        return
    days_int = int(message.text.strip())
    success, res = ssh_core.renew_ssh_account(user, days_int, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "del_slowdns")
def del_slowdns_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🗑️ SLOW DNS — Suppression",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur SLOW DNS à supprimer :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("slowdns"))
    bot.register_next_step_handler(msg, _slowdns_del_exec, uid)

def _slowdns_del_exec(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    success, res = ssh_core.delete_ssh_account(user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "list_slowdns")
def list_slowdns(call):
    uid = call.from_user.id
    if not has_access(uid): return
    if not is_admin(uid):
        _show_submenu(call,
            f"🔒 <b>Accès restreint ✋</b>\n\nSeul le propriétaire peut consulter la liste.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_slowdns")]]))
        return
    users  = ssh_core.get_ssh_usernames()
    markup = InlineKeyboardMarkup(row_width=1)
    if users:
        for u in users:
            markup.add(InlineKeyboardButton(f"👤 {u}", callback_data=f"view_slowdns_{u}"))
        text = f"📋 <b>LISTE DES COMPTES SLOW DNS sur {BRAND}</b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte SLOW DNS trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_slowdns_"))
def view_slowdns_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_slowdns_"):]
    ip, domain, slowdns_pub = get_system_info()
    ns_domain = subprocess.getoutput("cat /etc/slowdns/nsdomain 2>/dev/null || echo 'N/A'").strip()
    password = "*******"
    expiry = subprocess.getoutput(
        f"chage -l {shlex.quote(user)} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip() or "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃      SLOW DNS ACCOUNT \n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{hx(user)}</code>\n"
        f"🔑 Password: <code>{hx(password)}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Domain: <code>{domain}</code>\n"
        f"📛 NS Domain: <code>{ns_domain}</code>\n"
        f"🔌 Port DNS: <code>53</code>\n"
        f"🔥 PUB Key:\n<code>{slowdns_pub}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ {BRAND}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_slowdns"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

@bot.callback_query_handler(func=lambda call: call.data == "menu_udpfast")
def menu_udpfast(call):
    uid = call.from_user.id
    if not has_access(uid): return
    _show_submenu(call,
        f"<b>🚀 Module UDP FAST </b>\nChoisissez une action :",
        protocol_menu_keyboard("udpfast", uid))

@bot.callback_query_handler(func=lambda call: call.data == "add_udpfast")
def add_udpfast_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur {BRAND}.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_udpfast")]]))
        return
    bot.edit_message_text("⚙️ Module UDP FAST — Création",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Étape 1/3 — Nom d'utilisateur UDP FAST</b>\n\n"
        f"📌 Min 4 caractères, sans espaces\n"
        f"📌 Exemple : <code>jean01</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_get_user, uid)

def _udpfast_get_user(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    valid, err = validate_username(user)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n👤 <b>Entrez le nom d'utilisateur UDP FAST :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("udpfast"))
        bot.register_next_step_handler(msg, _udpfast_get_user, creator_id)
        return
    msg = bot.send_message(message.chat.id,
        f"🔑 <b>Étape 2/3 — Mot de passe</b>\n\n"
        f"📌 Minimum 4 caractères\n"
        f"📌 Exemple : <code>pass1234</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_get_pass, user, creator_id)

def _udpfast_get_pass(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    password = message.text.strip()
    valid, err = validate_password(password)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n🔑 <b>Entrez le mot de passe :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("udpfast"))
        bot.register_next_step_handler(msg, _udpfast_get_pass, user, creator_id)
        return
    d_rem = days_remaining(creator_id)
    msg = bot.send_message(message.chat.id,
        f"⏳ <b>Étape 3/3 — Durée</b>\n\n"
        f"📌 Entrez un nombre entier\n"
        f"📌 Exemple : <code>7</code>\n"
        f"⏳ Maximum autorisé : <b>{d_rem} jour(s)</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_get_days, user, password, creator_id)

def _udpfast_get_days(message, user, password, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("udpfast"))
        bot.register_next_step_handler(msg, _udpfast_get_days, user, password, creator_id)
        return
    days_int = int(message.text.strip())
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{hx(user)}</b> (UDP FAST)...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "UDP FAST", user, days_int, password)
        ip, domain, _ = get_system_info()
        expiry = subprocess.getoutput(f"chage -l {shlex.quote(user)} | grep 'Account expires' | cut -d: -f2").strip()
        res = (
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃      UDP FAST ACCOUNT \n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"👤 Username: <code>{hx(user)}</code>\n"
            f"🔑 Password: <code>{hx(password)}</code>\n"
            f"⏳ Expiry: <code>{expiry}</code>\n"
            f"🖥️ IP: <code>{ip}</code>\n"
            f"🌐 Host: <code>{domain}</code>\n"
            f"🔌 Port UDP: <code>1-65535</code>\n"
            f"🚀 Config: <code>{ip}:1-65535@{user}:{hx(password)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ {BRAND}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "renew_udpfast")
def renew_udpfast_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🔄 UDP FAST — Renouvellement",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur UDP FAST à renouveler :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_renew_days, uid)

def _udpfast_renew_days(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    d_rem = days_remaining(creator_id)
    msg   = bot.send_message(message.chat.id,
        f"⏳ <b>Jours à ajouter pour</b> <code>{hx(user)}</code>\n\n"
        f"📌 Exemple : <code>7</code> | Max : <b>{d_rem}j</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_renew_exec, user, creator_id)

def _udpfast_renew_exec(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("udpfast"))
        bot.register_next_step_handler(msg, _udpfast_renew_exec, user, creator_id)
        return
    days_int = int(message.text.strip())
    success, res = ssh_core.renew_ssh_account(user, days_int, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "del_udpfast")
def del_udpfast_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🗑️ UDP FAST — Suppression",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur UDP FAST à supprimer :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("udpfast"))
    bot.register_next_step_handler(msg, _udpfast_del_exec, uid)

def _udpfast_del_exec(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    success, res = ssh_core.delete_ssh_account(user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "list_udpfast")
def list_udpfast(call):
    uid = call.from_user.id
    if not has_access(uid): return
    if not is_admin(uid):
        _show_submenu(call,
            f"🔒 <b>Accès restreint ✋</b>\n\nSeul le propriétaire peut consulter la liste.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_udpfast")]]))
        return
    users  = ssh_core.get_ssh_usernames()
    markup = InlineKeyboardMarkup(row_width=1)
    if users:
        for u in users:
            markup.add(InlineKeyboardButton(f"👤 {u}", callback_data=f"view_udpfast_{u}"))
        text = f"📋 <b>LISTE DES COMPTES UDP FAST sur {BRAND}</b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte UDP FAST trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_udpfast_"))
def view_udpfast_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_udpfast_"):]
    ip, domain, _ = get_system_info()
    password = "*******"
    expiry = subprocess.getoutput(
        f"chage -l {shlex.quote(user)} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip() or "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃      UDP FAST ACCOUNT \n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{hx(user)}</code>\n"
        f"🔑 Password: <code>{hx(password)}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Host: <code>{domain}</code>\n"
        f"🔌 Port UDP: <code>1-65535</code>\n"
        f"🚀 Config: <code>{ip}:1-65535@{user}:{hx(password)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ {BRAND}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_udpfast"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

@bot.callback_query_handler(func=lambda call: call.data in ("add_vless","add_vmess","add_trojan","add_socks"))
def add_xray_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur {BRAND}.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data=f"menu_{proto}")]]))
        return
    bot.edit_message_text(f"⚙️ Module {proto.upper()} — Création",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Nom d'utilisateur {proto.upper()}</b>\n\n"
        f"📌 Min 4 caractères, sans espaces\n"
        f"📌 Exemple : <code>jean01</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(proto))
    bot.register_next_step_handler(msg, _xray_get_user, proto, uid)

def _xray_get_user(message, proto, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    valid, err = validate_username(user)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n👤 <b>Entrez le nom d'utilisateur {proto.upper()} :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(proto))
        bot.register_next_step_handler(msg, _xray_get_user, proto, creator_id)
        return
    d_rem = days_remaining(creator_id)
    msg = bot.send_message(message.chat.id,
        f"⏳ <b>Durée en jours</b>\n\n"
        f"📌 Exemple : <code>7</code>\n"
        f"⏳ Maximum autorisé : <b>{d_rem} jour(s)</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(proto))
    bot.register_next_step_handler(msg, _xray_get_days, user, proto, creator_id)

def _xray_get_days(message, user, proto, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(proto))
        bot.register_next_step_handler(msg, _xray_get_days, user, proto, creator_id)
        return
    days_int = int(message.text.strip())
    bot.send_message(message.chat.id, f"⚙️ Injection de <b>{hx(user)}</b> ({proto.upper()})...", parse_mode="HTML")
    success, res = xray_core.create_xray_account(proto, user, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, proto.upper(), user, days_int)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data in ("renew_vless","renew_vmess","renew_trojan","renew_socks"))
def renew_xray_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    bot.edit_message_text(f"🔄 {proto.upper()} — Renouvellement",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Nom d'utilisateur {proto.upper()} à renouveler :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(proto))
    bot.register_next_step_handler(msg, _xray_renew_get_days, proto, uid)

def _xray_renew_get_days(message, proto, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    d_rem = days_remaining(creator_id)
    msg   = bot.send_message(message.chat.id,
        f"⏳ <b>Jours à ajouter pour</b> <code>{hx(user)}</code>\n\n"
        f"📌 Exemple : <code>7</code> | Max : <b>{d_rem}j</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(proto))
    bot.register_next_step_handler(msg, _xray_renew_execute, proto, user, creator_id)

def _xray_renew_execute(message, proto, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(proto))
        bot.register_next_step_handler(msg, _xray_renew_execute, proto, user, creator_id)
        return
    days_int = int(message.text.strip())
    success, res = xray_core.renew_xray_account(proto, user, days_int, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data in ("del_vless","del_vmess","del_trojan","del_socks"))
def del_xray_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    bot.edit_message_text(f"🗑️ {proto.upper()} — Suppression",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Nom d'utilisateur {proto.upper()} à supprimer :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(proto))
    bot.register_next_step_handler(msg, _xray_del_execute, proto, uid)

def _xray_del_execute(message, proto, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    success, res = xray_core.delete_xray_account(proto, user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data in ("list_vless","list_vmess","list_trojan","list_socks"))
def handle_list_xray(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    if not is_admin(uid):
        proto = call.data.split("_", 1)[1]
        _show_submenu(call,
            f"🔒 <b>Accès restreint ✋🛑</b>\n\nSeul le propriétaire peut consulter la liste.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data=f"menu_{proto}")]]))
        return
    proto  = call.data.split("_", 1)[1]
    users  = xray_core.get_xray_usernames(proto)
    markup = InlineKeyboardMarkup(row_width=1)
    if users:
        for u in users:
            markup.add(InlineKeyboardButton(f"👤 {u}", callback_data=f"view_{proto}_{u}"))
        text = f"📋 <b>LISTE DES COMPTES {proto.upper()} </b>\nSélectionnez un compte :"
    else:
        text = f"📋 Aucun compte {proto.upper()} trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: any(
    call.data.startswith(f"view_{p}_") for p in ["vless","vmess","trojan","socks"]))
def view_xray_account(call):
    if not is_admin(call.from_user.id): return
    parts = call.data.split("_", 2)
    proto, user = parts[1], parts[2]
    ok, details = xray_core.get_xray_account_details(proto, user)
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data=f"list_{proto}"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_zivpn")
def add_zivpn_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("⚙️ Module ZIVPN — Création",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        f"👤 <b>Étape 1/3 — Nom d'utilisateur ZIVPN</b>\n\n"
        f"📌 Min 4 caractères, sans espaces\n"
        f"📌 Exemple : <code>jean01</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_get_user, uid)

def _zivpn_get_user(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    valid, err = validate_username(user)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n👤 <b>Entrez le nom d'utilisateur ZIVPN :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("zivpn"))
        bot.register_next_step_handler(msg, _zivpn_get_user, creator_id)
        return
    msg = bot.send_message(message.chat.id,
        f"🔑 <b>Étape 2/3 — Mot de passe</b>\n\n"
        f"📌 Minimum 4 caractères\n"
        f"📌 Exemple : <code>pass1234</code>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_get_pass, user, creator_id)

def _zivpn_get_pass(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    password = message.text.strip()
    valid, err = validate_password(password)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n🔑 <b>Entrez le mot de passe :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("zivpn"))
        bot.register_next_step_handler(msg, _zivpn_get_pass, user, creator_id)
        return
    d_rem = days_remaining(creator_id)
    msg = bot.send_message(message.chat.id,
        f"⏳ <b>Étape 3/3 — Durée</b>\n\n"
        f"📌 Entrez un nombre entier\n"
        f"📌 Exemple : <code>7</code>\n"
        f"⏳ Maximum autorisé : <b>{d_rem} jour(s)</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_get_days, user, password, creator_id)

def _zivpn_get_days(message, user, password, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("zivpn"))
        bot.register_next_step_handler(msg, _zivpn_get_days, user, password, creator_id)
        return
    days_int = int(message.text.strip())
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{hx(user)}</b> (ZIVPN)...", parse_mode="HTML")
    success, res = zivpn_core.create_zivpn_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "ZIVPN", user, days_int, password)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "renew_zivpn")
def renew_zivpn_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🔄 ZIVPN — Renouvellement",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur ZIVPN à renouveler :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_renew_get_days, uid)

def _zivpn_renew_get_days(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user  = message.text.strip()
    d_rem = days_remaining(creator_id)
    msg   = bot.send_message(message.chat.id,
        f"⏳ <b>Jours à ajouter pour</b> <code>{hx(user)}</code>\n\n"
        f"📌 Exemple : <code>7</code> | Max : <b>{d_rem}j</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_renew_execute, user, creator_id)

def _zivpn_renew_execute(message, user, creator_id):
    if message.text and message.text.startswith("/"):
        return
    d_rem = days_remaining(creator_id)
    valid, err = validate_days(message.text.strip(), d_rem if not is_admin(creator_id) else 99999)
    if not valid:
        msg = bot.send_message(message.chat.id,
            f"{err}\n\n⏳ <b>Entrez la durée en jours :</b>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard("zivpn"))
        bot.register_next_step_handler(msg, _zivpn_renew_execute, user, creator_id)
        return
    days_int = int(message.text.strip())
    success, res = zivpn_core.renew_zivpn_account(user, days_int, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "del_zivpn")
def del_zivpn_start(call):
    uid = call.from_user.id
    if not has_access(uid): return
    bot.edit_message_text("🗑️ ZIVPN — Suppression",
                          chat_id=call.message.chat.id, message_id=call.message.message_id)
    msg = bot.send_message(call.message.chat.id,
        "👤 <b>Nom d'utilisateur ZIVPN à supprimer :</b>",
        parse_mode="HTML",
        reply_markup=cancel_keyboard("zivpn"))
    bot.register_next_step_handler(msg, _zivpn_del_execute, uid)

def _zivpn_del_execute(message, creator_id):
    if message.text and message.text.startswith("/"):
        return
    user = message.text.strip()
    success, res = zivpn_core.delete_zivpn_account(user, **own_kwargs(creator_id))
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

@bot.callback_query_handler(func=lambda call: call.data == "list_zivpn")
def handle_list_zivpn(call):
    uid = call.from_user.id
    if not has_access(uid): return
    if not is_admin(uid):
        _show_submenu(call,
            f"🔒 <b>Accès restreint ✋🛑</b>\n\nSeul le propriétaire peut consulter la liste.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_zivpn")]]))
        return
    users  = zivpn_core.get_zivpn_usernames()
    markup = InlineKeyboardMarkup(row_width=1)
    if users:
        for u in users:
            markup.add(InlineKeyboardButton(f"👤 {u}", callback_data=f"view_zivpn_{u}"))
        text = f"📋 <b>LISTE DES COMPTES ZIVPN </b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte ZIVPN trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_zivpn_"))
def view_zivpn_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_zivpn_"):]
    ok, details = zivpn_core.get_zivpn_account_details(user)
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_zivpn"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

@bot.callback_query_handler(func=lambda call: call.data == "menu_status")
def handle_status(call):
    if not has_access(call.from_user.id): return
    status_text = system_core.get_vps_status()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, status_text, markup)

@bot.callback_query_handler(func=lambda call: call.data == "menu_log")
def handle_clean_logs(call):
    if not has_access(call.from_user.id): return
    result = system_core.clean_system_logs()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, result, markup)

@bot.callback_query_handler(func=lambda call: call.data == "action_reboot")
def handle_reboot(call):
    if not admin_core.is_super_admin(call.from_user.id): return
    bot.answer_callback_query(call.id, "♻️ Reboot en cours...")
    bot.send_message(call.message.chat.id, "♻️ <b>Reboot VPS lancé.</b>", parse_mode="HTML")
    subprocess.run(["reboot"])

@bot.callback_query_handler(func=lambda call: call.data == "menu_admins")
def handle_menu_admins(call):
    if not is_admin(call.from_user.id): return
    is_super = admin_core.is_super_admin(call.from_user.id)
    markup   = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📋 Liste des admins",       callback_data="list_admins"),
        InlineKeyboardButton("➕ Ajouter un admin",        callback_data="req_add_admin"),
    )
    if is_super:
        markup.add(InlineKeyboardButton("👑 Promouvoir admin en suprême", callback_data="req_promote_admin"))
    markup.add(
        InlineKeyboardButton("❌ Supprimer un admin",      callback_data="req_del_admin"),
        InlineKeyboardButton("🔙 Retour Accueil",         callback_data="action_home")
    )
    msg = admin_core.list_admins()
    _show_submenu(call, msg, markup)

@bot.callback_query_handler(func=lambda call: call.data == "list_admins")
def handle_list_admins(call):
    if not is_admin(call.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour Admins", callback_data="menu_admins"))
    _show_submenu(call, admin_core.list_admins(), markup)

@bot.callback_query_handler(func=lambda call: call.data == "req_add_admin")
def req_add_admin(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id, "👤 Entrez l'ID Telegram du nouvel administrateur :")
    bot.register_next_step_handler(msg, _process_add_admin, call.from_user.id)

def _process_add_admin(message, requester_id):
    target_id = message.text.strip()
    if not target_id.isdigit():
        bot.send_message(message.chat.id, "❌ L'ID doit être un nombre entier.")
        return
    if admin_core.is_super_admin(requester_id):
        success, res = admin_core.approve_new_admin(target_id)
        status = "✅ " if success else "❌ "
        bot.send_message(message.chat.id, f"{status}{res}", reply_markup=main_menu_keyboard(requester_id))
    else:
        bot.send_message(message.chat.id, "⏳ <b>Demande envoyée au Super Admin.</b>", parse_mode="HTML")
        super_admin_id = admin_core.get_config().get('super_admin')
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Approuver", callback_data=f"adm:approve:{target_id}:{requester_id}"),
            InlineKeyboardButton("❌ Refuser",   callback_data=f"adm:reject:{target_id}:{requester_id}")
        )
        bot.send_message(super_admin_id,
            f"⚠️ <b>REQUÊTE ADMIN</b>\n\nL'admin <code>{requester_id}</code> veut ajouter <code>{target_id}</code>.",
            parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm:approve:") or call.data.startswith("adm:reject:"))
def handle_admin_approval(call):
    if not admin_core.is_super_admin(call.from_user.id): return
    parts  = call.data.split(":")
    action, target_id, requester_id = parts[1], parts[2], parts[3]
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    if action == "approve":
        success, res = admin_core.approve_new_admin(target_id)
        bot.send_message(call.message.chat.id, f"✅ Approuvé <code>{target_id}</code>.", parse_mode="HTML")
        try: bot.send_message(int(requester_id), f"🎉 Demande pour <code>{target_id}</code> approuvée.", parse_mode="HTML")
        except Exception: pass
    else:
        bot.send_message(call.message.chat.id, f"❌ Refusé <code>{target_id}</code>.", parse_mode="HTML")
        try: bot.send_message(int(requester_id), f"🚫 Demande refusée pour <code>{target_id}</code>.", parse_mode="HTML")
        except Exception: pass

@bot.callback_query_handler(func=lambda call: call.data == "req_del_admin")
def req_del_admin(call):
    if not is_admin(call.from_user.id): return
    msg = bot.send_message(call.message.chat.id, "👤 Entrez l'ID de l'administrateur à révoquer :")
    bot.register_next_step_handler(msg, _process_del_admin, call.from_user.id)

def _process_del_admin(message, requester_id):
    target_id = message.text.strip()
    if not target_id.isdigit():
        bot.send_message(message.chat.id, "❌ L'ID doit être un nombre entier.")
        return
    if admin_core.is_super_admin(requester_id):
        success, res = admin_core.remove_admin(target_id)
        status = "✅ " if success else "❌ "
        bot.send_message(message.chat.id, f"{status}{res}", reply_markup=main_menu_keyboard(requester_id))
    else:
        bot.send_message(message.chat.id, "⏳ <b>Demande envoyée au Super Admin.</b>", parse_mode="HTML")
        super_admin_id = admin_core.get_config().get('super_admin')
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Révoquer", callback_data=f"adm:revoke:{target_id}:{requester_id}"),
            InlineKeyboardButton("❌ Annuler",  callback_data=f"adm:cancel:{target_id}:{requester_id}")
        )
        bot.send_message(super_admin_id,
            f"⚠️ <b>RÉVOCATION ADMIN</b>\n\n<code>{requester_id}</code> demande la révocation de <code>{target_id}</code>.",
            parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "req_promote_admin")
def req_promote_admin(call):
    if not admin_core.is_super_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Réservé aux Super Admins.")
        return
    msg = bot.send_message(call.message.chat.id, "👑 ID de l'admin à promouvoir en Super Admin :")
    bot.register_next_step_handler(msg, _process_promote_admin)

def _process_promote_admin(message):
    target_id = message.text.strip()
    if not target_id.isdigit():
        bot.send_message(message.chat.id, "❌ L'ID doit être un nombre entier.")
        return
    success, res = admin_core.promote_admin_to_supreme(int(target_id))
    status = "✅ " if success else "❌ "
    bot.send_message(message.chat.id, f"{status}{res}", parse_mode="HTML",
                     reply_markup=main_menu_keyboard(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm:revoke:") or call.data.startswith("adm:cancel:"))
def handle_revoke_approval(call):
    if not admin_core.is_super_admin(call.from_user.id): return
    parts  = call.data.split(":")
    action, target_id, requester_id = parts[1], parts[2], parts[3]
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    if action == "revoke":
        success, res = admin_core.remove_admin(target_id)
        bot.send_message(call.message.chat.id, f"✅ Admin <code>{target_id}</code> révoqué.", parse_mode="HTML")
        try: bot.send_message(int(requester_id), f"✅ Révocation de <code>{target_id}</code> effectuée.", parse_mode="HTML")
        except Exception: pass
    else:
        bot.send_message(call.message.chat.id, f"ℹ️ Révocation annulée pour <code>{target_id}</code>.", parse_mode="HTML")


def _expiry_label(expiry):
    try:
        d = datetime.strptime(str(expiry), "%Y-%m-%d").date()
    except ValueError:
        return "date inconnue"
    left = (d - datetime.now().date()).days
    return f"expire le {d.isoformat()} ({left} j)" if left > 0 else f"expiré le {d.isoformat()}"

@bot.callback_query_handler(func=lambda call: call.data == "my_accounts")
def my_accounts(call):
    """Liste des comptes créés par la personne qui clique (un revendeur ne voit que les siens)."""
    uid = call.from_user.id
    if not has_access(uid): return
    sections = []
    ssh = ssh_core.list_owned(uid)
    if ssh:
        sections.append(("🔑 <b>SSH / SlowDNS / UDP Fast</b>",
                         [f"• <code>{hx(u)}</code> — {_expiry_label(e)}{' 🔒 verrouillé' if lk else ''}" for u, e, lk in ssh]))
    for proto, items in xray_core.list_owned(uid).items():
        sections.append((f"🔰 <b>{proto.upper()}</b>", [f"• <code>{hx(u)}</code> — {_expiry_label(e)}" for u, e in items]))
    zi = zivpn_core.list_owned(uid)
    if zi:
        sections.append(("📱 <b>ZIVPN</b>", [f"• <code>{hx(u)}</code> — {_expiry_label(e)}" for u, e in zi]))
    total = sum(len(lines) for _, lines in sections)
    if not total:
        pages = ["📂 <b>Mes comptes</b>\n\nVous n'avez créé aucun compte pour le moment."]
    else:
        limit, head = 3500, "📂 <b>Mes comptes</b>"
        pages, current = [], f"{head} — {total} compte(s)\n"
        for title, lines in sections:
            if len(current) + len(title) + 2 > limit:
                pages.append(current); current = f"{head} (suite)\n"
            current += f"\n{title}\n"
            for line in lines:
                if len(current) + len(line) + 1 > limit:
                    pages.append(current); current = f"{head} (suite)\n\n{title} (suite)\n"
                current += line + "\n"
        pages.append(current)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    for i, page in enumerate(pages):
        last = (i == len(pages) - 1)
        if i == 0:
            _show_submenu(call, page.rstrip(), markup if last else None)
        else:
            bot.send_message(call.message.chat.id, page.rstrip(), parse_mode="HTML", reply_markup=markup if last else None)

if __name__ == "__main__":
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(RESELLERS_FILE): save_resellers({})
    if not os.path.exists(CONVS_FILE):    save_convs({})
    if not os.path.exists(VISITORS_FILE): save_visitors({})
    bot.infinity_polling()
EOF

# 3. Écriture des modules du bot
echo "🧩 Création des modules du bot..."
cat << 'MODULE_EOF' > "$CONFIG_DIR/modules/__init__.py"
# Modules du bot The_S
MODULE_EOF

cat << 'MODULE_EOF' > "$CONFIG_DIR/modules/common.py"
"""Fonctions communes aux modules du bot The_S."""
import contextlib
import fcntl
import html
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import date, timedelta

CONFIG_DIR       = os.environ.get("THE_S_BOT_CONFIG_DIR", "/etc/the_s_bot")
CONFIG_FILE      = os.path.join(CONFIG_DIR, "config.json")
XRAY_CONFIG      = os.environ.get("THE_S_BOT_XRAY_CONFIG", "/etc/xray/config.json")
XRAY_DOMAIN_FILE = os.environ.get("THE_S_BOT_XRAY_DOMAIN", "/etc/xray/domain")
ZIVPN_CONFIG     = os.environ.get("THE_S_BOT_ZIVPN_CONFIG", "/etc/zivpn/config.json")

LINE   = "━━━━━━━━━━━━━━━━━━━━━━━━"
DEFAULT_BRAND = "🔥THE_S-TUNNEL-PRO☄️📶🛜"

# ── Verrou (thread + processus) ────────────────────────────────────────────
_rlock   = threading.RLock()
_depth   = 0
_lock_fd = None


@contextlib.contextmanager
def state_lock():
    """Verrou réentrant : protège les fichiers d'état contre les accès simultanés
    (bot + tâche cron de nettoyage)."""
    global _depth, _lock_fd
    with _rlock:
        if _depth == 0:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            _lock_fd = os.open(os.path.join(CONFIG_DIR, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(_lock_fd, fcntl.LOCK_EX)
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if _depth == 0:
                fcntl.flock(_lock_fd, fcntl.LOCK_UN)
                os.close(_lock_fd)
                _lock_fd = None


# ── JSON ───────────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        with contextlib.suppress(OSError):
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
        return default


def save_json(path, data, mode=0o600, like=None):
    """Écriture atomique. `like` : copie droits et propriétaire d'un fichier existant."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    uid = gid = None
    ref = like if like and os.path.exists(like) else (path if os.path.exists(path) else None)
    if ref:
        st = os.stat(ref)
        mode, uid, gid = st.st_mode & 0o777, st.st_uid, st.st_gid
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, mode)
        if uid is not None and os.geteuid() == 0:
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ── Commandes ──────────────────────────────────────────────────────────────
def run(cmd, input_text=None, timeout=30):
    """Exécute une commande SANS shell. Retourne (code, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"commande introuvable : {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "délai dépassé"


# ── Infos serveur ──────────────────────────────────────────────────────────
_ip_cache = [0.0, ""]


def get_ip():
    if _ip_cache[1] and time.time() - _ip_cache[0] < 600:
        return _ip_cache[1]
    rc, out, _ = run(["curl", "-s", "-m", "5", "ifconfig.me"], timeout=8)
    if rc == 0 and re.match(r"^[0-9a-fA-F:.]+$", out or ""):
        _ip_cache[0], _ip_cache[1] = time.time(), out
        return out
    return _ip_cache[1] or "N/A"


def get_domain():
    try:
        with open(XRAY_DOMAIN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "N/A"
    except OSError:
        return "N/A"


def get_host():
    d = get_domain()
    return d if d != "N/A" else get_ip()


def bot_config():
    return load_json(CONFIG_FILE, {})


# ── Dates ──────────────────────────────────────────────────────────────────
def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def calc_expiry(days, current=None):
    """Nouvelle date d'expiration : on part du plus tard entre aujourd'hui et l'expiration actuelle."""
    base = date.today()
    if current and current > base:
        base = current
    return base + timedelta(days=int(days))


def is_expired(value):
    d = parse_date(value)
    return d is not None and d <= date.today()


# ── Mise en forme HTML (Telegram) ──────────────────────────────────────────
def owned(info, requester_id, restrict):
    """Propriété d'un compte. Avec restrict=True (revendeur), seul le créateur du compte peut agir ;
    un compte sans créateur connu n'appartient à personne. Sans restrict (admins), tout est permis."""
    if not restrict:
        return True
    owner = (info or {}).get("created_by")
    return owner is not None and str(owner) == str(requester_id)


def not_yours(user, extra=""):
    # Même message que « introuvable » : un revendeur ne peut pas deviner les comptes des autres.
    return False, (f"❌ Compte <b>{esc(user)}</b> introuvable parmi vos comptes{extra}.\n"
                   "📂 Le bouton « Mes comptes » du menu principal affiche la liste de vos comptes.")


def esc(value):
    return html.escape(str(value), quote=False)


def kv(icon, label, value):
    return f"{icon} {label}: <code>{esc(value)}</code>"


def brand():
    """Marque courante (celle définie avec /setbrand), sans caractères HTML."""
    value = re.sub(r"<[^>]*>", "", str(bot_config().get("brand") or ""))
    value = re.sub(r"[<>&]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or DEFAULT_BRAND


def card(title, lines):
    head = ["┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓", f"┃      {title}", "┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛"]
    return "\n".join(head + list(lines) + [LINE, f"✅ {brand()}", LINE])
MODULE_EOF

cat << 'MODULE_EOF' > "$CONFIG_DIR/modules/system_core.py"
"""État du VPS et nettoyage des logs."""
import os
import platform
import re
import shutil

from modules import common as c

SERVICES = [("SSH", ("ssh", "sshd")), ("Xray", ("xray",)), ("ZIVPN", ("zivpn",)),
            ("Nginx", ("nginx",)), ("The_S Bot", ("the-s-bot",))]


def _human(n):
    n = float(n)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def _uptime():
    try:
        with open("/proc/uptime") as f:
            s = int(float(f.read().split()[0]))
    except OSError:
        return "N/A"
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    return f"{d}j {h}h {r // 60}min"


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return info


def _os_name():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _service_state(names):
    for name in names:
        rc, out, _ = c.run(["systemctl", "show", "-p", "LoadState", "--value", f"{name}.service"], timeout=10)
        if rc == 0 and out and out != "not-found":
            rc, out, _ = c.run(["systemctl", "is-active", f"{name}.service"], timeout=10)
            return out == "active", out or "inconnu"
    return None, "non installé"


def _count_accounts():
    d = c.CONFIG_DIR
    ssh   = len(c.load_json(os.path.join(d, "ssh_accounts.json"), {}))
    zivpn = len(c.load_json(os.path.join(d, "zivpn_accounts.json"), {}))
    xray  = c.load_json(os.path.join(d, "xray_accounts.json"), {})
    return ssh, zivpn, {p: len(v) for p, v in xray.items()}


def get_vps_status():
    mem = _meminfo()
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    used = total - avail
    du = shutil.disk_usage("/")
    try:
        l1, l5, l15 = os.getloadavg()
        load = f"{l1:.2f} / {l5:.2f} / {l15:.2f}"
    except OSError:
        load = "N/A"
    lines = ["📊 <b>ÉTAT DU VPS</b>", c.LINE,
             c.kv("🖥️", "Hôte", platform.node()),
             c.kv("🐧", "Système", _os_name()),
             c.kv("🌍", "IP", c.get_ip()),
             c.kv("⏱️", "Uptime", _uptime()),
             c.kv("⚙️", "CPU (cœurs)", os.cpu_count() or "N/A"),
             c.kv("📈", "Charge 1/5/15 min", load)]
    if total:
        lines.append(c.kv("🧠", "RAM", f"{_human(used)} / {_human(total)} ({used * 100 // total}%)"))
    lines.append(c.kv("💾", "Disque", f"{_human(du.used)} / {_human(du.total)} ({du.used * 100 // du.total}%)"))
    lines += [c.LINE, "🔧 <b>Services</b>"]
    for label, names in SERVICES:
        ok, state = _service_state(names)
        if ok is None:
            continue
        lines.append(f"{'🟢' if ok else '🔴'} {label} : <code>{c.esc(state)}</code>")
    ssh, zivpn, xray = _count_accounts()
    lines += [c.LINE, "👥 <b>Comptes gérés</b>", f"SSH : <b>{ssh}</b> | ZIVPN : <b>{zivpn}</b>"]
    if xray:
        lines.append(" | ".join(f"{p.upper()} : <b>{n}</b>" for p, n in sorted(xray.items())))
    return "\n".join(lines)


def _dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clean_system_logs():
    before = _dir_size("/var/log")
    c.run(["journalctl", "--vacuum-time=2d"], timeout=60)
    c.run(["journalctl", "--vacuum-size=50M"], timeout=60)
    for root, dirs, files in os.walk("/var/log"):
        dirs[:] = [d for d in dirs if d != "journal"]
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    continue
                if name.endswith((".gz", ".xz", ".bz2", ".zst", ".old")) or re.search(r"\.\d+$", name):
                    os.unlink(path)
                elif name.endswith(".log"):
                    open(path, "w").close()
            except OSError:
                pass
    c.run(["apt-get", "clean"], timeout=60)
    after = _dir_size("/var/log")
    freed = max(0, before - after)
    return (f"🧹 <b>Nettoyage terminé</b>\n\n💾 Libéré : <code>{_human(freed)}</code>\n"
            f"📁 /var/log : <code>{_human(after)}</code>")
MODULE_EOF

cat << 'MODULE_EOF' > "$CONFIG_DIR/modules/ssh_core.py"
"""Gestion des comptes SSH (SSH, SlowDNS et UDP Fast utilisent ces comptes).

Sécurité : seuls les comptes créés par le bot (registre ssh_accounts.json) peuvent être
renouvelés, verrouillés ou supprimés. Impossible de toucher à root ou à un compte système.
"""
import os
import pwd
import re

from modules import common as c

REG      = os.path.join(c.CONFIG_DIR, "ssh_accounts.json")
NAME_RE  = re.compile(r"^[a-z][a-z0-9_-]{3,31}$")
SSH_PORT = "22"
WS_PORT  = "80"


def _reg()
