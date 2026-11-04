#!/usr/bin/env python3
"""Bot Telegram Thes : surveillance revendeurs et gestion VPN avec rôles."""
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

CONFIG_FILE = Path(os.environ.get("THE_S_BOT_CONFIG", "/etc/the_s_bot/config.json"))
RESELLERS_FILE = Path(os.environ.get("THE_S_BOT_RESELLERS", "/etc/the_s_bot/resellers.json"))
STATE_FILE = Path(os.environ.get("THE_S_BOT_STATE_FILE", "/var/lib/the-s-bot/state.json"))
MENU_IMAGE_FILE = Path(os.environ.get("THE_S_BOT_MENU_IMAGE", "/etc/the_s_bot/menu.jpg"))
POLL_SECONDS = max(60, int(os.environ.get("THE_S_BOT_POLL_SECONDS", "300")))
ALERT_THRESHOLDS = ((24, "24 heures"), (6, "6 heures"))
XRAY_CONFIG = "/etc/xray/config.json"
ZIVPN_DB = "/etc/zivpn/users.db"
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,32}$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("the-s-vpn-bot")


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Lecture impossible pour %s: %s", path, exc)
        return default


config = load_json(CONFIG_FILE, {})
if not isinstance(config, dict):
    raise SystemExit(f"Configuration invalide : {CONFIG_FILE}")
BOT_TOKEN = str(config.get("bot_token", "")).strip()
try:
    SUPER_ADMIN = int(config.get("super_admin", 0))
except (TypeError, ValueError):
    SUPER_ADMIN = 0
if not BOT_TOKEN or not SUPER_ADMIN:
    raise SystemExit(f"Renseignez bot_token et super_admin dans {CONFIG_FILE}")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
state_lock = threading.RLock()


def escaped(value: Any) -> str:
    return html.escape(str(value), quote=False)


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def run_shell(command: str, timeout: int = 30) -> str:
    result = run(["bash", "-lc", command], timeout)
    return (result.stdout + result.stderr).strip()


def load_state() -> dict[str, Any]:
    data = load_json(STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, STATE_FILE)


def get_role_ids(role: str) -> set[int]:
    # Migration douce des listes présentes dans l'ancienne configuration.
    legacy_keys = {"admin": ("admin_ids", "admins"), "reseller": ("reseller_ids", "resellers_ids")}
    ids: set[int] = set()
    for key in legacy_keys.get(role, ()):
        values = config.get(key, [])
        if isinstance(values, list):
            for value in values:
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    logger.warning("ID %r invalide dans %s", value, key)
    state = load_state()
    roles = state.get("telegram_roles", {})
    values = roles.get(role, []) if isinstance(roles, dict) else []
    if isinstance(values, list):
        for value in values:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return ids


def role_for(uid: int) -> str | None:
    if uid == SUPER_ADMIN:
        return "super_admin"
    if uid in get_role_ids("admin"):
        return "admin"
    if uid in get_role_ids("reseller"):
        return "reseller"
    return None


def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN


def require_role(message: Any, roles: set[str]) -> str | None:
    role = role_for(message.from_user.id)
    if role not in roles:
        bot.reply_to(message, "⛔ Accès refusé pour votre rôle.")
        return None
    return role


def parse_days(value: str) -> int | None:
    try:
        days = int(value)
        return days if 1 <= days <= 3650 else None
    except (TypeError, ValueError):
        return None


def load_resellers() -> dict[str, dict[str, Any]]:
    data = load_json(RESELLERS_FILE, {})
    if not isinstance(data, dict):
        logger.error("Le fichier revendeurs doit contenir un objet JSON.")
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
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
        hours = int(info.get("max_hours", 48 if info.get("type") == "trial" else int(info.get("max_days", 30)) * 24))
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


def reseller_label(reseller_id: str, info: dict[str, Any]) -> str:
    alias = escaped(info.get("alias") or info.get("first_name") or reseller_id)
    kind = escaped(info.get("type", "inconnu")).upper()
    return f"<b>{alias}</b> (ID <code>{escaped(reseller_id)}</code>, {kind})"


def notify_super(text: str) -> None:
    try:
        bot.send_message(SUPER_ADMIN, text)
    except Exception:
        logger.exception("Notification impossible au super administrateur")


def scan_resellers(notify_changes: bool = True) -> list[tuple[str, dict[str, Any], datetime | None]]:
    now = datetime.now()
    resellers = load_resellers()
    with state_lock:
        state = load_state()
        current_ids = set(resellers)
        prior = state.get("known_resellers")
        if not isinstance(prior, list):
            state["known_resellers"] = sorted(current_ids)
        elif notify_changes:
            for rid in sorted(current_ids - set(map(str, prior))):
                notify_super(f"🆕 Nouveau revendeur détecté : {reseller_label(rid, resellers[rid])}.")
            for rid in sorted(set(map(str, prior)) - current_ids):
                notify_super(f"➖ Revendeur retiré : <code>{escaped(rid)}</code>.")
            state["known_resellers"] = sorted(current_ids)
        alerts = state.setdefault("alerts", {})
        if not isinstance(alerts, dict):
            alerts = state["alerts"] = {}
        rows = []
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
                notify_super(f"⛔ Abonnement expiré : {reseller_label(rid, info)}. Échéance : <code>{expiry:%d/%m/%Y %H:%M}</code>.")
                sent.add("expired")
            elif delta.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    key = f"{hours}h"
                    if delta <= timedelta(hours=hours) and key not in sent:
                        notify_super(f"⚠️ Échéance dans moins de {description} : {reseller_label(rid, info)}. Temps restant : <b>{remaining(expiry, now)}</b> (échéance <code>{expiry:%d/%m/%Y %H:%M}</code>).")
                        sent.add(key)
            entry["expiry"], entry["sent"] = expiry_key, sorted(sent)
            alerts[rid] = entry
        for rid in set(alerts) - current_ids:
            alerts.pop(rid, None)
        save_state(state)
    return rows


def add_role(role: str, uid: int) -> bool:
    with state_lock:
        state = load_state()
        roles = state.setdefault("telegram_roles", {"admin": [], "reseller": []})
        values = roles.setdefault(role, [])
        values = [int(value) for value in values if str(value).isdigit()]
        if uid == SUPER_ADMIN or uid in values:
            return False
        values.append(uid)
        roles[role] = sorted(set(values))
        save_state(state)
    return True


def remove_role(role: str, uid: int) -> bool:
    with state_lock:
        state = load_state()
        roles = state.setdefault("telegram_roles", {"admin": [], "reseller": []})
        values = roles.get(role, [])
        updated = [int(value) for value in values if str(value).isdigit() and int(value) != uid]
        if len(updated) == len(values):
            return False
        roles[role] = updated
        save_state(state)
    return True


def actor_name(message: Any) -> str:
    user = message.from_user
    return " ".join(part for part in (user.first_name, user.last_name) if part) or user.username or str(user.id)


def audit_creation(message: Any, protocol: str, username: str, expiry: str) -> None:
    role = role_for(message.from_user.id) or "inconnu"
    now = datetime.now().astimezone()
    notify_super(
        "🆕 <b>Compte VPN créé</b>\n"
        f"👤 Auteur : <code>{escaped(actor_name(message))}</code> (ID <code>{message.from_user.id}</code>, {escaped(role)})\n"
        f"⚙️ Protocole : <code>{escaped(protocol.upper())}</code>\n"
        f"🧾 Compte : <code>{escaped(username)}</code>\n"
        f"📅 Expiration : <code>{escaped(expiry)}</code>\n"
        f"🕒 Création : <code>{now:%d/%m/%Y %H:%M:%S %Z}</code>"
    )


def ssh_create(username: str, days: int) -> tuple[bool, str, str]:
    if not USERNAME_RE.fullmatch(username):
        return False, "Nom d'utilisateur invalide.", ""
    if run(["id", username]).returncode == 0:
        return False, f"L'utilisateur {username} existe déjà.", ""
    password = secrets.token_urlsafe(12)
    exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    result = run(["useradd", "-e", exp, "-s", "/bin/false", "-M", username])
    if result.returncode:
        return False, result.stderr.strip() or "Échec de useradd.", ""
    passwd = run(["chpasswd"], timeout=10) if False else subprocess.run(["chpasswd"], input=f"{username}:{password}\n", capture_output=True, text=True)
    if passwd.returncode:
        run(["userdel", "-r", username])
        return False, passwd.stderr.strip() or "Échec de définition du mot de passe.", ""
    return True, f"✅ SSH créé : <code>{escaped(username)}</code>, mot de passe <code>{escaped(password)}</code>, expiration <code>{exp}</code>.", exp


def ssh_delete(username: str) -> tuple[bool, str]:
    if not USERNAME_RE.fullmatch(username):
        return False, "Nom d'utilisateur invalide."
    result = run(["userdel", "-r", username])
    if result.returncode:
        return False, result.stderr.strip() or "Suppression impossible ou compte introuvable."
    return True, f"🗑 Compte SSH <code>{escaped(username)}</code> supprimé."


def xray_add(protocol: str, username: str, days: int) -> tuple[bool, str, str]:
    protocol = protocol.lower()
    if protocol not in {"vless", "vmess", "trojan"} or not USERNAME_RE.fullmatch(username):
        return False, "Protocole ou nom d'utilisateur invalide.", ""
    path = Path(XRAY_CONFIG)
    try:
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Lecture de la configuration Xray impossible : {exc}", ""
    inbound = next((item for item in data.get("inbounds", []) if item.get("protocol", "").lower() == protocol), None)
    if inbound is None:
        return False, f"Aucun inbound {protocol.upper()} trouvé.", ""
    clients = inbound.setdefault("settings", {}).setdefault("clients", [])
    if any(client.get("email") == username for client in clients):
        return False, "Ce compte existe déjà.", ""
    user_uuid = str(uuid.uuid4())
    client = {"id": user_uuid, "email": username} if protocol != "trojan" else {"password": secrets.token_urlsafe(18), "email": username}
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    client["expiryDate"] = expiry
    clients.append(client)
    try:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except OSError as exc:
        return False, f"Écriture de la configuration impossible : {exc}", ""
    restarted = run(["systemctl", "restart", "xray"])
    if restarted.returncode:
        return False, f"Le compte a été écrit mais le redémarrage Xray a échoué : {restarted.stderr.strip()}", expiry
    cred = client.get("password", user_uuid)
    return True, f"✅ {protocol.upper()} créé : <code>{escaped(username)}</code>, identifiant <code>{escaped(cred)}</code>, expiration <code>{expiry}</code>.", expiry


def xray_delete(protocol: str, username: str) -> tuple[bool, str]:
    protocol = protocol.lower()
    if protocol not in {"vless", "vmess", "trojan"} or not USERNAME_RE.fullmatch(username):
        return False, "Protocole ou nom d'utilisateur invalide."
    path = Path(XRAY_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Lecture Xray impossible : {exc}"
    inbound = next((item for item in data.get("inbounds", []) if item.get("protocol", "").lower() == protocol), None)
    if inbound is None:
        return False, f"Inbound {protocol.upper()} introuvable."
    clients = inbound.setdefault("settings", {}).setdefault("clients", [])
    remaining_clients = [client for client in clients if client.get("email") != username]
    if len(remaining_clients) == len(clients):
        return False, "Compte introuvable."
    inbound["settings"]["clients"] = remaining_clients
    try:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except OSError as exc:
        return False, f"Écriture Xray impossible : {exc}"
    restarted = run(["systemctl", "restart", "xray"])
    if restarted.returncode:
        return False, f"Configuration modifiée, mais redémarrage Xray échoué : {restarted.stderr.strip()}"
    return True, f"🗑 Compte {protocol.upper()} <code>{escaped(username)}</code> supprimé."


def zivpn_create(username: str, password: str, days: int) -> tuple[bool, str, str]:
    if not USERNAME_RE.fullmatch(username) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", password):
        return False, "Nom ou mot de passe invalide.", ""
    path = Path(ZIVPN_DB)
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if any(line.split(":", 1)[0] == username for line in lines):
            return False, "Ce compte existe déjà.", ""
        expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{username}:{password}:{expiry}\n")
    except OSError as exc:
        return False, f"Écriture ZIVPN impossible : {exc}", ""
    restarted = run(["systemctl", "restart", "zivpn"])
    if restarted.returncode:
        return False, f"Compte écrit, mais redémarrage ZIVPN échoué : {restarted.stderr.strip()}", expiry
    return True, f"✅ ZIVPN créé : <code>{escaped(username)}</code>, expiration <code>{expiry}</code>.", expiry


def delete_zivpn(username: str) -> tuple[bool, str]:
    if not USERNAME_RE.fullmatch(username):
        return False, "Nom d'utilisateur invalide."
    path = Path(ZIVPN_DB)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        updated = [line for line in lines if line.split(":", 1)[0] != username]
        if len(updated) == len(lines):
            return False, "Compte introuvable."
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except OSError as exc:
        return False, f"Écriture ZIVPN impossible : {exc}"
    restarted = run(["systemctl", "restart", "zivpn"])
    if restarted.returncode:
        return False, f"Configuration modifiée, mais redémarrage ZIVPN échoué : {restarted.stderr.strip()}"
    return True, f"🗑 Compte ZIVPN <code>{escaped(username)}</code> supprimé."


def create_account(message: Any, protocol: str, username: str, days: int, password: str | None = None) -> None:
    if protocol == "ssh":
        ok, text, expiry = ssh_create(username, days)
    elif protocol in {"vless", "vmess", "trojan"}:
        ok, text, expiry = xray_add(protocol, username, days)
    elif protocol == "zivpn":
        ok, text, expiry = zivpn_create(username, password or secrets.token_urlsafe(12), days)
    else:
        bot.reply_to(message, "Protocole non pris en charge.")
        return
    bot.reply_to(message, text)
    if ok:
        audit_creation(message, protocol, username, expiry)


def start_text(message: Any) -> str:
    return (
        "🛡 <b>Thes — Surveillance et comptes VPN</b>\n\n"
        "<code>/resellers</code>, <code>/status</code>, <code>/check</code> — surveillance\n"
        "Création : <code>/ssh_create user jours</code>, <code>/vless_create user jours</code>,\n"
        "<code>/vmess_create user jours</code>, <code>/trojan_create user jours</code>,\n"
        "<code>/zivpn_create user motdepasse jours</code>\n"
        "Suppression : <code>/ssh_delete user</code>, <code>/xray_delete protocole user</code>,\n"
        "<code>/zivpn_delete user</code>\n"
        "Consultation et renouvellement : réservés au super administrateur.\n"
        "Gestion des rôles : <code>/admin_add id</code>, <code>/admin_remove id</code>,\n"
        "<code>/reseller_add id</code>, <code>/reseller_remove id</code>, <code>/roles</code> (super admin)."
    )


def menu_keyboard(uid: int) -> types.InlineKeyboardMarkup:
    role = role_for(uid)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🛡 Surveillance", callback_data="monitor"))
    if role in {"super_admin", "admin"}:
        kb.add(types.InlineKeyboardButton("➕ Créer SSH", callback_data="hint_ssh_create"),
               types.InlineKeyboardButton("🗑 Supprimer SSH", callback_data="hint_ssh_delete"))
        kb.add(types.InlineKeyboardButton("⚡ Créer XRAY", callback_data="hint_xray_create"),
               types.InlineKeyboardButton("🗑 Supprimer XRAY", callback_data="hint_xray_delete"))
        kb.add(types.InlineKeyboardButton("🌐 Créer ZIVPN", callback_data="hint_zivpn_create"),
               types.InlineKeyboardButton("🗑 Supprimer ZIVPN", callback_data="hint_zivpn_delete"))
    elif role == "reseller":
        kb.add(types.InlineKeyboardButton("➕ Créer SSH", callback_data="hint_ssh_create"),
               types.InlineKeyboardButton("⚡ Créer XRAY", callback_data="hint_xray_create"))
        kb.add(types.InlineKeyboardButton("🌐 Créer ZIVPN", callback_data="hint_zivpn_create"))
    if role == "super_admin":
        kb.add(types.InlineKeyboardButton("📋 Comptes", callback_data="accounts"),
               types.InlineKeyboardButton("👥 Rôles", callback_data="roles"))
    return kb


@bot.message_handler(commands=["start", "help", "menu"])
def start(message: Any) -> None:
    if role_for(message.from_user.id) is None:
        bot.reply_to(message, "⛔ Accès refusé.")
        return
    bot.send_message(message.chat.id, start_text(message), reply_markup=menu_keyboard(message.from_user.id))


@bot.message_handler(commands=["resellers"])
def reseller_subscriptions(message: Any) -> None:
    if role_for(message.from_user.id) is None:
        bot.reply_to(message, "⛔ Accès refusé."); return
    rows, now = scan_resellers(), datetime.now()
    lines = ["👥 <b>Surveillance des abonnements revendeurs</b>"]
    for rid, info, expiry in rows:
        value = "échéance inconnue" if expiry is None else f"{remaining(expiry, now)} — {expiry:%d/%m/%Y %H:%M}"
        lines.append(f"• {reseller_label(rid, info)}\n  ⏳ {escaped(value)}")
    bot.send_message(message.chat.id, "\n".join(lines) if rows else "Aucun revendeur enregistré.")


@bot.message_handler(commands=["status"])
def status(message: Any) -> None:
    if role_for(message.from_user.id) is None:
        bot.reply_to(message, "⛔ Accès refusé."); return
    rows, now = scan_resellers(), datetime.now()
    expired = sum(1 for _, _, exp in rows if exp and exp <= now)
    soon = sum(1 for _, _, exp in rows if exp and now < exp <= now + timedelta(hours=24))
    bot.send_message(message.chat.id, f"📊 Revendeurs suivis : <b>{len(rows)}</b>\n⚠️ Échéance sous 24 h : <b>{soon}</b>\n⛔ Expirés : <b>{expired}</b>")


@bot.message_handler(commands=["check"])
def check_now(message: Any) -> None:
    if role_for(message.from_user.id) is None:
        bot.reply_to(message, "⛔ Accès refusé."); return
    bot.reply_to(message, f"✅ Vérification terminée : {len(scan_resellers())} revendeur(s) contrôlé(s).")


@bot.message_handler(commands=["admin_add", "admin_remove", "reseller_add", "reseller_remove", "roles"])
def manage_roles(message: Any) -> None:
    if not is_super(message.from_user.id):
        bot.reply_to(message, "⛔ Seul le super administrateur peut gérer les rôles."); return
    parts = message.text.split()
    command = parts[0].split("@", 1)[0].lstrip("/")
    if command == "roles":
        state = load_state().get("telegram_roles", {})
        admins = sorted(get_role_ids("admin"))
        resellers = sorted(get_role_ids("reseller"))
        bot.reply_to(message, f"👥 <b>Rôles</b>\nAdministrateurs : <code>{escaped(admins)}</code>\nRevendeurs : <code>{escaped(resellers)}</code>")
        return
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, f"Usage: /{command} <telegram_id>"); return
    uid = int(parts[1])
    role = "admin" if command.startswith("admin_") else "reseller"
    changed = add_role(role, uid) if command.endswith("add") else remove_role(role, uid)
    bot.reply_to(message, f"✅ Rôle {role} mis à jour pour <code>{uid}</code>." if changed else "ℹ️ Aucun changement (ID déjà présent/absent ou super administrateur).")


@bot.message_handler(commands=["ssh_create", "vless_create", "vmess_create", "trojan_create"])
def create_simple_account(message: Any) -> None:
    role = role_for(message.from_user.id)
    if role not in {"super_admin", "admin", "reseller"}:
        bot.reply_to(message, "⛔ Accès refusé."); return
    parts = message.text.split()
    protocol = parts[0].split("@", 1)[0].lstrip("/").replace("_create", "")
    if len(parts) != 3:
        bot.reply_to(message, f"Usage: /{protocol}_create <username> <jours>"); return
    days = parse_days(parts[2])
    if days is None:
        bot.reply_to(message, "Nombre de jours invalide (1–3650)."); return
    create_account(message, protocol, parts[1], days)


@bot.message_handler(commands=["zivpn_create"])
def create_zivpn(message: Any) -> None:
    if role_for(message.from_user.id) not in {"super_admin", "admin", "reseller"}:
        bot.reply_to(message, "⛔ Accès refusé."); return
    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message, "Usage: /zivpn_create <username> <password> <jours>"); return
    days = parse_days(parts[3])
    if days is None:
        bot.reply_to(message, "Nombre de jours invalide (1–3650)."); return
    create_account(message, "zivpn", parts[1], days, parts[2])


@bot.message_handler(commands=["ssh_delete", "zivpn_delete"])
def delete_named_account(message: Any) -> None:
    role = role_for(message.from_user.id)
    command = message.text.split()[0].split("@", 1)[0].lstrip("/")
    allowed = {"super_admin", "admin"} if role != "reseller" else set()
    if role not in allowed:
        bot.reply_to(message, "⛔ Les revendeurs ne peuvent pas supprimer de comptes."); return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, f"Usage: /{command} <username>"); return
    ok, result = ssh_delete(parts[1]) if command == "ssh_delete" else delete_zivpn(parts[1])
    bot.reply_to(message, result)


@bot.message_handler(commands=["xray_delete"])
def delete_xray_command(message: Any) -> None:
    role = role_for(message.from_user.id)
    if role not in {"super_admin", "admin"}:
        bot.reply_to(message, "⛔ Les revendeurs ne peuvent pas supprimer de comptes."); return
    parts = message.text.split()
    if len(parts) != 3:
        bot.reply_to(message, "Usage: /xray_delete <vless|vmess|trojan> <username>"); return
    ok, result = xray_delete(parts[1], parts[2])
    bot.reply_to(message, result)


@bot.message_handler(commands=["ssh_list", "xray_list", "zivpn_list", "accounts"])
def list_accounts(message: Any) -> None:
    if not is_super(message.from_user.id):
        bot.reply_to(message, "⛔ Seul le super administrateur peut consulter les comptes."); return
    command = message.text.split()[0].split("@", 1)[0].lstrip("/")
    if command in {"ssh_list", "accounts"}:
        result = run(["awk", "-F:", '$3>=1000 && $1!="nobody" {print $1}', "/etc/passwd"]).stdout.strip()
        text = "📋 <b>Comptes SSH</b>\n" + ("\n".join(f"• <code>{escaped(item)}</code>" for item in result.splitlines()) or "Aucun")
    elif command == "xray_list":
        try:
            data = json.loads(Path(XRAY_CONFIG).read_text(encoding="utf-8"))
            rows = [f"• {escaped(inbound.get('protocol', '?'))}: <code>{escaped(client.get('email', '?'))}</code>" for inbound in data.get("inbounds", []) for client in inbound.get("settings", {}).get("clients", [])]
            text = "📋 <b>Comptes XRAY</b>\n" + ("\n".join(rows) if rows else "Aucun")
        except (OSError, json.JSONDecodeError) as exc:
            text = f"Lecture XRAY impossible : {escaped(exc)}"
    else:
        try:
            rows = [line.split(":", 1)[0] for line in Path(ZIVPN_DB).read_text(encoding="utf-8").splitlines() if ":" in line]
            text = "📋 <b>Comptes ZIVPN</b>\n" + ("\n".join(f"• <code>{escaped(item)}</code>" for item in rows) or "Aucun")
        except OSError as exc:
            text = f"Lecture ZIVPN impossible : {escaped(exc)}"
    bot.reply_to(message, text)


@bot.message_handler(commands=["ssh_renew", "xray_renew", "zivpn_renew"])
def renewal_super_only(message: Any) -> None:
    if not is_super(message.from_user.id):
        bot.reply_to(message, "⛔ Le renouvellement est réservé au super administrateur."); return
    bot.reply_to(message, "La commande de renouvellement doit être réalisée depuis le panneau super administrateur.")


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call: Any) -> None:
    uid = call.from_user.id
    if role_for(uid) is None:
        bot.answer_callback_query(call.id, "⛔ Accès refusé")
        return
    bot.answer_callback_query(call.id)
    if call.data == "monitor":
        bot.send_message(call.message.chat.id, "🛡 Commandes : /resellers, /status, /check")
    elif call.data == "accounts" and is_super(uid):
        bot.send_message(call.message.chat.id, "📋 Commandes : /ssh_list, /xray_list, /zivpn_list")
    elif call.data == "roles" and is_super(uid):
        bot.send_message(call.message.chat.id, "👥 Commandes super admin : /admin_add ID, /admin_remove ID, /reseller_add ID, /reseller_remove ID, /roles")
    elif call.data == "hint_ssh_create":
        bot.send_message(call.message.chat.id, "Envoyer /ssh_create <username> <jours>")
    elif call.data == "hint_ssh_delete" and role_for(uid) in {"super_admin", "admin"}:
        bot.send_message(call.message.chat.id, "Envoyer /ssh_delete <username>")
    elif call.data == "hint_xray_create":
        bot.send_message(call.message.chat.id, "Envoyer /vless_create, /vmess_create ou /trojan_create <username> <jours>")
    elif call.data == "hint_xray_delete" and role_for(uid) in {"super_admin", "admin"}:
        bot.send_message(call.message.chat.id, "Envoyer /xray_delete <vless|vmess|trojan> <username>")
    elif call.data == "hint_zivpn_create":
        bot.send_message(call.message.chat.id, "Envoyer /zivpn_create <username> <password> <jours>")
    elif call.data == "hint_zivpn_delete" and role_for(uid) in {"super_admin", "admin"}:
        bot.send_message(call.message.chat.id, "Envoyer /zivpn_delete <username>")


def monitor_loop() -> None:
    while True:
        try:
            scan_resellers()
        except Exception:
            logger.exception("Erreur lors de la surveillance périodique")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    rows = scan_resellers(notify_changes=False)
    notify_super(f"✅ Surveillance démarrée. {len(rows)} revendeur(s) suivis.")
    threading.Thread(target=monitor_loop, name="reseller-monitor", daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
