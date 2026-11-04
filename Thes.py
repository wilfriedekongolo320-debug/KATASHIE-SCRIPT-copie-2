import telebot
import subprocess
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import json
import os
import logging
import threading
import re
from datetime import datetime, timedelta
from modules import system_core, ssh_core, admin_core, xray_core, zivpn_core

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
CONFIG_FILE    = '/etc/pps_bot/config.json'
RESELLERS_FILE = '/etc/pps_bot/resellers.json'
CONVS_FILE     = '/etc/pps_bot/convs.json'
VISITORS_FILE  = '/etc/pps_bot/visitors.json'
MENU_IMAGE_URL = "https://github.com/ppstech237/pps-tg-bot/blob/main/pps.jpg?raw=true"

def load_config():
    if not os.path.exists(CONFIG_FILE): return None
    with open(CONFIG_FILE, 'r') as f: return json.load(f)

config = load_config()
if not config: exit(1)

bot         = telebot.TeleBot(config.get('bot_token'))
SUPER_ADMIN = int(config.get('super_admin'))
BRAND       = config.get('brand', '🜲 THE_S')

# ══════════════════════════════════════════
#  VALIDATION
# ══════════════════════════════════════════
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
            f"➡️ <code>{password}</code> ⬅️ ({len(password)} caractère(s))\n\n"
            f"📌 Minimum 4 caractères. Exemple : <code>pass1234</code>"
        )
    return True, ""

def validate_days(text, max_days):
    if not text.isdigit():
        return False, (
            f"❌ Valeur invalide : <code>{text}</code>\n\n"
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

# ══════════════════════════════════════════
#  BOUTON ANNULER
# ══════════════════════════════════════════
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

# ══════════════════════════════════════════
#  REVENDEURS — PERSISTANCE
# ══════════════════════════════════════════
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
                f"🆕 *Nouvel utilisateur a lancé le bot 🜲 THE_S_TECH TUNNEL 🜲 *\n\n"
                f"👤 Nom : {user.first_name or 'N/A'}\n"
                f"🔗 Username : @{user.username or 'N/A'}\n"
                f"🆔 ID : `{user.id}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ══════════════════════════════════════════
#  GESTION REVENDEURS
# ══════════════════════════════════════════
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

# ══════════════════════════════════════════
#  VÉRIFICATION EXPIRATION AUTO (thread)
# ══════════════════════════════════════════
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
                            f"⛔ *Abonnement expiré à 🜲 THE_S_TECH TUNNEL 🜲*\n\n"
                            f"Votre abonnement est arrivé à son terme.\n"
                            f"Contactez {BRAND} pour le renouveler.",
                            parse_mode="Markdown",
                            reply_markup=markup
                        )
                    except Exception:
                        pass
                    try:
                        bot.send_message(
                            SUPER_ADMIN,
                            f"ℹ️ Revendeur *{alias}* (`{rid}`) expiré — supprimé automatiquement.",
                            parse_mode="Markdown"
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

# ══════════════════════════════════════════
#  ACCÈS
# ══════════════════════════════════════════
def is_admin(user_id):
    cfg = load_config()
    return user_id == SUPER_ADMIN or user_id in cfg.get('admins', [])

def has_access(user_id):
    return is_admin(user_id) or (get_reseller(user_id) is not None and is_active(user_id))

# ══════════════════════════════════════════
#  NOTIFICATION OWNER — COMPTE CRÉÉ
# ══════════════════════════════════════════
def notify_owner_account_created(creator_id, proto, username, days, password=""):
    if creator_id == SUPER_ADMIN:
        return
    try:
        r     = get_reseller(creator_id)
        alias = r.get("alias", str(creator_id)) if r else "OWNER"
        msg   = (
            f"🔔 <b>Nouveau compte créé sur 🜲 THE_S_TECH TUNNEL 🜲</b>\n\n"
            f"👤 Revendeur : <b>{alias}</b> (<code>{creator_id}</code>)\n"
            f"🔌 Protocole : <b>{proto.upper()}</b>\n"
            f"🧑 Username  : <code>{username}</code>\n"
        )
        if password:
            msg += f"🔑 Mot de passe : <code>{password}</code>\n"
        msg += f"⏳ Durée : <b>{days} jour(s)</b>"
        bot.send_message(SUPER_ADMIN, msg, parse_mode="HTML")
    except Exception:
        pass

# ══════════════════════════════════════════
#  RÉCUPÉRATION INFOS SYSTÈME
# ══════════════════════════════════════════
def get_system_info():
    ip          = subprocess.getoutput("curl -s ifconfig.me").strip()
    domain      = subprocess.getoutput("cat /etc/xray/domain 2>/dev/null || echo 'N/A'").strip()
    slowdns_pub = subprocess.getoutput("cat /etc/slowdns/server.pub 2>/dev/null || echo 'N/A'").strip()
    return ip, domain, slowdns_pub

# ══════════════════════════════════════════
#  CLAVIERS
# ══════════════════════════════════════════
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
    if call.message.content_type == 'photo':
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)
    else:
        bot.edit_message_text(text, chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              parse_mode="HTML", reply_markup=markup)

# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════
@bot.message_handler(commands=['start'])
def send_welcome(message):
    uid = message.from_user.id
    register_visitor(message.from_user)
    if not has_access(uid):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_pps_unauth"))
        bot.send_message(
            uid,
            f"⛔ <b>Accès refusé à 🜲 THE_S_TECH TUNNEL 🜲</b>\n\n"
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
    bot.send_photo(
        uid,
        MENU_IMAGE_URL,
        caption=f"<b> 🜲 THE_S_TECH TUNNEL 🜲</b>{extra}\nSélectionnez un Protocole :",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(uid)
    )

# ══════════════════════════════════════════
#  RETOUR ACCUEIL
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "action_home")
def home_callback(call):
    uid = call.from_user.id
    if not has_access(uid): return
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    r      = get_reseller(uid)
    rtype  = r['type'].upper() if r else "OWNER"
    remain = fmt_remain(uid) if r else ""
    extra  = f"\n🏷️ Type : <b>{rtype}</b> | ⏳ Restant : <b>{remain}</b>" if r else ""
    bot.send_photo(
        call.message.chat.id,
        MENU_IMAGE_URL,
        caption=f"<b> 🜲 THE_S_TECH TUNNEL 🜲</b>{extra}\nSélectionnez un module :",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(uid)
    )

# ══════════════════════════════════════════
#  DÉFINIR SA MARQUE (SUPER ADMIN UNIQUEMENT)
# ══════════════════════════════════════════
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
        f"👉 Exemple : <code>/setbrand 🜲 THE_S_TECH </code>\n\n"
        f"✅ Le nouveau nom remplacera <b>{BRAND}</b> partout dans le bot.",
        parse_mode="HTML"
    )

# ══════════════════════════════════════════
#  SOUS-MENUS PROTOCOLES
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data in (
    "menu_ssh","menu_vmess","menu_vless","menu_trojan","menu_socks","menu_zivpn"
))
def protocol_submenu(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    _show_submenu(call, f"<b>Module {proto.upper()} — {BRAND}</b>\nChoisissez une action :",
                  protocol_menu_keyboard(proto, uid))

# ══════════════════════════════════════════
#  CONTACT THE_S
# ══════════════════════════════════════════
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
            f"👤 {alias} | 🏷️ {rtype}\n"
            f"🆔 <code>{uid}</code> | @{uname}\n\n"
            f"{message.text}",
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception: pass
    bot.send_message(uid, f"✅ Message envoyé à <b>{BRAND}</b>.", parse_mode="HTML",
                     reply_markup=main_menu_keyboard(uid) if has_access(uid) else None)

# ══════════════════════════════════════════
#  MESSAGERIE OWNER → REVENDEURS
# ══════════════════════════════════════════
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
        f"<b>💬 Messagerie de 🜲 THE_S_TECH TUNNEL 🜲</b>\n\nChoisissez un destinataire :",
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
        hist += f"{arrow} {m['sender']} [{m['time']}]\n{m['text']}\n\n"
    if not hist: hist = "Aucun message précédent.\n\n"
    user_sessions[uid] = {"state": "msg_to_user", "target_id": int(tid), "alias": alias}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Annuler", callback_data="msg_panel"))
    _show_submenu(call,
        f"<b>💬 Conversation avec {alias} ({rtype})</b>\n"
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
        f"↩️ <b>Répondre à {alias}</b>\n\nÉcrivez votre réponse :",
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
                bot.send_message(int(rid), f"📢 <b>Message de {BRAND}</b>\n\n{message.text}", parse_mode="HTML")
                add_conv_msg(int(rid), resellers[rid].get("alias", rid), message.text, "to")
                sent += 1
            except Exception: pass
        bot.send_message(uid, f"✅ Message envoyé à {sent} revendeur(s).", reply_markup=main_menu_keyboard(uid))
    elif state.get("state") == "msg_to_user":
        tid   = state["target_id"]
        alias = state["alias"]
        add_conv_msg(tid, alias, message.text, "to")
        try:
            bot.send_message(tid, f"💬 <b>Message de {BRAND}</b>\n\n{message.text}", parse_mode="HTML")
            bot.send_message(uid, f"✅ Message envoyé à <b>{alias}</b>.",
                             parse_mode="HTML", reply_markup=main_menu_keyboard(uid))
        except Exception as e:
            bot.send_message(uid, f"❌ Erreur : {e}")

# ══════════════════════════════════════════
#  GESTION REVENDEURS (bouton menu)
#  CORRECTION : envoi en send_message au lieu de _show_submenu
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "menu_resellers" and is_admin(call.from_user.id))
def menu_resellers(call):
    resellers = load_resellers()
    lines = []
    for rid, info in resellers.items():
        remain = fmt_remain(int(rid))
        lines.append(
            f"• <b>{info.get('alias', rid)}</b> | ID: <code>{rid}</code>\n"
            f"  Type: {info.get('type','?').upper()} | Max: {info.get('max_days',30)}j | Restant: {remain}"
        )
    txt = "\n\n".join(lines) if lines else "Aucun revendeur."
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Retour", callback_data="action_home"))
    text = (
        f"<b>👥 Revendeurs de 🜲 THE_S_TECH TUNNEL 🜲</b>\n\n{txt}\n\n"
        f"➕ /addreseller &lt;ID&gt; &lt;Alias&gt; &lt;trial|premium&gt; [jours]\n"
        f"➖ /delreseller &lt;ID&gt;\n"
        f"✏️ /setmaxdays &lt;ID&gt; &lt;jours&gt;\n"
        f"⏳ /extendtrial &lt;ID&gt; &lt;heures&gt;"
    )
    # On supprime l'ancien message et on envoie un nouveau
    # pour éviter le bug d'édition sur les messages photo
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)

# ══════════════════════════════════════════
#  COMMANDES REVENDEURS
# ══════════════════════════════════════════
@bot.message_handler(commands=['addreseller'])
def cmd_add_reseller(message):
    uid = message.from_user.id
    if not is_admin(uid):
        bot.reply_to(message, f"🔒 Réservé au propriétaire {BRAND}."); return
    args = message.text.split()[1:]
    if len(args) < 3:
        bot.reply_to(message,
            f"Usage :\n/addreseller <ID> <Alias> <trial|premium> [max_jours]\n\n"
            f"Exemple :\n/addreseller 123456789 Jean premium 30",
            parse_mode="HTML"); return
    try:
        new_id   = int(args[0])
        alias    = args[1]
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
            f"✅ Revendeur ajouté à 🜲 THE_S_TECH TUNNEL 🜲\n\n"
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
        bot.reply_to(message, "Usage : /delreseller <ID>"); return
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
                f"⛔ <b>Accès retiré à 🜲 THE_S_TECH TUNNEL 🜲</b>\n\n"
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
        bot.reply_to(message, "Usage : /setmaxdays <ID> <jours>"); return
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
        bot.reply_to(message, "Usage : /extendtrial <ID> <heures>\nEx: /extendtrial 123456 72"); return
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
                f"🎉 <b>Abonnement prolongé sur 🜲 THE_S_TECH TUNNEL 🜲</b>\n\n"
                f"Votre abonnement a été prolongé de {hours} heure(s) par {BRAND}.\n"
                f"Nouveau temps restant : {fmt_remain(int(tid))}.",
                parse_mode="HTML")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "❌ Valeur invalide.")

# ══════════════════════════════════════════
#  SETBRAND
# ══════════════════════════════════════════
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
            "Usage : /setbrand <nouveau nom>\n\n"
            "Exemple : /setbrand NOVA TECH",
            parse_mode="HTML")
        return
    new_brand = args[1].strip()
    cfg = load_config()
    cfg['brand'] = new_brand
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
    BRAND = new_brand
    bot.reply_to(message, f"✅ Marque mise à jour : <b>{new_brand}</b>", parse_mode="HTML")

# ══════════════════════════════════════════
#  SSH — CRÉATION
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "add_ssh")
def add_ssh_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur  🜲 THE_S_TECH TUNNEL 🜲\nContactez {BRAND}.",
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
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{user}</b>...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "SSH", user, days_int, password)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# SSH — RENOUVELLEMENT
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
        f"⏳ <b>Jours à ajouter pour</b> <code>{user}</code>\n\n"
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
    success, res = ssh_core.renew_ssh_account(user, days_int)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# SSH — SUPPRESSION
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
    success, res = ssh_core.delete_ssh_account(user)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# SSH — VERROUILLAGE / DÉVERROUILLAGE
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
        success, res = ssh_core.lock_ssh_account(user)
    else:
        success, res = ssh_core.unlock_ssh_account(user)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# ══════════════════════════════════════════
#  SSH — LISTE + AFFICHAGE FORMAT COMPLET
# ══════════════════════════════════════════
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
        text = f"📋 <b>LISTE DES COMPTES SSH sur 🜲 THE_S_TECH TUNNEL 🜲</b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte SSH trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_ssh_"))
def view_ssh_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_ssh_"):]
    ip, domain, _ = get_system_info()
    #password = subprocess.getoutput(
    #    f"grep '^{user}:' /etc/shadow 2>/dev/null | cut -d: -f2 || echo 'N/A'"
    #).strip()
    password = "*******"
    expiry_raw = subprocess.getoutput(
        f"chage -l {user} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip()
    expiry = expiry_raw if expiry_raw else "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃       SSH/WS ACCOUNT\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{user}</code>\n"
        f"🔑 Password: <code>{password}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Domain: <code>{domain}</code>\n"
        f"🔌 Port SSH: <code>22</code>\n"
        f"🔌 Port WS: <code>80</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Développé par 🜲 THE_S TECH.✅\n"
        f" ━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_ssh"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

# ══════════════════════════════════════════
#  SLOW DNS
# ══════════════════════════════════════════
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
        _show_submenu(call, f"❌ Abonnement expiré sur 🜲 THE_S_TECH TUNNEL 🜲.",
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
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{user}</b> (SLOW DNS)...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "SLOW DNS", user, days_int, password)
        ip, domain, slowdns_pub = get_system_info()
        ns_domain = subprocess.getoutput("cat /etc/slowdns/nsdomain 2>/dev/null || echo 'N/A'").strip()
        expiry = subprocess.getoutput(f"chage -l {user} | grep 'Account expires' | cut -d: -f2").strip()
        res = (
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃      SLOW DNS ACCOUNT \n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"👤 Username: <code>{user}</code>\n"
            f"🔑 Password: <code>{password}</code>\n"
            f"⏳ Expiry: <code>{expiry}</code>\n"
            f"🖥️ IP: <code>{ip}</code>\n"
            f"🌐 Domain: <code>{domain}</code>\n"
            f"📛 NS Domain: <code>{ns_domain}</code>\n"
            f"🔌 Port DNS: <code>53</code>\n"
            f"🔥 PUB Key:\n<code>{slowdns_pub}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Développé par 🜲 THE_S TECH.✅\n"
            f" ━━━━━━━━━━━━━━━━━━━━━━━━"
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
        f"⏳ <b>Jours à ajouter pour</b> <code>{user}</code>\n\n"
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
    success, res = ssh_core.renew_ssh_account(user, days_int)
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
    success, res = ssh_core.delete_ssh_account(user)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# ══════════════════════════════════════════
#  SLOW DNS — LISTE + AFFICHAGE FORMAT COMPLET
# ══════════════════════════════════════════
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
        text = f"📋 <b>LISTE DES COMPTES SLOW DNS sur 🜲 THE_S_TECH TUNNEL 🜲</b>\nSélectionnez un compte :"
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
    #password  = subprocess.getoutput(
    #    f"grep '^{user}:' /etc/shadow 2>/dev/null | cut -d: -f2 || echo 'N/A'"
    #).strip()
    password = "*******"
    expiry = subprocess.getoutput(
        f"chage -l {user} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip() or "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃      SLOW DNS ACCOUNT \n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{user}</code>\n"
        f"🔑 Password: <code>{password}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Domain: <code>{domain}</code>\n"
        f"📛 NS Domain: <code>{ns_domain}</code>\n"
        f"🔌 Port DNS: <code>53</code>\n"
        f"🔥 PUB Key:\n<code>{slowdns_pub}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Développé par 🜲 THE_S TECH.✅\n"
        f" ━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_slowdns"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

# ══════════════════════════════════════════
#  UDP FAST
# ══════════════════════════════════════════
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
        _show_submenu(call, f"❌ Abonnement expiré sur 🜲 THE_S_TECH TUNNEL 🜲.",
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
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{user}</b> (UDP FAST)...", parse_mode="HTML")
    success, res = ssh_core.create_ssh_account(user, password, days_int, created_by_id=creator_id)
    if success:
        notify_owner_account_created(creator_id, "UDP FAST", user, days_int, password)
        ip, domain, _ = get_system_info()
        expiry = subprocess.getoutput(f"chage -l {user} | grep 'Account expires' | cut -d: -f2").strip()
        res = (
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃      UDP FAST ACCOUNT \n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"👤 Username: <code>{user}</code>\n"
            f"🔑 Password: <code>{password}</code>\n"
            f"⏳ Expiry: <code>{expiry}</code>\n"
            f"🖥️ IP: <code>{ip}</code>\n"
            f"🌐 Host: <code>{domain}</code>\n"
            f"🔌 Port UDP: <code>1-65535</code>\n"
            f"🚀 Config: <code>{ip}:1-65535@{user}:{password}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Développé par 🜲 THE_S TECH.✅\n"
            f" ━━━━━━━━━━━━━━━━━━━━━━━━"
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
        f"⏳ <b>Jours à ajouter pour</b> <code>{user}</code>\n\n"
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
    success, res = ssh_core.renew_ssh_account(user, days_int)
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
    success, res = ssh_core.delete_ssh_account(user)
    bot.send_message(message.chat.id, res, parse_mode="HTML", reply_markup=main_menu_keyboard(creator_id))

# ══════════════════════════════════════════
#  UDP FAST — LISTE + AFFICHAGE FORMAT COMPLET
# ══════════════════════════════════════════
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
        text = f"📋 <b>LISTE DES COMPTES UDP FAST sur 🜲 THE_S_TECH TUNNEL 🜲</b>\nSélectionnez un compte :"
    else:
        text = "📋 Aucun compte UDP FAST trouvé."
    markup.add(InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    _show_submenu(call, text, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_udpfast_"))
def view_udpfast_account(call):
    if not is_admin(call.from_user.id): return
    user = call.data[len("view_udpfast_"):]
    ip, domain, _ = get_system_info()
    #password = subprocess.getoutput(
    #    f"grep '^{user}:' /etc/shadow 2>/dev/null | cut -d: -f2 || echo 'N/A'"
    #).strip()
    password = "*******"
    expiry = subprocess.getoutput(
        f"chage -l {user} 2>/dev/null | grep 'Account expires' | cut -d: -f2"
    ).strip() or "N/A"
    details = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃      UDP FAST ACCOUNT \n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"👤 Username: <code>{user}</code>\n"
        f"🔑 Password: <code>{password}</code>\n"
        f"⏳ Expiry: <code>{expiry}</code>\n"
        f"🖥️ IP: <code>{ip}</code>\n"
        f"🌐 Host: <code>{domain}</code>\n"
        f"🔌 Port UDP: <code>1-65535</code>\n"
        f"🚀 Config: <code>{ip}:1-65535@{user}:{password}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Développé par 🜲 THE_S TECH.✅\n"
        f" ━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔙 Retour Liste",   callback_data="list_udpfast"),
        InlineKeyboardButton("🏠 Retour Accueil", callback_data="action_home")
    )
    _show_submenu(call, details, markup)

# ══════════════════════════════════════════
#  XRAY (VMESS / VLESS / TROJAN / SOCKS)
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data in ("add_vless","add_vmess","add_trojan","add_socks"))
def add_xray_start(call):
    uid   = call.from_user.id
    if not has_access(uid): return
    proto = call.data.split("_", 1)[1]
    d_rem = days_remaining(uid)
    if d_rem == 0 and not is_admin(uid):
        _show_submenu(call, f"❌ Abonnement expiré sur 🜲 THE_S_TECH TUNNEL 🜲.",
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
    bot.send_message(message.chat.id, f"⚙️ Injection de <b>{user}</b> ({proto.upper()})...", parse_mode="HTML")
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
        f"⏳ <b>Jours à ajouter pour</b> <code>{user}</code>\n\n"
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
    success, res = xray_core.renew_xray_account(proto, user, days_int)
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
    success, res = xray_core.delete_xray_account(proto, user)
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

# ══════════════════════════════════════════
#  ZIVPN
# ══════════════════════════════════════════
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
    bot.send_message(message.chat.id, f"⚙️ Création du compte <b>{user}</b> (ZIVPN)...", parse_mode="HTML")
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
        f"⏳ <b>Jours à ajouter pour</b> <code>{user}</code>\n\n"
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
    success, res = zivpn_core.renew_zivpn_account(user, days_int)
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
    success, res = zivpn_core.delete_zivpn_account(user)
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

# ══════════════════════════════════════════
#  SYSTÈME
# ══════════════════════════════════════════
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
    subprocess.run("reboot", shell=True)

# ══════════════════════════════════════════
#  GESTION ADMINS
# ══════════════════════════════════════════
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

# ══════════════════════════════════════════
#  LANCEMENT
# ══════════════════════════════════════════
if __name__ == "__main__":
    os.makedirs("/etc/pps_bot", exist_ok=True)
    if not os.path.exists(RESELLERS_FILE): save_resellers({})
    if not os.path.exists(CONVS_FILE):    save_convs({})
    if not os.path.exists(VISITORS_FILE): save_visitors({})
    bot.infinity_polling()
