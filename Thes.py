#!/usr/bin/env python3
"""Thes: surveillance des revendeurs et gestion des rôles Telegram, sans actions VPN."""
from __future__ import annotations

import html
import json
import logging
import os
import threading
import time
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("the-s-reseller-monitor")


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


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


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


def role_ids(role: str) -> set[int]:
    legacy_keys = {"admin": ("admin_ids", "admins"), "reseller": ("reseller_ids", "resellers_ids")}
    result: set[int] = set()
    for key in legacy_keys.get(role, ()):
        values = config.get(key, [])
        if isinstance(values, list):
            for raw in values:
                try:
                    result.add(int(raw))
                except (TypeError, ValueError):
                    logger.warning("ID invalide dans %s", key)
    stored_roles = load_state().get("telegram_roles", {})
    stored = stored_roles.get(role, []) if isinstance(stored_roles, dict) else []
    now = datetime.now(timezone.utc)
    if isinstance(stored, list):
        for raw in stored:
            try:
                result.add(int(raw))
            except (TypeError, ValueError):
                continue
    elif isinstance(stored, dict):
        for raw, record in stored.items():
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            expiry = parse_datetime(record.get("expires_at")) if isinstance(record, dict) else None
            if expiry is None or expiry.replace(tzinfo=timezone.utc) > now:
                result.add(uid)
    return result


def role_for(uid: int) -> str | None:
    if uid == SUPER_ADMIN:
        return "super_admin"
    if uid in role_ids("admin"):
        return "admin"
    if uid in role_ids("reseller"):
        return "reseller"
    return None


def authorized(message: Any) -> bool:
    if role_for(message.from_user.id) is None:
        bot.reply_to(message, "⛔ Accès refusé.")
        return False
    return True


def parse_days(raw: str) -> int | None:
    try:
        days = int(raw)
        return days if 1 <= days <= 3650 else None
    except (TypeError, ValueError):
        return None


def store_role(role: str, uid: int, days: int) -> datetime | None:
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    with state_lock:
        state = load_state()
        all_roles = state.setdefault("telegram_roles", {})
        if not isinstance(all_roles, dict):
            all_roles = state["telegram_roles"] = {}
        current = all_roles.get(role, {})
        if isinstance(current, list):
            current = {str(old): {"expires_at": None} for old in current if str(old).isdigit()}
        if not isinstance(current, dict):
            current = {}
        current[str(uid)] = {
            "expires_at": expires.isoformat(timespec="seconds"),
            "assigned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        all_roles[role] = current
        save_state(state)
    return expires


def remove_role(role: str, uid: int) -> bool:
    with state_lock:
        state = load_state()
        all_roles = state.get("telegram_roles", {})
        if not isinstance(all_roles, dict):
            return False
        current = all_roles.get(role)
        if isinstance(current, list):
            updated = [item for item in current if str(item) != str(uid)]
        elif isinstance(current, dict):
            updated = dict(current)
            updated.pop(str(uid), None)
        else:
            return False
        if updated == current:
            return False
        all_roles[role] = updated
        save_state(state)
    return True


def load_resellers() -> dict[str, dict[str, Any]]:
    data = load_json(RESELLERS_FILE, {})
    if not isinstance(data, dict):
        logger.error("Le fichier revendeurs doit contenir un objet JSON.")
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


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
                info = resellers[rid]
                alias = esc(info.get("alias") or info.get("first_name") or rid)
                notify_super(f"🆕 Nouveau revendeur détecté : <b>{alias}</b> (ID <code>{esc(rid)}</code>).")
            for rid in sorted(set(map(str, prior)) - current_ids):
                notify_super(f"➖ Revendeur retiré : <code>{esc(rid)}</code>.")
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
            alias = esc(info.get("alias") or info.get("first_name") or rid)
            if delta.total_seconds() <= 0 and "expired" not in sent:
                notify_super(f"⛔ Abonnement expiré : <b>{alias}</b> (ID <code>{esc(rid)}</code>). Échéance : <code>{expiry:%d/%m/%Y %H:%M}</code>.")
                sent.add("expired")
            elif delta.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    key = f"{hours}h"
                    if delta <= timedelta(hours=hours) and key not in sent:
                        notify_super(f"⚠️ Échéance dans moins de {description} : <b>{alias}</b>. Temps restant : <b>{remaining(expiry, now)}</b> (échéance <code>{expiry:%d/%m/%Y %H:%M}</code>).")
                        sent.add(key)
            entry["expiry"], entry["sent"] = expiry_key, sorted(sent)
            alerts[rid] = entry
        for rid in set(alerts) - current_ids:
            alerts.pop(rid, None)
        save_state(state)
    return rows


def menu_keyboard(uid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("👥 Revendeurs", callback_data="resellers"),
           types.InlineKeyboardButton("📊 Statut", callback_data="status"))
    if uid == SUPER_ADMIN:
        kb.add(types.InlineKeyboardButton("👤 Gérer les rôles", callback_data="roles"))
    return kb


def send_menu(message: Any) -> None:
    text = (
        "🛡 <b>Thes — Surveillance des revendeurs</b>\n\n"
        "Fonctions disponibles en lecture seule :\n"
        "<code>/resellers</code> — abonnements et échéances\n"
        "<code>/status</code> — résumé des échéances\n"
        "<code>/check</code> — vérifier maintenant\n\n"
        "Le bot ne crée, ne modifie ni ne supprime de comptes VPN."
    )
    keyboard = menu_keyboard(message.from_user.id)
    if MENU_IMAGE_FILE.is_file():
        try:
            with MENU_IMAGE_FILE.open("rb") as photo:
                bot.send_photo(message.chat.id, photo, caption=text, reply_markup=keyboard)
            return
        except Exception:
            logger.exception("Impossible d'afficher l'image de menu")
    bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.message_handler(commands=["start", "help", "menu"])
def start(message: Any) -> None:
    if authorized(message):
        send_menu(message)


@bot.message_handler(commands=["resellers"])
def list_resellers(message: Any) -> None:
    if not authorized(message):
        return
    rows, now = scan_resellers(), datetime.now()
    lines = ["👥 <b>Surveillance des abonnements revendeurs</b>"]
    for rid, info, expiry in rows:
        alias = esc(info.get("alias") or info.get("first_name") or rid)
        exp_text = "échéance inconnue" if expiry is None else f"{remaining(expiry, now)} — {expiry:%d/%m/%Y %H:%M}"
        lines.append(f"• <b>{alias}</b> (ID <code>{esc(rid)}</code>)\n  ⏳ {esc(exp_text)}")
    bot.reply_to(message, "\n".join(lines) if rows else "Aucun revendeur enregistré.")


@bot.message_handler(commands=["status"])
def status(message: Any) -> None:
    if not authorized(message):
        return
    rows, now = scan_resellers(), datetime.now()
    expired = sum(1 for _, _, exp in rows if exp is not None and exp <= now)
    soon = sum(1 for _, _, exp in rows if exp is not None and now < exp <= now + timedelta(hours=24))
    unknown = sum(1 for _, _, exp in rows if exp is None)
    bot.reply_to(message, f"📊 <b>Surveillance</b>\n\n👥 Revendeurs : <b>{len(rows)}</b>\n⚠️ Échéance sous 24 h : <b>{soon}</b>\n⛔ Expirés : <b>{expired}</b>\n❓ Échéance inconnue : <b>{unknown}</b>")


@bot.message_handler(commands=["check"])
def check_now(message: Any) -> None:
    if authorized(message):
        rows = scan_resellers()
        bot.reply_to(message, f"✅ Vérification terminée : {len(rows)} revendeur(s) contrôlé(s).")


@bot.message_handler(commands=["admin_add", "admin_remove", "reseller_add", "reseller_remove", "roles"])
def manage_roles(message: Any) -> None:
    if message.from_user.id != SUPER_ADMIN:
        bot.reply_to(message, "⛔ Seul le super administrateur peut gérer les rôles.")
        return
    parts = message.text.split()
    command = parts[0].split("@", 1)[0].lstrip("/")
    if command == "roles":
        output = ["👥 <b>Rôles Telegram</b>"]
        for role, title in (("admin", "Administrateurs"), ("reseller", "Revendeurs")):
            stored = load_state().get("telegram_roles", {}).get(role, {})
            output.append(f"<b>{title}</b>")
            active = role_ids(role)
            for uid in sorted(active):
                record = stored.get(str(uid), {}) if isinstance(stored, dict) else {}
                expiry = parse_datetime(record.get("expires_at")) if isinstance(record, dict) else None
                expires_text = "sans expiration (ancienne config)" if expiry is None else expiry.strftime("%d/%m/%Y %H:%M")
                output.append(f"• <code>{uid}</code> — expiration : <code>{esc(expires_text)}</code>")
            if not active:
                output.append("• Aucun")
        bot.reply_to(message, "\n".join(output))
        return
    if command.endswith("_remove"):
        if len(parts) != 2 or not parts[1].isdigit():
            bot.reply_to(message, f"Usage : /{command} <telegram_id>")
            return
        role = "admin" if command.startswith("admin_") else "reseller"
        changed = remove_role(role, int(parts[1]))
        bot.reply_to(message, "✅ Rôle retiré." if changed else "ℹ️ Rôle introuvable dans les rôles modifiables.")
        return
    if len(parts) != 3 or not parts[1].isdigit():
        bot.reply_to(message, f"Usage : /{command} <telegram_id> <jours> (1–3650)")
        return
    days = parse_days(parts[2])
    if days is None:
        bot.reply_to(message, "Durée invalide. Choisissez entre 1 et 3650 jours.")
        return
    uid = int(parts[1])
    if uid == SUPER_ADMIN:
        bot.reply_to(message, "⛔ Le super administrateur ne peut pas être ajouté à un autre rôle.")
        return
    role = "admin" if command.startswith("admin_") else "reseller"
    expiry = store_role(role, uid, days)
    bot.reply_to(message, f"✅ Rôle {role} attribué à <code>{uid}</code> pour <b>{days} jour(s)</b>, jusqu’au <code>{expiry.astimezone():%d/%m/%Y %H:%M %Z}</code>.")


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call: Any) -> None:
    if role_for(call.from_user.id) is None:
        bot.answer_callback_query(call.id, "⛔ Accès refusé")
        return
    bot.answer_callback_query(call.id)
    if call.data == "resellers":
        bot.send_message(call.message.chat.id, "Utilisez /resellers pour consulter les abonnements.")
    elif call.data == "status":
        bot.send_message(call.message.chat.id, "Utilisez /status pour le résumé des échéances.")
    elif call.data == "roles" and call.from_user.id == SUPER_ADMIN:
        bot.send_message(call.message.chat.id, "Commandes super admin : /admin_add ID JOURS, /admin_remove ID, /reseller_add ID JOURS, /reseller_remove ID, /roles.")


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
