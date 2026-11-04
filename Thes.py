#!/usr/bin/env python3
"""Telegram bot en lecture seule pour surveiller les revendeurs.

Le bot n'exécute aucune commande système et ne modifie ni comptes ni configuration.
Il lit uniquement config.json et resellers.json, puis notifie le super administrateur.
"""
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
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def reseller_expiry(info: dict[str, Any]) -> datetime | None:
    explicit = parse_datetime(info.get("expires_at") or info.get("expiry_datetime"))
    if explicit:
        return explicit

    added = parse_datetime(info.get("added_datetime"))
    if added is None:
        added_date = info.get("added_date")
        if isinstance(added_date, str):
            added = parse_datetime(f"{added_date} 00:00:00")
    if added is None:
        return None

    try:
        default_hours = 48 if info.get("type") == "trial" else int(info.get("max_days", 30)) * 24
        hours = int(info.get("max_hours", default_hours))
        if hours < 0:
            return None
        return added + timedelta(hours=hours)
    except (TypeError, ValueError, OverflowError):
        return None


def format_remaining(expiry: datetime, now: datetime) -> str:
    seconds = int((expiry - now).total_seconds())
    if seconds <= 0:
        return "expiré"
    days, seconds = divmod(seconds, 86400)
    hours, minutes = divmod(seconds, 3600)
    minutes //= 60
    if days:
        return f"{days} j {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def safe_text(value: Any) -> str:
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
    temp_path = STATE_FILE.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, STATE_FILE)


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
        logger.exception("Impossible d'envoyer une notification au super administrateur")


def reseller_label(reseller_id: str, info: dict[str, Any]) -> str:
    alias = safe_text(info.get("alias") or info.get("first_name") or reseller_id)
    kind = safe_text(info.get("type", "inconnu")).upper()
    return f"<b>{alias}</b> (ID <code>{safe_text(reseller_id)}</code>, {kind})"


def scan_resellers(notify_changes: bool = True) -> tuple[dict[str, dict[str, Any]], list[tuple[str, dict[str, Any], datetime | None]]]:
    now = datetime.now()
    resellers = load_resellers()
    state = load_state()
    prior_known = state.get("known_resellers")
    current_ids = set(resellers)

    with state_lock:
        # À la première exécution, on initialise la référence sans signaler
        # tous les revendeurs déjà enregistrés comme de nouveaux ajouts.
        if not isinstance(prior_known, list):
            state["known_resellers"] = sorted(current_ids)
        elif notify_changes:
            old_ids = set(map(str, prior_known))
            for reseller_id in sorted(current_ids - old_ids):
                send_admin(f"🆕 Nouveau revendeur détecté : {reseller_label(reseller_id, resellers[reseller_id])}.")
            for reseller_id in sorted(old_ids - current_ids):
                send_admin(f"➖ Revendeur retiré du fichier : <code>{safe_text(reseller_id)}</code>.")
            state["known_resellers"] = sorted(current_ids)

        alerts = state.setdefault("alerts", {})
        if not isinstance(alerts, dict):
            alerts = state["alerts"] = {}

        rows = []
        for reseller_id, info in resellers.items():
            expiry = reseller_expiry(info)
            rows.append((reseller_id, info, expiry))
            if expiry is None:
                continue
            expiry_key = expiry.isoformat(timespec="seconds")
            previous = alerts.get(reseller_id)
            entry = previous if isinstance(previous, dict) and previous.get("expiry") == expiry_key else {"expiry": expiry_key, "sent": []}
            sent = set(entry.get("sent", []))
            label = reseller_label(reseller_id, info)
            remaining = expiry - now
            if remaining.total_seconds() <= 0 and "expired" not in sent:
                send_admin(f"⛔ Abonnement expiré : {label}. Échéance : <code>{expiry:%d/%m/%Y %H:%M}</code>.")
                sent.add("expired")
            elif remaining.total_seconds() > 0:
                for hours, description in ALERT_THRESHOLDS:
                    alert_key = f"{hours}h"
                    if remaining <= timedelta(hours=hours) and alert_key not in sent:
                        send_admin(
                            f"⚠️ Échéance dans moins de {description} : {label}. "
                            f"Temps restant : <b>{format_remaining(expiry, now)}</b> "
                            f"(échéance <code>{expiry:%d/%m/%Y %H:%M}</code>)."
                        )
                        sent.add(alert_key)
            entry["expiry"] = expiry_key
            entry["sent"] = sorted(sent)
            alerts[reseller_id] = entry

        # Nettoie l'état des identifiants retirés pour éviter une croissance infinie.
        for reseller_id in set(alerts) - current_ids:
            alerts.pop(reseller_id, None)
        save_state(state)
    return resellers, rows


def require_super_admin(message: Any) -> bool:
    if message.from_user.id != SUPER_ADMIN:
        bot.reply_to(message, "⛔ Commande réservée au super administrateur.")
        return False
    return True


@bot.message_handler(commands=["start", "help"])
def start(message: Any) -> None:
    if not require_super_admin(message):
        return
    bot.send_message(
        message.chat.id,
        "🛡 <b>Surveillance des revendeurs</b>\n\n"
        "Ce bot est en lecture seule et envoie les alertes uniquement au super administrateur.\n"
        "Commandes : <code>/resellers</code> (liste), <code>/status</code> (résumé), <code>/check</code> (vérifier maintenant).",
    )


@bot.message_handler(commands=["resellers"])
def list_resellers(message: Any) -> None:
    if not require_super_admin(message):
        return
    _, rows = scan_resellers()
    now = datetime.now()
    lines = ["👥 <b>Surveillance des revendeurs</b>"]
    for reseller_id, info, expiry in rows:
        if expiry is None:
            expiry_text = "échéance inconnue"
        else:
            expiry_text = f"{format_remaining(expiry, now)} — {expiry:%d/%m/%Y %H:%M}"
        lines.append(f"• {reseller_label(reseller_id, info)}\n  ⏳ {safe_text(expiry_text)}")
    if not rows:
        lines.append("Aucun revendeur enregistré.")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            bot.send_message(message.chat.id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        bot.send_message(message.chat.id, chunk)


@bot.message_handler(commands=["status"])
def status(message: Any) -> None:
    if not require_super_admin(message):
        return
    _, rows = scan_resellers()
    now = datetime.now()
    expired = sum(1 for _, _, expiry in rows if expiry is not None and expiry <= now)
    soon = sum(1 for _, _, expiry in rows if expiry is not None and now < expiry <= now + timedelta(hours=24))
    unknown = sum(1 for _, _, expiry in rows if expiry is None)
    bot.send_message(
        message.chat.id,
        f"📊 <b>État de la surveillance</b>\n\n"
        f"👥 Revendeurs : <b>{len(rows)}</b>\n"
        f"⚠️ Échéance sous 24 h : <b>{soon}</b>\n"
        f"⛔ Expirés : <b>{expired}</b>\n"
        f"❔ Échéance inconnue : <b>{unknown}</b>\n"
        f"🔄 Vérification automatique toutes les {POLL_SECONDS // 60} min.",
    )


@bot.message_handler(commands=["check"])
def check_now(message: Any) -> None:
    if not require_super_admin(message):
        return
    _, rows = scan_resellers()
    bot.send_message(message.chat.id, f"✅ Vérification terminée : {len(rows)} revendeur(s) contrôlé(s).")


def monitor_loop() -> None:
    while True:
        try:
            scan_resellers()
        except Exception:
            logger.exception("Erreur pendant la vérification périodique")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    # Établit la référence de départ et traite les alertes d'échéance pertinentes.
    current, _ = scan_resellers(notify_changes=False)
    send_admin(f"✅ Surveillance des revendeurs démarrée. {len(current)} revendeur(s) suivis.")
    threading.Thread(target=monitor_loop, name="reseller-monitor", daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
