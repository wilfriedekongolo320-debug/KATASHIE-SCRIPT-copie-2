#!/usr/bin/env python3
"""Bot Telegram : surveillance revendeurs + gestion comptes VPN/SSH."""
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


def escaped(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _run(cmd: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (result.stdout + result.stderr).strip()
    except Exception as exc:
        return str(exc)


config = load_json(CONFIG_FILE, {})
if not isinstance(config, dict):
    raise SystemExit(f"Configuration invalide : {CONFIG_FILE}")
BOT_TOKEN = str(config.get("bot_token", "")).strip()
try:
    SUPER_ADMIN = int(config.get("super_admin", 0))
except (TypeError, ValueError):
    SUPER_ADMIN = 0
ADMIN_IDS = []
for key in ("admin_ids", "admins"):
    raw = config.get(key, [])
    if isinstance(raw, list):
        for item in raw:
            try:
                ADMIN_IDS.append(int(item))
            except (TypeError, ValueError):
                continue
ADMIN_IDS = list(dict.fromkeys([SUPER_ADMIN, *ADMIN_IDS]))
if not BOT_TOKEN or not SUPER_ADMIN:
    raise SystemExit(f"Renseignez bot_token et super_admin dans {CONFIG_FILE}")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
state_lock = threading.Lock()


def is_admin(uid: int) -> bool:
    return uid == SUPER_ADMIN or uid in ADMIN_IDS


def authorized(message: Any, require_super: bool = False) -> bool:
    allowed = is_admin(message.from_user.id)
    if require_super and message.from_user.id != SUPER_ADMIN:
        allowed = False
    if not allowed:
        bot.reply_to(message, "⛔ Commande réservée à l’administrateur.")
        return False
    return True


def load_state() -> dict[str, Any]:
    state = load_json(STATE_FILE, {})
    return state if isinstance(state, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, STATE_FILE)


def load_resellers() -> dict[str, dict[str, Any]]:
    data = load_json(RESELLERS_FILE, {})
    if not isinstance(data, dict):
        logger.error("Le fichier revendeurs doit contenir un objet JSON.")
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def send_admin(text: str) -> None:
    try:
        bot.send_message(SUPER_ADMIN, text)
    except Exception:
        logger.exception("Notification impossible au super administrateur")


def label(reseller_id: str, info: dict[str, Any]) -> str:
    alias = escaped(info.get("alias") or info.get("first_name") or reseller_id)
    kind = escaped(info.get("type", "inconnu")).upper()
    return f"<b>{alias}</b> (ID <code>{escaped(reseller_id)}</code>, {kind})"


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
            elif delta.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    key = f"{hours}h"
                    if delta <= timedelta(hours=hours) and key not in sent:
                        send_admin(f"⚠️ Échéance dans moins de {description} : {label(rid, info)}. Temps restant : <b>{remaining(expiry, now)}</b> (échéance <code>{expiry:%d/%m/%Y %H:%M}</code>).")
                        sent.add(key)
            entry["expiry"], entry["sent"] = expiry_key, sorted(sent)
            alerts[rid] = entry
        for rid in set(alerts) - current_ids:
            alerts.pop(rid, None)
        save_state(state)
    return rows


def show_menu(chat_id: int) -> None:
    caption = (
        "🛡 <b>Surveillance + Gestion VPN</b>\n\n"
        "Bot unifié KATASHIE / Thes.\n\n"
        "<code>/resellers</code> — Liste et temps restant\n"
        "<code>/status</code> — Résumé des échéances\n"
        "<code>/check</code> — Vérifier maintenant\n\n"
        "<code>/ssh_create &lt;user&gt; &lt;days&gt;</code>\n"
        "<code>/ssh_list</code> — comptes SSH\n"
        "<code>/ssh_delete &lt;user&gt;</code>\n\n"
        "<code>/vless_create &lt;user&gt; &lt;days&gt;</code>\n"
        "<code>/vmess_create &lt;user&gt; &lt;days&gt;</code>\n"
        "<code>/trojan_create &lt;user&gt; &lt;days&gt;</code>\n"
        "<code>/xray_list</code> — comptes XRAY\n\n"
        "<code>/zivpn_create &lt;user&gt; &lt;pass&gt; &lt;days&gt;</code>\n"
        "<code>/zivpn_list</code> — comptes ZIVPN\n"
        "<code>/zivpn_delete &lt;user&gt;</code>"
    )
    if MENU_IMAGE_FILE.is_file():
        try:
            with MENU_IMAGE_FILE.open("rb") as photo:
                bot.send_photo(chat_id, photo, caption=caption)
            return
        except Exception:
            logger.exception("Impossible d'afficher l'image de menu %s", MENU_IMAGE_FILE)
    bot.send_message(chat_id, caption)


# ─── SSH helpers ───────────────────���───────────────────────────────────────────


def create_ssh_account(username: str, password: str, days: int) -> tuple[bool, str]:
    if not re.match(r"^[a-zA-Z0-9_]{1,32}$", username):
        return False, "❌ Nom d'utilisateur invalide. Lettres, chiffres, underscore uniquement."
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."
    if subprocess.run(["id", username], capture_output=True, check=False).returncode == 0:
        return False, f"❌ L'utilisateur <code>{username}</code> existe déjà."

    exp_date = _run(f"date -d '+{days} days' +%Y-%m-%d").strip()
    r = subprocess.run(["useradd", "-e", exp_date, "-s", "/bin/false", "-M", username], capture_output=True)
    if r.returncode != 0:
        return False, f"❌ Erreur useradd: {r.stderr.decode().strip() or 'inconnue'}"
    subprocess.run(["chpasswd"], input=f"{username}:{password}", text=True, capture_output=True)

    domain = _run("cat /etc/xray/domain 2>/dev/null || echo 'N/A'")
    ip = _run("curl -s4 https://ipv4.icanhazip.com 2>/dev/null || echo 'N/A'")
    dns = _run("cat /etc/slowdns/nsdomain 2>/dev/null || echo 'N/A'")
    pub = _run("cat /etc/slowdns/server.pub 2>/dev/null || echo 'N/A'")
    return True, (
        f"✅ <b>Compte SSH Créé</b>\n\n"
        f"👤 Utilisateur : <code>{username}</code>\n"
        f"🔑 Mot de passe: <code>{password}</code>\n"
        f"📅 Expiration  : <code>{exp_date}</code>\n"
        f"🌐 Domaine     : <code>{domain}</code>\n"
        f"📡 IP          : <code>{ip}</code>\n"
        f"🔗 NS Domaine  : <code>{dns}</code>\n\n"
        f"<b>Ports:</b>\n"
        f"• OpenSSH: 22\n"
        f"• Dropbear: 109, 143\n"
        f"• Stunnel: 447, 777\n"
        f"• WS TLS: 443 / NTLS: 80, 8880\n"
        f"• UDPGW: 7100–7900\n\n"
        f"<b>UDP Custom:</b>\n<code>{domain}:1-65535@{username}:{password}</code>\n\n"
        f"<b>Slow DNS PUB:</b>\n<code>{pub}</code>"
    )


def renew_ssh_account(username: str, days: int) -> tuple[bool, str]:
    if subprocess.run(["id", username], capture_output=True, check=False).returncode != 0:
        return False, f"❌ L'utilisateur <code>{username}</code> n'existe pas."
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."

    current_exp = _run(f"chage -l {username} | grep 'Account expires' | awk -F': ' '{{print $2}}'")
    if current_exp == "never":
        old = _run("date +%s")
    else:
        old = _run(f"date -d '{current_exp} +1 day' +%s")
    new_ts = int(old) + int(days) * 86400
    new_exp = _run(f"date --date='1970-01-01 {new_ts} sec' +%Y-%m-%d")
    _run(f"usermod -e {new_exp} {username}")
    _run(f"passwd -u {username}")
    return True, (
        f"🔄 <b>Compte SSH Renouvelé</b>\n\n"
        f"👤 Utilisateur      : <code>{username}</code>\n"
        f"📅 Ancienne exp.    : <code>{current_exp}</code>\n"
        f"➕ Jours ajoutés   : {days}\n"
        f"📅 Nouvelle exp.    : <code>{new_exp}</code>"
    )


def delete_ssh_account(username: str) -> tuple[bool, str]:
    _run(f"pkill -u {username}")
    r = subprocess.run(["userdel", "-r", username], capture_output=True)
    if r.returncode == 0:
        return True, f"🗑️ <b>Compte SSH supprimé:</b> <code>{username}</code>"
    return False, f"❌ Erreur: {r.stderr.decode().strip() or 'inconnue'}"


def list_ssh_accounts() -> str:
    out = _run("awk -F: '$3>=1000 && $1!=\"nobody\" {print $1}' /etc/passwd")
    users = [u for u in out.splitlines() if u.strip()]
    if not users:
        return "📋 <b>Comptes SSH</b>\n\nAucun compte trouvé."
    lines = ["📋 <b>Comptes SSH</b>"]
    for user in users:
        lines.append(f"• <code>{user}</code>")
    return "\n".join(lines)


def get_expiring_ssh(hours: int = 24) -> list[str]:
    users = [u for u in list_ssh_accounts().splitlines() if u.strip().startswith("•")]
    if not users:
        return []
    out = []
    now = int(_run("date +%s"))
    for user in [line.split("<code>", 1)[1].split("</code>", 1)[0] for line in users if "<code>" in line]:
        chage_out = _run(f"chage -l {user}")
        match = re.search(r"Account expires\s*:\s*(.*)", chage_out)
        if not match:
            continue
        raw = match.group(1).strip()
        if raw in ("never", "Never"):
            continue
        try:
            ts = int(_run(f"date -d '{raw}' +%s"))
            delta = ts - now
            if 0 <= delta <= int(hours) * 3600:
                out.append(user)
        except Exception:
            continue
    return out


# ─── XRAY helpers ──────────────────────────────────────────────────────────────


def create_xray_account(protocol: str, username: str, days: int) -> tuple[bool, str]:
    proto = protocol.lower()
    if proto not in {"vless", "vmess", "trojan"}:
        return False, "❌ Protocole XRAY invalide. Utilisez vless, vmess ou trojan."
    if not re.match(r"^[a-zA-Z0-9_]{1,32}$", username):
        return False, "❌ Nom d'utilisateur invalide."
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."
    if not os.path.exists(XRAY_CONFIG):
        return False, f"❌ Fichier XRAY introuvable : {XRAY_CONFIG}"

    existing = _run(f"grep -w '{username}' {XRAY_CONFIG} | wc -l")
    if int(existing or 0) > 0:
        return False, f"❌ L'utilisateur <code>{username}</code> existe déjà en {proto.upper()}."

    uid = str(uuid.uuid4())
    exp = _run(f"date -d '+{days} days' +%Y-%m-%d").strip()
    domain = _run("cat /etc/xray/domain 2>/dev/null || echo 'N/A'").strip()

    _run(
        f"sed -i '/#{proto}$/a\\#& {username} {exp} {uid}\\n"
        f",{{\"id\": \"{uid}\",\"email\": \"{username}\"' {XRAY_CONFIG} && systemctl restart xray"
    )

    if proto == "vless":
        link_tls = f"vless://{uid}@{domain}:443?path=/vless&security=tls&encryption=none&type=ws#{username}"
        link_ntls = f"vless://{uid}@{domain}:80?path=/vless&encryption=none&type=ws#{username}"
        links = f"TLS : <code>{link_tls}</code>\n\nNTLS: <code>{link_ntls}</code>"
    elif proto == "vmess":
        vmess_obj = json.dumps({
            "v": "2", "ps": username, "add": domain, "port": "443", "id": uid,
            "aid": "0", "net": "ws", "type": "none", "host": domain,
            "path": "/vmess", "tls": "tls"
        })
        link = "vmess://" + base64.b64encode(vmess_obj.encode()).decode()
        links = f"<code>{link}</code>"
    else:
        link_tls = f"trojan://{uid}@{domain}:443?type=ws&host={domain}&path=/trojan#{username}"
        links = f"TLS : <code>{link_tls}</code>"

    return True, (
        f"✅ <b>Compte {proto.upper()} Créé</b>\n\n"
        f"👤 Utilisateur : <code>{username}</code>\n"
        f"🔑 UUID        : <code>{uid}</code>\n"
        f"📅 Expiration  : <code>{exp}</code>\n"
        f"🌐 Domaine     : <code>{domain}</code>\n\n"
        f"<b>Liens de connexion:</b>\n{links}"
    )


def renew_xray_account(protocol: str, username: str, days: int) -> tuple[bool, str]:
    proto = protocol.lower()
    if proto not in {"vless", "vmess", "trojan"}:
        return False, "❌ Protocole XRAY invalide."
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."
    old_exp = _run(f"grep -E '^#& {username} ' {XRAY_CONFIG} | awk '{{print $3}}' | sort -u | head -1").strip()
    if not old_exp:
        return False, f"❌ Utilisateur <code>{username}</code> introuvable en {proto.upper()}."
    uid = _run(f"grep -E '^#& {username} ' {XRAY_CONFIG} | awk '{{print $4}}' | sort -u | head -1").strip()
    today = _run("date +%Y-%m-%d").strip()
    d1 = int(_run(f"date -d '{old_exp}' +%s"))
    d2 = int(_run(f"date -d '{today}' +%s"))
    remaining = max(0, (d1 - d2) // 86400)
    new_total = remaining + int(days)
    new_exp = _run(f"date -d '+{new_total} days' +%Y-%m-%d").strip()
    _run(f"sed -i '/^#& {username} /c\\#& {username} {new_exp} {uid}' {XRAY_CONFIG} && systemctl restart xray")
    return True, (
        f"🔄 <b>{proto.upper()} Renouvelé</b>\n\n"
        f"👤 Utilisateur   : <code>{username}</code>\n"
        f"📅 Ancienne exp. : <code>{old_exp}</code>\n"
        f"➕ Jours ajoutés : {days}\n"
        f"📅 Nouvelle exp. : <code>{new_exp}</code>"
    )


def delete_xray_account(protocol: str, username: str) -> tuple[bool, str]:
    proto = protocol.lower()
    _run(f"sed -i '/^#& {username} /d; /\"email\": \"{username}\"/d' {XRAY_CONFIG} && systemctl restart xray")
    return True, f"🗑️ <b>Compte {proto.upper()} supprimé:</b> <code>{username}</code>"


def list_xray_accounts() -> str:
    out = _run(f"grep '^#& ' {XRAY_CONFIG} || true")
    if not out:
        return "📋 <b>Comptes XRAY</b>\n\nAucun compte trouvé."
    lines = ["📋 <b>Comptes XRAY</b>"]
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            lines.append(f"• <code>{parts[1]}</code> — exp: <code>{parts[2]}</code>")
    return "\n".join(lines)


# ─── ZIVPN helpers ──────────────────────────────────────────────────────────────


def create_zivpn_account(username: str, password: str, days: int) -> tuple[bool, str]:
    if not re.match(r"^[a-zA-Z0-9_]{1,32}$", username):
        return False, "❌ Nom d'utilisateur invalide."
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."
    exp = _run(f"date -d '+{days} days' +%Y-%m-%d").strip()
    _run(f"echo '{username}:{password}:{exp}' >> {ZIVPN_DB} && systemctl restart zivpn")
    return True, (
        f"✅ <b>Compte ZIVPN Créé</b>\n\n"
        f"👤 Utilisateur : <code>{username}</code>\n"
        f"🔑 Mot de passe: <code>{password}</code>\n"
        f"📅 Expiration  : <code>{exp}</code>"
    )


def renew_zivpn_account(username: str, days: int) -> tuple[bool, str]:
    if not str(days).isdigit():
        return False, "❌ Nombre de jours invalide."
    _run(f"sed -i '/^{username}:/d' {ZIVPN_DB}")
    exp = _run(f"date -d '+{days} days' +%Y-%m-%d").strip()
    password = "renouvele"
    _run(f"echo '{username}:{password}:{exp}' >> {ZIVPN_DB} && systemctl restart zivpn")
    return True, f"🔄 Compte ZIVPN <code>{username}</code> renouvelé jusqu'au <code>{exp}</code>."


def delete_zivpn_account(username: str) -> tuple[bool, str]:
    _run(f"sed -i '/^{username}:/d' {ZIVPN_DB} && systemctl restart zivpn")
    return True, f"🗑️ Compte ZIVPN <code>{username}</code> supprimé."


def list_zivpn_accounts() -> str:
    if not os.path.exists(ZIVPN_DB):
        return "📋 <b>Comptes ZIVPN</b>\n\nAucun compte trouvé."
    out = _run(f"cat {ZIVPN_DB} 2>/dev/null || true")
    if not out.strip():
        return "📋 <b>Comptes ZIVPN</b>\n\nAucun compte trouvé."
    lines = ["📋 <b>Comptes ZIVPN</b>"]
    for line in out.splitlines():
        user = line.split(":", 1)[0] if line else ""
        if user:
            lines.append(f"• <code>{user}</code>")
    return "\n".join(lines)


# ─── Telegram command handlers ───────────────────────────────────────────────────


@bot.message_handler(commands=["start", "help", "menu"])
def start(message: Any) -> None:
    if not authorized(message):
        return
    show_menu(message.chat.id)


@bot.message_handler(commands=["resellers"])
def list_resellers(message: Any) -> None:
    if not authorized(message):
        return
    rows, now = scan_resellers(), datetime.now()
    lines = ["👥 <b>Surveillance des revendeurs</b>"]
    for rid, info, expiry in rows:
        exp_text = "échéance inconnue" if expiry is None else f"{remaining(expiry, now)} — {expiry:%d/%m/%Y %H:%M}"
        lines.append(f"• {label(rid, info)}\n  ⏳ {escaped(exp_text)}")
    if not rows:
        lines.append("Aucun revendeur enregistré.")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 3500:
            bot.send_message(message.chat.id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        bot.send_message(message.chat.id, chunk)


@bot.message_handler(commands=["status"])
def status(message: Any) -> None:
    if not authorized(message):
        return
    rows, now = scan_resellers(), datetime.now()
    expired = sum(1 for _, _, exp in rows if exp is not None and exp <= now)
    soon = sum(1 for _, _, exp in rows if exp is not None and now < exp <= now + timedelta(hours=24))
    unknown = sum(1 for _, _, exp in rows if exp is None)
    bot.send_message(
        message.chat.id,
        f"📊 <b>État de la surveillance</b>\n\n"
        f"👥 Revendeurs : <b>{len(rows)}</b>\n"
        f"⚠️ Échéance sous 24 h : <b>{soon}</b>\n"
        f"⛔ Expirés : <b>{expired}</b>\n"
        f"❓ Échéance inconnue : <b>{unknown}</b>\n\n"
        f"<b>Comptes actifs:</b> SSH, VLESS, VMess, Trojan et ZIVPN via commandes dédiées."
    )


@bot.message_handler(commands=["check"])
def check_now(message: Any) -> None:
    if not authorized(message):
        return
    rows = scan_resellers()
    bot.send_message(message.chat.id, f"✅ Vérification terminée : {len(rows)} revendeur(s) contrôlé(s).")


@bot.message_handler(commands=["ssh_create"])
def ssh_create_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /ssh_create <username> <jours>")
        return
    username, days = parts[1], int(parts[2])
    password = secrets.token_urlsafe(12)
    ok, reply = create_ssh_account(username, password, days)
    bot.reply_to(message, reply if ok else reply)


@bot.message_handler(commands=["ssh_list"])
def ssh_list_cmd(message: Any) -> None:
    if not authorized(message):
        return
    bot.reply_to(message, list_ssh_accounts())


@bot.message_handler(commands=["ssh_delete"])
def ssh_delete_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /ssh_delete <username>")
        return
    ok, reply = delete_ssh_account(parts[1])
    bot.reply_to(message, reply)


@bot.message_handler(commands=["ssh_renew"])
def ssh_renew_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /ssh_renew <username> <jours>")
        return
    ok, reply = renew_ssh_account(parts[1], int(parts[2]))
    bot.reply_to(message, reply)


@bot.message_handler(commands=["vless_create", "vmess_create", "trojan_create"])
def xray_create_cmd(message: Any) -> None:
    if not authorized(message):
        return
    cmd = message.text.split()[0][1:]
    protocol = cmd.replace("_create", "")
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, f"Usage: /{cmd} <username> <jours>")
        return
    ok, reply = create_xray_account(protocol, parts[1], int(parts[2]))
    bot.reply_to(message, reply)


@bot.message_handler(commands=["xray_list"])
def xray_list_cmd(message: Any) -> None:
    if not authorized(message):
        return
    bot.reply_to(message, list_xray_accounts())


@bot.message_handler(commands=["xray_delete"])
def xray_delete_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /xray_delete <vless|vmess|trojan> <username>")
        return
    proto, username = parts[1], parts[2]
    ok, reply = delete_xray_account(proto, username)
    bot.reply_to(message, reply)


@bot.message_handler(commands=["xray_renew"])
def xray_renew_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 4:
        bot.reply_to(message, "Usage: /xray_renew <vless|vmess|trojan> <username> <jours>")
        return
    proto, username, days = parts[1], parts[2], int(parts[3])
    ok, reply = renew_xray_account(proto, username, days)
    bot.reply_to(message, reply)


@bot.message_handler(commands=["zivpn_create"])
def zivpn_create_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 4:
        bot.reply_to(message, "Usage: /zivpn_create <username> <password> <jours>")
        return
    ok, reply = create_zivpn_account(parts[1], parts[2], int(parts[3]))
    bot.reply_to(message, reply)


@bot.message_handler(commands=["zivpn_list"])
def zivpn_list_cmd(message: Any) -> None:
    if not authorized(message):
        return
    bot.reply_to(message, list_zivpn_accounts())


@bot.message_handler(commands=["zivpn_delete"])
def zivpn_delete_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /zivpn_delete <username>")
        return
    ok, reply = delete_zivpn_account(parts[1])
    bot.reply_to(message, reply)


@bot.message_handler(commands=["zivpn_renew"])
def zivpn_renew_cmd(message: Any) -> None:
    if not authorized(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /zivpn_renew <username> <jours>")
        return
    ok, reply = renew_zivpn_account(parts[1], int(parts[2]))
    bot.reply_to(message, reply)


# ─── Inline keyboard menus ─────────────────────────────────────────────────────


def main_menu_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🛡 Surveillance", callback_data="menu_monitor"),
        types.InlineKeyboardButton("👤 SSH", callback_data="menu_ssh"),
        types.InlineKeyboardButton("⚡ XRAY", callback_data="menu_xray"),
        types.InlineKeyboardButton("🌐 ZIVPN", callback_data="menu_zivpn"),
    )
    return kb


def ssh_menu_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Créer", callback_data="ssh_create_hint"),
        types.InlineKeyboardButton("📋 Lister", callback_data="ssh_list"),
        types.InlineKeyboardButton("🗑 Supprimer", callback_data="ssh_delete_hint"),
        types.InlineKeyboardButton("🔄 Renouveler", callback_data="ssh_renew_hint"),
        types.InlineKeyboardButton("◀ Retour", callback_data="main_menu"),
    )
    return kb


def xray_menu_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ VLESS", callback_data="vless_create_hint"),
        types.InlineKeyboardButton("➕ VMESS", callback_data="vmess_create_hint"),
        types.InlineKeyboardButton("➕ Trojan", callback_data="trojan_create_hint"),
        types.InlineKeyboardButton("📋 Lister", callback_data="xray_list"),
        types.InlineKeyboardButton("🗑 Supprimer", callback_data="xray_delete_hint"),
        types.InlineKeyboardButton("◀ Retour", callback_data="main_menu"),
    )
    return kb


def zivpn_menu_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Créer", callback_data="zivpn_create_hint"),
        types.InlineKeyboardButton("📋 Lister", callback_data="zivpn_list"),
        types.InlineKeyboardButton("🗑 Supprimer", callback_data="zivpn_delete_hint"),
        types.InlineKeyboardButton("🔄 Renouveler", callback_data="zivpn_renew_hint"),
        types.InlineKeyboardButton("◀ Retour", callback_data="main_menu"),
    )
    return kb


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call: Any) -> None:
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "⛔ Accès refusé")
        return
    bot.answer_callback_query(call.id)

    if call.data == "main_menu":
        bot.edit_message_text(
            "🛡 <b>Surveillance + Gestion VPN</b>\n\nChoisissez une action :",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu_keyboard(),
        )
    elif call.data == "menu_monitor":
        bot.edit_message_text(
            "🛡 <b>Surveillance des revendeurs</b>\n\n"
            "<code>/resellers</code> — liste\n"
            "<code>/status</code> — résumé\n"
            "<code>/check</code> — vérification",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu_keyboard(),
        )
    elif call.data == "menu_ssh":
        bot.edit_message_text(
            "👤 <b>Gestion SSH</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=ssh_menu_keyboard(),
        )
    elif call.data == "menu_xray":
        bot.edit_message_text(
            "⚡ <b>Gestion XRAY</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=xray_menu_keyboard(),
        )
    elif call.data == "menu_zivpn":
        bot.edit_message_text(
            "🌐 <b>Gestion ZIVPN</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=zivpn_menu_keyboard(),
        )
    elif call.data == "ssh_list":
        bot.edit_message_text(list_ssh_accounts(), call.message.chat.id, call.message.message_id, reply_markup=ssh_menu_keyboard())
    elif call.data == "ssh_create_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /ssh_create <username> <jours>")
    elif call.data == "ssh_delete_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /ssh_delete <username>")
    elif call.data == "ssh_renew_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /ssh_renew <username> <jours>")
    elif call.data == "vless_create_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /vless_create <username> <jours>")
    elif call.data == "vmess_create_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /vmess_create <username> <jours>")
    elif call.data == "trojan_create_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /trojan_create <username> <jours>")
    elif call.data == "xray_list":
        bot.edit_message_text(list_xray_accounts(), call.message.chat.id, call.message.message_id, reply_markup=xray_menu_keyboard())
    elif call.data == "xray_delete_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /xray_delete <vless|vmess|trojan> <username>")
    elif call.data == "zivpn_list":
        bot.edit_message_text(list_zivpn_accounts(), call.message.chat.id, call.message.message_id, reply_markup=zivpn_menu_keyboard())
    elif call.data == "zivpn_create_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /zivpn_create <username> <password> <jours>")
    elif call.data == "zivpn_delete_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /zivpn_delete <username>")
    elif call.data == "zivpn_renew_hint":
        bot.send_message(call.message.chat.id, "📝 Envoyer: /zivpn_renew <username> <jours>")


# ─── Monitoring loop ────────────────────────────────────────────────────────────


def monitor_loop() -> None:
    while True:
        try:
            scan_resellers()
        except Exception:
            logger.exception("Erreur lors de la surveillance périodique")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    rows = scan_resellers(notify_changes=False)
    send_admin(f"✅ Surveillance démarrée. {len(rows)} revendeur(s) suivis.")
    threading.Thread(target=monitor_loop, name="reseller-monitor", daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
