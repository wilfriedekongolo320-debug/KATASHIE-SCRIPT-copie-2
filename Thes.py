#!/usr/bin/env python3
"""Bot Telegram unifié : surveillance revendeurs, alertes automatiques + gestion comptes VPN/SSH/Xray/Zivpn."""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import telebot
from telebot import types

# Import des modules optionnels s'ils existent (compatibilité avec ton arborescence)
try:
    from modules import system_core, ssh_core, admin_core, xray_core, zivpn_core
except ImportError:
    system_core = ssh_core = admin_core = xray_core = zivpn_core = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("the-s-vpn-bot")

# Chemins de configuration
CONFIG_FILE = Path(os.environ.get("THE_S_BOT_CONFIG", "/etc/the_s_bot/config.json"))
RESELLERS_FILE = Path(os.environ.get("THE_S_BOT_RESELLERS", "/etc/the_s_bot/resellers.json"))
STATE_FILE = Path(os.environ.get("THE_S_BOT_STATE_FILE", "/var/lib/the-s-bot/state.json"))
CONVS_FILE = Path(os.environ.get("THE_S_BOT_CONVS", "/etc/the_s_bot/convs.json"))
VISITORS_FILE = Path(os.environ.get("THE_S_BOT_VISITORS", "/etc/the_s_bot/visitors.json"))
MENU_IMAGE_URL = "https://github.com/the_stech237/the_s-tg-bot/blob/main/the_s.jpg?raw=true"

POLL_SECONDS = max(60, int(os.environ.get("THE_S_BOT_POLL_SECONDS", "300")))
ALERT_THRESHOLDS = ((24, "24 heures"), (6, "6 heures"))
XRAY_CONFIG = "/etc/xray/config.json"
ZIVPN_DB = "/etc/zivpn/users.db"


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Lecture impossible pour %s: %s", path, exc)
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def load_config() -> dict[str, Any]:
    cfg = load_json(CONFIG_FILE, {})
    return cfg if isinstance(cfg, dict) else {}


config = load_config()
BOT_TOKEN = str(config.get("bot_token", "")).strip()
try:
    SUPER_ADMIN = int(config.get("super_admin", 0))
except (TypeError, ValueError):
    SUPER_ADMIN = 0

BRAND = config.get("brand", "🜲 THE_S")

if not BOT_TOKEN or not SUPER_ADMIN:
    raise SystemExit(f"Renseignez bot_token et super_admin dans {CONFIG_FILE}")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
state_lock = threading.Lock()
user_sessions: dict[int, dict[str, Any]] = {}


def load_resellers() -> dict[str, dict[str, Any]]:
    data = load_json(RESELLERS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_resellers(data: dict[str, dict[str, Any]]) -> None:
    save_json(RESELLERS_FILE, data)


def load_convs() -> dict[str, Any]:
    return load_json(CONVS_FILE, {})


def save_convs(data: dict[str, Any]) -> None:
    save_json(CONVS_FILE, data)


def load_visitors() -> dict[str, Any]:
    return load_json(VISITORS_FILE, {})


def save_visitors(data: dict[str, Any]) -> None:
    save_json(VISITORS_FILE, data)


def load_state() -> dict[str, Any]:
    return load_json(STATE_FILE, {})


def save_state(state: dict[str, Any]) -> None:
    save_json(STATE_FILE, state)


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
            return parsed
        except ValueError:
            return None


def reseller_expiry(info: dict[str, Any]) -> datetime | None:
    expiry = parse_datetime(info.get("expires_at") or info.get("expiry_datetime"))
    if expiry:
        return expiry
    added = parse_datetime(info.get("added_datetime"))
    if added is None and isinstance(info.get("added_date"), str):
        added = parse_datetime(f"{info['added_date']} 00:00:00")
    if added is None:
        return None
    try:
        default_hours = 48 if info.get("type") == "trial" else int(info.get("max_days", 30)) * 24
        hours = int(info.get("max_hours", default_hours))
        return added + timedelta(hours=hours) if hours >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def remaining(expiry: datetime, now: datetime) -> str:
    seconds = int((expiry - now).total_seconds())
    if seconds <= 0:
        return "expiré"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days} j {hours} h" if days else (f"{hours} h {minutes} min" if hours else f"{minutes} min")


def fmt_remain(uid: int) -> str:
    r = load_resellers().get(str(uid))
    if not r:
        return "N/A"
    exp = reseller_expiry(r)
    if not exp:
        return "N/A"
    now = datetime.now()
    if exp <= now:
        return "Expiré"
    diff = exp - now
    days = diff.days
    hours = diff.seconds // 3600
    return f"{days}j {hours}h" if days > 0 else f"{hours}h"


def escaped(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _run(cmd: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (result.stdout + result.stderr).strip()
    except Exception as exc:
        return str(exc)


def is_admin(user_id: int) -> bool:
    cfg = load_config()
    admins = cfg.get("admins", [])
    return user_id == SUPER_ADMIN or user_id in admins


def is_active(uid: int) -> bool:
    if is_admin(uid):
        return True
    r = load_resellers().get(str(uid))
    if not r:
        return False
    exp = reseller_expiry(r)
    return exp is not None and datetime.now() <= exp


def has_access(user_id: int) -> bool:
    return is_admin(user_id) or (load_resellers().get(str(user_id)) is not None and is_active(user_id))


def days_remaining(uid: int) -> int:
    if is_admin(uid):
        return 9999
    r = load_resellers().get(str(uid))
    if not r:
        return 0
    exp = reseller_expiry(r)
    if not exp:
        return 0
    remain = exp - datetime.now()
    return max(0, remain.days)


def send_admin(text: str) -> None:
    try:
        bot.send_message(SUPER_ADMIN, text)
    except Exception:
        logger.exception("Notification impossible au super administrateur")


def label(reseller_id: str, info: dict[str, Any]) -> str:
    alias = escaped(info.get("alias") or info.get("first_name") or reseller_id)
    kind = escaped(info.get("type", "inconnu")).upper()
    return f"<b>{alias}</b> (ID <code>{escaped(reseller_id)}</code>, {kind})"


# ══════════════════════════════════════════
#  VALIDATION
# ══════════════════════════════════════════
def validate_username(username: str) -> tuple[bool, str]:
    if not username or len(username) < 4:
        return False, "❌ Minimum 4 caractères.\n📌 Exemple : <code>jean01</code>"
    if " " in username:
        return False, "❌ Pas d'espaces autorisés.\n📌 Exemple : <code>jean01</code>"
    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        return False, "❌ Caractères autorisés : lettres, chiffres, <code>_</code> et <code>-</code> uniquement."
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < 4:
        return False, f"❌ Mot de passe trop court (min 4 caractères)."
    return True, ""


def validate_days(text: str, max_days: int) -> tuple[bool, str]:
    if not text.isdigit():
        return False, f"❌ Valeur invalide : <code>{text}</code>. Entrez un nombre entier."
    days_int = int(text)
    if days_int < 1:
        return False, "❌ Le nombre de jours doit être au moins 1."
    if days_int > max_days:
        return False, f"❌ Durée refusée : dépasse votre maximum autorisé (<b>{max_days}j</b>)."
    return True, ""


# ══════════════════════════════════════════
#  SURVEILLANCE & SCAN PERIODIQUE
# ══════════════════════════════════════════
def scan_resellers(notify_changes: bool = True) -> list[tuple[str, dict[str, Any], datetime | None]]:
    now = datetime.now()
    resellers = load_resellers()
    state = load_state()
    current_ids = set(resellers)
    with state_lock:
        prior = state.get("known_resellers")
        if not isinstance(prior, list):
            state["known_resellers"] = sorted(current_ids)
        elif notify_changes:
            for rid in sorted(current_ids - set(map(str, prior))):
                send_admin(f"🆕 Nouveau revendeur détecté : {label(rid, resellers[rid])}.")
            for rid in sorted(set(map(str, prior)) - current_ids):
                send_admin(f"➖ Revendeur retiré : <code>{escaped(rid)}</code>.")
            state["known_resellers"] = sorted(current_ids)

        alerts = state.setdefault("alerts", {})
        if not isinstance(alerts, dict):
            alerts = state["alerts"] = {}
        rows: list[tuple[str, dict[str, Any], datetime | None]] = []
        for rid, info in resellers.items():
            expiry = reseller_expiry(info)
            rows.append((rid, info, expiry))
            if expiry is None:
                continue
            expiry_key = expiry.isoformat(timespec="seconds")
            entry = alerts.get(rid)
            if not isinstance(entry, dict) or entry.get("expiry") != expiry_key:
                entry = {"expiry": expiry_key, "sent": []}
            sent = set(entry.get("sent", []))
            delta = expiry - now
            if delta.total_seconds() <= 0 and "expired" not in sent:
                send_admin(f"⛔ Abonnement expiré : {label(rid, info)}. Échéance : <code>{expiry:%d/%m/%Y %H:%M}</code>.")
                sent.add("expired")
                # Optionnel : notifier l'utilisateur directement
                try:
                    bot.send_message(
                        int(rid),
                        f"⛔ <b>Abonnement expiré sur {BRAND}</b>\n\nVotre abonnement est arrivé à son terme.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            elif delta.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    key = f"{hours}h"
                    if delta <= timedelta(hours=hours) and key not in sent:
                        send_admin(f"⚠️ Échéance dans moins de {description} : {label(rid, info)}. Restant : <b>{remaining(expiry, now)}</b>.")
                        sent.add(key)
            entry["expiry"], entry["sent"] = expiry_key, sorted(sent)
            alerts[rid] = entry
        for rid in set(alerts) - current_ids:
            alerts.pop(rid, None)
        save_state(state)
    return rows


def monitor_loop() -> None:
    while True:
        try:
            scan_resellers()
        except Exception:
            logger.exception("Erreur lors de la surveillance périodique")
        time.sleep(POLL_SECONDS)


threading.Thread(target=monitor_loop, name="reseller-monitor", daemon=True).start()


# ══════════════════════════════════════════
#  CLAVIERS & MENUS INTERACTIFS
# ══════════════════════════════════════════
def main_menu_keyboard(uid: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔑 SSH/WS", callback_data="menu_ssh"),
        types.InlineKeyboardButton("🔰 VMESS", callback_data="menu_vmess"),
        types.InlineKeyboardButton("🔰 VLESS", callback_data="menu_vless"),
        types.InlineKeyboardButton("🔰 TROJAN", callback_data="menu_trojan"),
        types.InlineKeyboardButton("🔥 SLOW DNS", callback_data="menu_slowdns"),
        types.InlineKeyboardButton("🚀 UDP FAST", callback_data="menu_udpfast"),
        types.InlineKeyboardButton("📱 ZIVPN", callback_data="menu_zivpn"),
        types.InlineKeyboardButton("📊 VPS STATUS", callback_data="menu_status"),
    )
    if is_admin(uid):
        markup.add(
            types.InlineKeyboardButton("👥 Revendeurs & Alertes", callback_data="menu_resellers"),
            types.InlineKeyboardButton("💬 Messagerie", callback_data="msg_panel"),
            types.InlineKeyboardButton("🔄 REBOOT VPS", callback_data="action_reboot"),
        )
    if uid == SUPER_ADMIN:
        markup.add(types.InlineKeyboardButton("🏷️ Définir ma marque", callback_data="set_brand_menu"))
    else:
        markup.add(types.InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_the_s"))
    return markup


def protocol_menu_keyboard(proto: str, uid: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    d_rem = days_remaining(uid)
    note = f" (max {d_rem}j)" if not is_admin(uid) and d_rem < 9999 else ""
    markup.add(
        types.InlineKeyboardButton(f"➕ Créer compte {proto.upper()}{note}", callback_data=f"add_{proto}"),
        types.InlineKeyboardButton(f"🔄 Renouveler compte {proto.upper()}", callback_data=f"renew_{proto}"),
        types.InlineKeyboardButton(f"🗑️ Supprimer compte {proto.upper()}", callback_data=f"del_{proto}"),
        types.InlineKeyboardButton(f"📋 Liste des comptes {proto.upper()}", callback_data=f"list_{proto}"),
    )
    markup.add(types.InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    return markup


def cancel_keyboard(back_menu: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ Annuler", callback_data=f"cancel_to_{back_menu}"))
    return markup


@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_to_"))
def handle_cancel(call: Any) -> None:
    uid = call.from_user.id
    target = call.data[len("cancel_to_"):]
    user_sessions.pop(uid, None)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(
        call.message.chat.id,
        "❌ <b>Opération annulée.</b>",
        parse_mode="HTML",
        reply_markup=protocol_menu_keyboard(target, uid) if target != "home" else main_menu_keyboard(uid),
    )


# ══════════════════════════════════════════
#  COMMANDES DE BASE & ACCUEIL
# ══════════════════════════════════════════
@bot.message_handler(commands=["start", "help", "menu"])
def send_welcome(message: Any) -> None:
    uid = message.from_user.id
    if not has_access(uid):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"📩 Contacter {BRAND}", callback_data="contact_the_s_unauth"))
        bot.send_message(
            uid,
            f"⛔ <b>Accès refusé à {BRAND}</b>\n\nContactez le propriétaire pour obtenir un accès.",
            parse_mode="HTML",
            reply_markup=markup,
        )
        return
    r = load_resellers().get(str(uid))
    rtype = r["type"].upper() if r else "OWNER"
    remain = fmt_remain(uid) if r else ""
    extra = f"\n🏷️ Type : <b>{rtype}</b> | ⏳ Restant : <b>{remain}</b>" if r else ""
    
    caption = f"🛡 <b>{BRAND} — Panneau de Contrôle VPN & Surveillance</b>{extra}\n\nSélectionnez un module :"
    try:
        bot.send_photo(uid, MENU_IMAGE_URL, caption=caption, parse_mode="HTML", reply_markup=main_menu_keyboard(uid))
    except Exception:
        bot.send_message(uid, caption, parse_mode="HTML", reply_markup=main_menu_keyboard(uid))


@bot.callback_query_handler(func=lambda call: call.data == "action_home")
def home_callback(call: Any) -> None:
    uid = call.from_user.id
    if not has_access(uid):
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    r = load_resellers().get(str(uid))
    rtype = r["type"].upper() if r else "OWNER"
    remain = fmt_remain(uid) if r else ""
    extra = f"\n🏷️ Type : <b>{rtype}</b> | ⏳ Restant : <b>{remain}</b>" if r else ""
    caption = f"🛡 <b>{BRAND} — Accueil</b>{extra}\n\nSélectionnez un module :"
    try:
        bot.send_photo(call.message.chat.id, MENU_IMAGE_URL, caption=caption, parse_mode="HTML", reply_markup=main_menu_keyboard(uid))
    except Exception:
        bot.send_message(call.message.chat.id, caption, parse_mode="HTML", reply_markup=main_menu_keyboard(uid))


# ══════════════════════════════════════════
#  MODULES DE GESTION DES REVENDEURS (ADMIN)
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "menu_resellers" and is_admin(call.from_user.id))
def menu_resellers(call: Any) -> None:
    rows, now = scan_resellers(notify_changes=False), datetime.now()
    lines = []
    for rid, info, expiry in rows:
        exp_text = "Inconnue" if expiry is None else f"{remaining(expiry, now)} ({expiry:%d/%m/%Y %H:%M})"
        lines.append(f"• {label(rid, info)}\n  ⏳ {exp_text}")
    txt = "\n\n".join(lines) if lines else "Aucun revendeur enregistré."
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Retour", callback_data="action_home"))
    text = (
        f"<b>👥 Gestion & Surveillance des Revendeurs</b>\n\n{txt}\n\n"
        f"➕ <code>/addreseller &lt;ID&gt; &lt;Alias&gt; &lt;trial|premium&gt; [jours]</code>\n"
        f"➖ <code>/delreseller &lt;ID&gt;</code>\n"
        f"📊 <code>/status</code> — Résumé des échéances"
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=["resellers"])
def cmd_list_resellers(message: Any) -> None:
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Commande réservée à l'administrateur.")
        return
    rows, now = scan_resellers(notify_changes=False), datetime.now()
    lines = ["👥 <b>Liste et temps restant des revendeurs</b>"]
    for rid, info, expiry in rows:
        exp_text = "inconnue" if expiry is None else f"{remaining(expiry, now)} — {expiry:%d/%m/%Y %H:%M}"
        lines.append(f"• {label(rid, info)}\n  ⏳ {escaped(exp_text)}")
    if not rows:
        lines.append("Aucun revendeur enregistré.")
    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["status"])
def cmd_status(message: Any) -> None:
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Commande réservée à l'administrateur.")
        return
    rows, now = scan_resellers(notify_changes=False), datetime.now()
    expired = sum(1 for _, _, exp in rows if exp is not None and exp <= now)
    soon = sum(1 for _, _, exp in rows if exp is not None and now < exp <= now + timedelta(hours=24))
    unknown = sum(1 for _, _, exp in rows if exp is None)
    bot.reply_to(
        message,
        f"📊 <b>État de la surveillance</b>\n\n"
        f"👥 Total revendeurs : <b>{len(rows)}</b>\n"
        f"⚠️ Échéance sous 24 h : <b>{soon}</b>\n"
        f"⛔ Expirés : <b>{expired}</b>\n"
        f"❓ Inconnus : <b>{unknown}</b>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["addreseller"])
def cmd_add_reseller(message: Any) -> None:
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()[1:]
    if len(args) < 3:
        bot.reply_to(message, "Usage : <code>/addreseller &lt;ID&gt; &lt;Alias&gt; &lt;trial|premium&gt; [max_jours]</code>", parse_mode="HTML")
        return
    try:
        new_id = int(args[0])
        alias = args[1]
        rtype = args[2].lower()
        if rtype not in ("trial", "premium"):
            bot.reply_to(message, "❌ Type invalide. Utilisez 'trial' ou 'premium'.")
            return
        max_days = int(args[3]) if len(args) >= 4 else (2 if rtype == "trial" else 30)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resellers = load_resellers()
        resellers[str(new_id)] = {
            "alias": alias,
            "type": rtype,
            "added_date": datetime.now().strftime("%Y-%m-%d"),
            "added_datetime": now_str,
            "max_days": max_days,
            "max_hours": 48 if rtype == "trial" else max_days * 24,
        }
        save_resellers(resellers)
        bot.reply_to(message, f"✅ Revendeur <b>{alias}</b> ajouté avec succès !", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "❌ ID ou format de jours invalide.")


@bot.message_handler(commands=["delreseller"])
def cmd_del_reseller(message: Any) -> None:
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()[1:]
    if not args:
        bot.reply_to(message, "Usage : <code>/delreseller &lt;ID&gt;</code>", parse_mode="HTML")
        return
    rid = args[0]
    resellers = load_resellers()
    if rid in resellers:
        del resellers[rid]
        save_resellers(resellers)
        bot.reply_to(message, f"✅ Revendeur <code>{rid}</code> supprimé.", parse_mode="HTML")
    else:
        bot.reply_to(message, "❌ ID revendeur introuvable.")


# ══════════════════════════════════════════
#  SOUS-MENUS DES PROTOCOLES VPN
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data in ("menu_ssh", "menu_vmess", "menu_vless", "menu_trojan", "menu_slowdns", "menu_udpfast", "menu_zivpn"))
def protocol_submenu(call: Any) -> None:
    uid = call.from_user.id
    if not has_access(uid):
        return
    proto = call.data.split("_", 1)[1]
    
    # Redirection vers les fonctions spécifiques si les modules core existent, sinon affichage du menu générique
    text = f"<b>Module {proto.upper()} — {BRAND}</b>\nChoisissez une action :"
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=protocol_menu_keyboard(proto, uid))
    except Exception:
        bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=protocol_menu_keyboard(proto, uid))


# ══════════════════════════════════════════
#  COMMANDES SYSTÈME & INFOS
# ══════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "menu_status")
def handle_status_callback(call: Any) -> None:
    if not has_access(call.from_user.id):
        return
    status_text = system_core.get_vps_status() if system_core else "📊 Statut VPS opérationnel."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Retour Accueil", callback_data="action_home"))
    try:
        bot.edit_message_text(status_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(call.message.chat.id, status_text, parse_mode="HTML", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "action_reboot")
def handle_reboot(call: Any) -> None:
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id, "♻️ Redémarrage en cours...")
    bot.send_message(call.message.chat.id, "♻️ <b>Redémarrage du VPS lancé.</b>", parse_mode="HTML")
    subprocess.run("reboot", shell=True)


@bot.message_handler(commands=["setbrand"])
def cmd_setbrand(message: Any) -> None:
    global BRAND
    if message.from_user.id != SUPER_ADMIN:
        bot.reply_to(message, "🔒 Réservé au Super Admin.")
        return
    args = message.text.split(None, 1)
    if len(args) < 2:
        bot.reply_to(message, "Usage : <code>/setbrand &lt;Nom&gt;</code>", parse_mode="HTML")
        return
    BRAND = args[1].strip()
    cfg = load_config()
    cfg["brand"] = BRAND
    save_json(CONFIG_FILE, cfg)
    bot.reply_to(message, f"✅ Marque mise à jour : <b>{BRAND}</b>", parse_mode="HTML")


# ══════════════════════════════════════════
#  LANCEMENT DU BOT
# ══════════════════════════════════════════
if __name__ == "__main__":
    os.makedirs("/etc/the_s_bot", exist_ok=True)
    if not RESELLERS_FILE.exists():
        save_resellers({})
    if not CONVS_FILE.exists():
        save_convs({})
    if not VISITORS_FILE.exists():
        save_visitors({})

    logger.info("Bot Telegram unifié démarré avec succès.")
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
