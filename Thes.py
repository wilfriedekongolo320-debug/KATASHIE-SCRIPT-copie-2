#!/usr/bin/env python3
"""Bot Telegram Thes : surveillance, comptes VPN et rôles à durée limitée."""
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
from datetime import datetime, timedelta, timezone
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
XRAY_CONFIG = Path(os.environ.get("THE_S_BOT_XRAY_CONFIG", "/etc/xray/config.json"))
ZIVPN_DB = Path(os.environ.get("THE_S_BOT_ZIVPN_DB", "/etc/zivpn/users.db"))
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


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def run(args: list[str], input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


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


def parse_expiry(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def get_role_records(role: str) -> dict[int, datetime | None]:
    """Returns active role assignments; legacy ID arrays remain permanent."""
    legacy = {"admin": ("admin_ids", "admins"), "reseller": ("reseller_ids", "resellers_ids")}
    records: dict[int, datetime | None] = {}
    for key in legacy.get(role, ()):
        for raw in config.get(key, []) if isinstance(config.get(key, []), list) else []:
            try:
                records[int(raw)] = None
            except (TypeError, ValueError):
                logger.warning("ID invalide dans %s", key)
    roles = load_state().get("telegram_roles", {})
    stored = roles.get(role, []) if isinstance(roles, dict) else []
    # Old role state used an array of IDs; new records map ID to expiry metadata.
    if isinstance(stored, list):
        for raw in stored:
            try:
                records.setdefault(int(raw), None)
            except (TypeError, ValueError):
                continue
    elif isinstance(stored, dict):
        for raw_uid, info in stored.items():
            try:
                uid = int(raw_uid)
            except (TypeError, ValueError):
                continue
            expiry = parse_expiry(info.get("expires_at")) if isinstance(info, dict) else None
            if expiry is not None and expiry <= datetime.now(timezone.utc):
                continue
            records[uid] = expiry
    return records


def role_for(uid: int) -> str | None:
    if uid == SUPER_ADMIN:
        return "super_admin"
    if uid in get_role_records("admin"):
        return "admin"
    if uid in get_role_records("reseller"):
        return "reseller"
    return None


def parse_days(raw: str) -> int | None:
    try:
        value = int(raw)
        return value if 1 <= value <= 3650 else None
    except (TypeError, ValueError):
        return None


def add_role(role: str, uid: int, days: int) -> tuple[bool, datetime | None]:
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    with state_lock:
        state = load_state()
        roles = state.setdefault("telegram_roles", {})
        if not isinstance(roles, dict):
            roles = state["telegram_roles"] = {}
        current = roles.get(role, [])
        entries: dict[str, Any] = {}
        if isinstance(current, list):
            for old_uid in current:
                try:
                    entries[str(int(old_uid))] = {"expires_at": None}
                except (TypeError, ValueError):
                    continue
        elif isinstance(current, dict):
            entries = current
        if uid == SUPER_ADMIN or uid in get_role_records(role):
            # Re-adding an active role renews its expiry, as explicitly requested by the super admin.
            if uid == SUPER_ADMIN:
                return False, None
        entries[str(uid)] = {"expires_at": expiry.isoformat(timespec="seconds"), "assigned_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        roles[role] = entries
        save_state(state)
    return True, expiry


def remove_role(role: str, uid: int) -> bool:
    with state_lock:
        state = load_state()
        roles = state.get("telegram_roles", {})
        if not isinstance(roles, dict):
            return False
        current = roles.get(role, [])
        if isinstance(current, list):
            updated = [str(value) for value in current if str(value) != str(uid)]
        elif isinstance(current, dict):
            updated = dict(current)
            updated.pop(str(uid), None)
        else:
            return False
        if updated == current:
            return False
        roles[role] = updated
        save_state(state)
    return True


def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN


def authorized(message: Any, roles: set[str] | None = None) -> bool:
    role = role_for(message.from_user.id)
    if role is None or (roles is not None and role not in roles):
        bot.reply_to(message, "⛔ Accès refusé pour votre rôle.")
        return False
    return True


def notify_super(text: str) -> None:
    try:
        bot.send_message(SUPER_ADMIN, text)
    except Exception:
        logger.exception("Notification impossible au super administrateur")


def reseller_expiry(info: dict[str, Any]) -> datetime | None:
    for key in ("expires_at", "expiry_datetime"):
        expiry = parse_expiry(info.get(key))
        if expiry:
            return expiry
    added = parse_expiry(info.get("added_datetime"))
    if added is None and isinstance(info.get("added_date"), str):
        added = parse_expiry(f"{info['added_date']}T00:00:00Z")
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


def load_resellers() -> dict[str, dict[str, Any]]:
    data = load_json(RESELLERS_FILE, {})
    if not isinstance(data, dict):
        logger.error("Le fichier revendeurs doit contenir un objet JSON.")
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def scan_resellers(notify_changes: bool = True) -> list[tuple[str, dict[str, Any], datetime | None]]:
    now = datetime.now(timezone.utc)
    resellers = load_resellers()
    with state_lock:
        state = load_state()
        ids = set(resellers)
        prior = state.get("known_resellers")
        if not isinstance(prior, list):
            state["known_resellers"] = sorted(ids)
        elif notify_changes:
            for rid in sorted(ids - set(map(str, prior))):
                info = resellers[rid]
                alias = esc(info.get("alias") or info.get("first_name") or rid)
                notify_super(f"🆕 Nouveau revendeur détecté : <b>{alias}</b> (ID <code>{esc(rid)}</code>).")
            for rid in sorted(set(map(str, prior)) - ids):
                notify_super(f"➖ Revendeur retiré : <code>{esc(rid)}</code>.")
            state["known_resellers"] = sorted(ids)
        alerts = state.setdefault("alerts", {})
        if not isinstance(alerts, dict):
            alerts = state["alerts"] = {}
        rows = []
        for rid, info in resellers.items():
            expiry = reseller_expiry(info)
            rows.append((rid, info, expiry))
            if expiry is None:
                continue
            key = expiry.isoformat(timespec="seconds")
            entry = alerts.get(rid)
            if not isinstance(entry, dict) or entry.get("expiry") != key:
                entry = {"expiry": key, "sent": []}
            sent = set(entry.get("sent", []))
            delta = expiry - now
            alias = esc(info.get("alias") or info.get("first_name") or rid)
            if delta.total_seconds() <= 0 and "expired" not in sent:
                notify_super(f"⛔ Abonnement expiré : <b>{alias}</b> (ID <code>{esc(rid)}</code>). Échéance : <code>{expiry:%d/%m/%Y %H:%M}</code>.")
                sent.add("expired")
            elif delta.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    alert_key = f"{hours}h"
                    if delta <= timedelta(hours=hours) and alert_key not in sent:
                        notify_super(f"⚠️ Échéance dans moins de {description} : <b>{alias}</b>. Temps restant : <b>{remaining(expiry, now)}</b>.")
                        sent.add(alert_key)
            entry["expiry"], entry["sent"] = key, sorted(sent)
            alerts[rid] = entry
        for rid in set(alerts) - ids:
            alerts.pop(rid, None)
        save_state(state)
    return rows


def audit_creation(message: Any, protocol: str, username: str, expiry: str) -> None:
    user = message.from_user
    actor = " ".join(p for p in (user.first_name, user.last_name) if p) or user.username or str(user.id)
    now = datetime.now().astimezone()
    notify_super(
        "🆕 <b>Compte VPN créé</b>\n"
        f"👤 Auteur : <code>{esc(actor)}</code> (ID <code>{user.id}</code>, {esc(role_for(user.id))})\n"
        f"⚙️ Protocole : <code>{esc(protocol.upper())}</code>\n"
        f"🧾 Compte : <code>{esc(username)}</code>\n"
        f"📅 Expiration : <code>{esc(expiry)}</code>\n"
        f"🕒 Création : <code>{now:%d/%m/%Y %H:%M:%S %Z}</code>"
    )


def atomic_json(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)


def create_ssh(username: str, days: int) -> tuple[bool, str, str]:
    if not USERNAME_RE.fullmatch(username):
        return False, "Nom d'utilisateur invalide.", ""
    if run(["id", username]).returncode == 0:
        return False, "Ce compte existe déjà.", ""
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    result = run(["useradd", "-e", expiry, "-s", "/bin/false", "-M", username])
    if result.returncode:
        return False, result.stderr.strip() or "Échec de création SSH.", ""
    password = secrets.token_urlsafe(12)
    result = run(["chpasswd"], f"{username}:{password}\n")
    if result.returncode:
        run(["userdel", "-r", username])
        return False, result.stderr.strip() or "Échec de définition du mot de passe.", ""
    return True, f"✅ SSH créé : <code>{esc(username)}</code>, mot de passe <code>{esc(password)}</code>, expiration <code>{expiry}</code>.", expiry


def create_xray(protocol: str, username: str, days: int) -> tuple[bool, str, str]:
    protocol = protocol.lower()
    if protocol not in {"vless", "vmess", "trojan"} or not USERNAME_RE.fullmatch(username):
        return False, "Protocole ou nom d'utilisateur invalide.", ""
    try:
        data = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Lecture Xray impossible : {exc}", ""
    inbound = next((i for i in data.get("inbounds", []) if i.get("protocol", "").lower() == protocol), None)
    if inbound is None:
        return False, f"Inbound {protocol.upper()} introuvable.", ""
    clients = inbound.setdefault("settings", {}).setdefault("clients", [])
    if any(c.get("email") == username for c in clients):
        return False, "Ce compte existe déjà.", ""
    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    credential = str(uuid.uuid4()) if protocol != "trojan" else secrets.token_urlsafe(18)
    client = {"id": credential, "email": username} if protocol != "trojan" else {"password": credential, "email": username}
    client["expiryDate"] = expiry
    clients.append(client)
    try:
        atomic_json(XRAY_CONFIG, data)
    except OSError as exc:
        return False, f"Écriture Xray impossible : {exc}", ""
    restarted = run(["systemctl", "restart", "xray"])
    if restarted.returncode:
        return False, f"Configuration écrite mais redémarrage Xray échoué : {restarted.stderr.strip()}", expiry
    domain = run(["bash", "-lc", "cat /etc/xray/domain 2>/dev/null || hostname -I | awk '{print $1}'"]).stdout.strip() or "serveur"
    if protocol == "vless":
        connection = f"vless://{credential}@{domain}:443?encryption=none&security=tls&type=tcp#{username}"
    elif protocol == "vmess":
        payload = {"v":"2", "ps":username, "add":domain, "port":"443", "id":credential, "aid":"0", "net":"tcp", "type":"none", "host":"", "path":"", "tls":"tls"}
        connection = "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()
    else:
        connection = f"trojan://{credential}@{domain}:443#{username}"
    return True, f"✅ {protocol.upper()} créé : <code>{esc(username)}</code>\nIdentifiant : <code>{esc(credential)}</code>\nExpiration : <code>{expiry}</code>\nLien : <code>{esc(connection)}</code>", expiry


def create_zivpn(username: str, password: str, days: int) -> tuple[bool, str, str]:
    if not USERNAME_RE.fullmatch(username) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", password):
        return False, "Nom ou mot de passe invalide.", ""
    try:
        rows = ZIVPN_DB.read_text(encoding="utf-8").splitlines() if ZIVPN_DB.exists() else []
        if any(row.split(":", 1)[0] == username for row in rows):
            return False, "Ce compte existe déjà.", ""
        expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        ZIVPN_DB.parent.mkdir(parents=True, exist_ok=True)
        with ZIVPN_DB.open("a", encoding="utf-8") as handle:
            handle.write(f"{username}:{password}:{expiry}\n")
    except OSError as exc:
        return False, f"Écriture ZIVPN impossible : {exc}", ""
    restarted = run(["systemctl", "restart", "zivpn"])
    if restarted.returncode:
        return False, f"Compte écrit mais redémarrage ZIVPN échoué : {restarted.stderr.strip()}", expiry
    return True, f"✅ ZIVPN créé : <code>{esc(username)}</code>, expiration <code>{expiry}</code>.", expiry


def delete_account(protocol: str, username: str) -> tuple[bool, str]:
    if not USERNAME_RE.fullmatch(username):
        return False, "Nom d'utilisateur invalide."
    if protocol == "ssh":
        result = run(["userdel", "-r", username])
        return (True, f"🗑 SSH <code>{esc(username)}</code> supprimé.") if not result.returncode else (False, result.stderr.strip() or "Compte SSH introuvable.")
    if protocol in {"vless", "vmess", "trojan"}:
        try:
            data = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
            inbound = next((i for i in data.get("inbounds", []) if i.get("protocol", "").lower() == protocol), None)
            if inbound is None:
                return False, f"Inbound {protocol.upper()} introuvable."
            clients = inbound.setdefault("settings", {}).setdefault("clients", [])
            updated = [c for c in clients if c.get("email") != username]
            if len(updated) == len(clients):
                return False, "Compte introuvable."
            inbound["settings"]["clients"] = updated
            atomic_json(XRAY_CONFIG, data)
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"Modification Xray impossible : {exc}"
        result = run(["systemctl", "restart", "xray"])
        return (True, f"🗑 {protocol.upper()} <code>{esc(username)}</code> supprimé.") if not result.returncode else (False, result.stderr.strip())
    if protocol == "zivpn":
        try:
            rows = ZIVPN_DB.read_text(encoding="utf-8").splitlines()
            updated = [row for row in rows if row.split(":", 1)[0] != username]
            if len(updated) == len(rows):
                return False, "Compte introuvable."
            temp = ZIVPN_DB.with_suffix(".tmp")
            temp.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
            os.chmod(temp, ZIVPN_DB.stat().st_mode & 0o777)
            os.replace(temp, ZIVPN_DB)
        except OSError as exc:
            return False, f"Modification ZIVPN impossible : {exc}"
        result = run(["systemctl", "restart", "zivpn"])
        return (True, f"🗑 ZIVPN <code>{esc(username)}</code> supprimé.") if not result.returncode else (False, result.stderr.strip())
    return False, "Protocole non pris en charge."


def list_accounts(protocol: str) -> str:
    if protocol == "ssh":
        output = run(["awk", "-F:", '$3>=1000 && $1!="nobody" {print $1}', "/etc/passwd"]).stdout.strip()
        rows = output.splitlines()
    elif protocol == "zivpn":
        try:
            rows = [line.split(":", 1)[0] for line in ZIVPN_DB.read_text(encoding="utf-8").splitlines() if ":" in line]
        except OSError:
            rows = []
    else:
        try:
            data = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
            rows = [f"{i.get('protocol', '?')}: {c.get('email', '?')}" for i in data.get("inbounds", []) for c in i.get("settings", {}).get("clients", [])]
        except (OSError, json.JSONDecodeError):
            rows = []
    return f"📋 <b>{protocol.upper()} — comptes configurés</b>\n" + ("\n".join(f"• <code>{esc(row)}</code>" for row in rows) if rows else "Aucun compte trouvé.")


def actor_name(message: Any) -> str:
    user = message.from_user
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.username or str(user.id)


def show_menu(message: Any) -> None:
    role = role_for(message.from_user.id)
    caption = "🛡 <b>Thes — Surveillance et gestion VPN</b>\nChoisissez une option. Les rôles expirent à la durée définie par le super administrateur."
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(types.InlineKeyboardButton("👥 Revendeurs", callback_data="resellers"), types.InlineKeyboardButton("📊 Statut", callback_data="status"))
    if role in {"super_admin", "admin"}:
        keyboard.add(types.InlineKeyboardButton("➕ Créer VPN", callback_data="create_hint"), types.InlineKeyboardButton("🗑 Supprimer VPN", callback_data="delete_hint"))
    elif role == "reseller":
        keyboard.add(types.InlineKeyboardButton("➕ Créer VPN", callback_data="create_hint"))
    if role == "super_admin":
        keyboard.add(types.InlineKeyboardButton("📋 Comptes", callback_data="accounts"), types.InlineKeyboardButton("👤 Gérer les rôles", callback_data="roles"))
    if MENU_IMAGE_FILE.is_file():
        try:
            with MENU_IMAGE_FILE.open("rb") as photo:
                bot.send_photo(message.chat.id, photo, caption=caption, reply_markup=keyboard)
            return
        except Exception:
            logger.exception("Image de menu indisponible")
    bot.send_message(message.chat.id, caption, reply_markup=keyboard)


@bot.message_handler(commands=["start", "help", "menu"])
def start(message: Any) -> None:
    if not authorized(message):
        return
    show_menu(message)


@bot.message_handler(commands=["admin_add", "admin_remove", "reseller_add", "reseller_remove", "roles"])
def manage_roles(message: Any) -> None:
    if not is_super(message.from_user.id):
        bot.reply_to(message, "⛔ Seul le super administrateur peut gérer les rôles.")
        return
    parts = message.text.split()
    command = parts[0].split("@", 1)[0].lstrip("/")
    if command == "roles":
        output = ["👥 <b>Rôles attribués</b>"]
        for role, title in (("admin", "Administrateurs"), ("reseller", "Revendeurs")):
            records = get_role_records(role)
            output.append(f"<b>{title}</b>")
            for uid, expiry in sorted(records.items()):
                deadline = "sans expiration (ancienne configuration)" if expiry is None else expiry.astimezone().strftime("%d/%m/%Y %H:%M %Z")
                output.append(f"• <code>{uid}</code> — expire : <code>{esc(deadline)}</code>")
            if not records:
                output.append("• Aucun")
        bot.reply_to(message, "\n".join(output))
        return
    if command.endswith("_remove"):
        if len(parts) != 2 or not parts[1].isdigit():
            bot.reply_to(message, f"Usage : /{command} <telegram_id>")
            return
        role = "admin" if command.startswith("admin_") else "reseller"
        changed = remove_role(role, int(parts[1]))
        bot.reply_to(message, "✅ Rôle retiré." if changed else "ℹ️ Rôle introuvable.")
        return
    if len(parts) != 3 or not parts[1].isdigit():
        bot.reply_to(message, f"Usage : /{command} <telegram_id> <jours> (1–3650)")
        return
    days = parse_days(parts[2])
    if days is None:
        bot.reply_to(message, "Durée invalide. Choisissez entre 1 et 3650 jours.")
        return
    role = "admin" if command.startswith("admin_") else "reseller"
    changed, expiry = add_role(role, int(parts[1]), days)
    if not changed:
        bot.reply_to(message, "⛔ Le super administrateur ne peut pas être ajouté à un autre rôle.")
        return
    bot.reply_to(message, f"✅ Rôle {role} attribué à <code>{parts[1]}</code> pour <b>{days} jour(s)</b>, jusqu’au <code>{expiry.astimezone():%d/%m/%Y %H:%M %Z}</code>.")


@bot.message_handler(commands=["resellers"])
def reseller_list(message: Any) -> None:
    if not authorized(message):
        return
    now = datetime.now(timezone.utc)
    rows = scan_resellers()
    lines = ["👥 <b>Surveillance des abonnements revendeurs</b>"]
    for rid, info, expiry in rows:
        alias = esc(info.get("alias") or info.get("first_name") or rid)
        time_left = "échéance inconnue" if expiry is None else f"{remaining(expiry, now)} — {expiry.astimezone():%d/%m/%Y %H:%M}"
        lines.append(f"• <b>{alias}</b> (ID <code>{esc(rid)}</code>)\n  ⏳ {esc(time_left)}")
    bot.reply_to(message, "\n".join(lines) if rows else "Aucun revendeur suivi.")


@bot.message_handler(commands=["status", "check"])
def monitor_status(message: Any) -> None:
    if not authorized(message):
        return
    rows = scan_resellers()
    if message.text.split()[0].lstrip("/").split("@", 1)[0] == "check":
        bot.reply_to(message, f"✅ Vérification terminée : {len(rows)} revendeur(s) contrôlé(s).")
        return
    now = datetime.now(timezone.utc)
    expired = sum(1 for _, _, exp in rows if exp is not None and exp <= now)
    soon = sum(1 for _, _, exp in rows if exp is not None and now < exp <= now + timedelta(hours=24))
    bot.reply_to(message, f"📊 Revendeurs : <b>{len(rows)}</b>\n⚠️ Échéance sous 24 h : <b>{soon}</b>\n⛔ Expirés : <b>{expired}</b>")


@bot.message_handler(commands=["ssh_create", "vless_create", "vmess_create", "trojan_create"])
def create_simple(message: Any) -> None:
    if not authorized(message, {"super_admin", "admin", "reseller"}):
        return
    parts = message.text.split()
    protocol = parts[0].split("@", 1)[0].lstrip("/").replace("_create", "")
    if len(parts) != 3:
        bot.reply_to(message, f"Usage : /{protocol}_create <username> <jours>")
        return
    days = parse_days(parts[2])
    if days is None:
        bot.reply_to(message, "Durée invalide (1–3650 jours).")
        return
    if protocol == "ssh":
        ok, text, expiry = create_ssh(parts[1], days)
    else:
        ok, text, expiry = create_xray(protocol, parts[1], days)
    bot.reply_to(message, text)
    if ok:
        audit_creation(message, protocol, parts[1], expiry)


@bot.message_handler(commands=["zivpn_create"])
def create_zivpn(message: Any) -> None:
    if not authorized(message, {"super_admin", "admin", "reseller"}):
        return
    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message, "Usage : /zivpn_create <username> <password> <jours>")
        return
    days = parse_days(parts[3])
    if days is None:
        bot.reply_to(message, "Durée invalide (1–3650 jours).")
        return
    ok, text, expiry = create_zivpn(parts[1], parts[2], days)
    bot.reply_to(message, text)
    if ok:
        audit_creation(message, "zivpn", parts[1], expiry)


@bot.message_handler(commands=["ssh_delete", "zivpn_delete"])
def delete_named(message: Any) -> None:
    if not authorized(message, {"super_admin", "admin"}):
        return
    parts = message.text.split()
    command = parts[0].split("@", 1)[0].lstrip("/")
    if len(parts) != 2:
        bot.reply_to(message, f"Usage : /{command} <username>")
        return
    protocol = "ssh" if command == "ssh_delete" else "zivpn"
    _, text = delete_account(protocol, parts[1])
    bot.reply_to(message, text)


@bot.message_handler(commands=["xray_delete"])
def delete_xray(message: Any) -> None:
    if not authorized(message, {"super_admin", "admin"}):
        return
    parts = message.text.split()
    if len(parts) != 3:
        bot.reply_to(message, "Usage : /xray_delete <vless|vmess|trojan> <username>")
        return
    _, text = delete_account(parts[1].lower(), parts[2])
    bot.reply_to(message, text)


@bot.message_handler(commands=["ssh_list", "xray_list", "zivpn_list", "accounts"])
def account_list(message: Any) -> None:
    if not authorized(message, {"super_admin"}):
        return
    cmd = message.text.split()[0].split("@", 1)[0].lstrip("/")
    if cmd == "accounts":
        text = "\n\n".join(list_accounts(proto) for proto in ("ssh", "xray", "zivpn"))
    else:
        text = list_accounts({"ssh_list": "ssh", "xray_list": "xray", "zivpn_list": "zivpn"}[cmd])
    bot.reply_to(message, text)


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call: Any) -> None:
    role = role_for(call.from_user.id)
    if role is None:
        bot.answer_callback_query(call.id, "⛔ Accès refusé")
        return
    bot.answer_callback_query(call.id)
    if call.data == "resellers":
        bot.send_message(call.message.chat.id, "Commandes : /resellers, /status, /check")
    elif call.data == "status":
        bot.send_message(call.message.chat.id, "Utilisez /status pour le résumé des échéances.")
    elif call.data == "accounts" and role == "super_admin":
        bot.send_message(call.message.chat.id, "Comptes : /ssh_list, /xray_list, /zivpn_list")
    elif call.data == "roles" and role == "super_admin":
        bot.send_message(call.message.chat.id, "Ajouter (jours requis) : /admin_add ID JOURS ou /reseller_add ID JOURS. Retirer : /admin_remove ID ou /reseller_remove ID. Voir : /roles.")
    elif call.data == "create_hint":
        bot.send_message(call.message.chat.id, "/ssh_create user jours, /vless_create user jours, /vmess_create user jours, /trojan_create user jours ou /zivpn_create user motdepasse jours")
    elif call.data == "delete_hint" and role in {"super_admin", "admin"}:
        bot.send_message(call.message.chat.id, "/ssh_delete user, /xray_delete protocole user ou /zivpn_delete user")


def monitor_loop() -> None:
    while True:
        try:
            scan_resellers()
        except Exception:
            logger.exception("Erreur de surveillance périodique")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    rows = scan_resellers(notify_changes=False)
    notify_super(f"✅ Surveillance démarrée. {len(rows)} revendeur(s) suivis.")
    threading.Thread(target=monitor_loop, name="reseller-monitor", daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
