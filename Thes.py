#!/usr/bin/env python3
"""Bot Telegram en lecture seule : surveillance et notifications revendeurs."""
from __future__ import annotations

import html
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import telebot

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


def escaped(value: Any) -> str:
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
state_lock = threading.Lock()


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


def authorized(message: Any) -> bool:
    if message.from_user.id != SUPER_ADMIN:
        bot.reply_to(message, "⛔ Commande réservée au super administrateur.")
        return False
    return True


def show_menu(chat_id: int) -> None:
    caption = (
        "🛡 <b>Surveillance des revendeurs</b>\n\n"
        "Mode lecture seule — les alertes sont envoyées au super administrateur.\n\n"
        "<code>/resellers</code> — Liste et temps restant\n"
        "<code>/status</code> — Résumé des échéances\n"
        "<code>/check</code> — Vérifier maintenant"
    )
    if MENU_IMAGE_FILE.is_file():
        try:
            with MENU_IMAGE_FILE.open("rb") as photo:
                bot.send_photo(chat_id, photo, caption=caption)
            return
        except Exception:
            logger.exception("Impossible d'afficher l'image de menu %s", MENU_IMAGE_FILE)
    bot.send_message(chat_id, caption)


@bot.message_handler(commands=["start", "help"])
def start(message: Any) -> None:
    if authorized(message):
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
    bot.send_message(message.chat.id, f"📊 <b>État de la surveillance</b>\n\n👥 Revendeurs : <b>{len(rows)}</b>\n⚠️ Échéance sous 24 h : <b>{soon}</b>\n⛔ Expirés : <b>{expired}</b>\n❔ Échéance inconnue : <b>{unknown}</b>\n🔄 Vérification toutes les {POLL_SECONDS // 60} min.")


@bot.message_handler(commands=["check"])
def check_now(message: Any) -> None:
    if authorized(message):
        rows = scan_resellers()
        bot.send_message(message.chat.id, f"✅ Vérification terminée : {len(rows)} revendeur(s) contrôlé(s).")


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
