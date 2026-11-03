#!/usr/bin/env python3
"""KATASHIE Telegram bot. Existing VPN modules remain the source of account operations."""
import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

from modules import ssh_core, xray_core, zivpn_core, system_core
from modules import telegram_store as store

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('katashie-bot')
CONFIG_PATH = os.environ.get('KATASHIE_BOT_CONFIG', '/etc/katashie_bot/config.json')
if not os.path.isfile(CONFIG_PATH):
    raise SystemExit(f'Bot config not found: {CONFIG_PATH}')
try:
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f'Cannot read bot config: {exc}')

TOKEN = str(cfg.get('bot_token') or '').strip()
if not TOKEN or TOKEN == 'VOTRE_TOKEN_TELEGRAM':
    raise SystemExit('Missing real Telegram bot token in config (bot_token).')

def _ids(*values):
    result = set()
    for value in values:
        values_list = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values_list:
            try:
                if item is not None and str(item).strip():
                    result.add(int(item))
            except (TypeError, ValueError):
                logger.warning('Ignoring invalid configured Telegram ID')
    return result

SUPER_ADMIN_ID = next(iter(_ids(cfg.get('super_admin'), cfg.get('super_admin_id'))), None)
ADMIN_IDS = _ids(cfg.get('admin_ids'), cfg.get('admins'))
RESELLER_IDS = _ids(cfg.get('reseller_ids'))
if SUPER_ADMIN_ID is None:
    logger.warning('No super_admin ID configured; contact, broadcast and account management are unavailable.')

def is_super_admin(user_id):
    return SUPER_ADMIN_ID is not None and int(user_id) == SUPER_ADMIN_ID

def is_admin(user_id):
    return is_super_admin(user_id) or int(user_id) in ADMIN_IDS

def is_reseller(user_id):
    return is_admin(user_id) or int(user_id) in RESELLER_IDS

def registered_user(user_id):
    row = store.account(user_id)
    return bool(row and row['status'] == 'active')

def authorized_service_user(user_id):
    return is_reseller(user_id) or registered_user(user_id)

def safe(text):
    return html.escape(str(text or ''), quote=False)

store.initialize()
bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True, num_threads=4)
BANNER = '🛡 <b>KATASHIE VPN</b> — Panel Telegram'


def main_keyboard(admin=False, super_admin=False):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('👤 SSH', callback_data='menu:ssh'),
           types.InlineKeyboardButton('⚡ XRAY', callback_data='menu:xray'),
           types.InlineKeyboardButton('🌐 ZIVPN', callback_data='menu:zivpn'),
           types.InlineKeyboardButton('📊 Statut', callback_data='menu:status'))
    if admin:
        kb.add(types.InlineKeyboardButton('👥 Administrateurs', callback_data='menu:admins'))
    if super_admin:
        kb.add(types.InlineKeyboardButton('📨 Messages en attente', callback_data='inbox'),
               types.InlineKeyboardButton('📣 Diffusion', callback_data='broadcast_menu'))
    return kb


def contact_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('↩️ Répondre', callback_data=f'reply:{user_id}'),
           types.InlineKeyboardButton('✅ Activer le compte', callback_data=f'activate:{user_id}'),
           types.InlineKeyboardButton('⛔ Suspendre le compte', callback_data=f'suspend:{user_id}'),
           types.InlineKeyboardButton('ℹ️ Voir les informations', callback_data=f'info:{user_id}'),
           types.InlineKeyboardButton('↩️ Retour', callback_data='inbox'))
    return kb


def notify_superadmin(protocol, username, creator, days, expiry, event_id):
    if SUPER_ADMIN_ID is None or not store.log_event_once(event_id):
        return
    created = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    text = (f'🆕 <b>Configuration VPN créée</b>\n\n'
            f'Type : <b>{safe(protocol)}</b>\nUtilisateur : <code>{safe(username)}</code>\n'
            f'Créée par Telegram ID : <code>{int(creator)}</code>\nDate : {created}\n'
            f'Durée : {int(days)} jour(s)\nExpiration : <code>{safe(expiry)}</code>')
    try:
        bot.send_message(SUPER_ADMIN_ID, text)
    except Exception:
        logger.exception('VPN creation notification delivery failed')


def _notify_created(ok, username, actor, protocol, days, result):
    if not ok:
        return
    expiry_match = re.search(r'(?:Expiration|Nouvelle exp\.)\s*:?\s*<code>([^<]+)', str(result), re.I)
    expiry = expiry_match.group(1) if expiry_match else 'non précisée'
    # A stable event key prevents duplicate notifications for a repeated update.
    event_id = f'{protocol}:{username}:{expiry}:{actor}'
    notify_superadmin(protocol, username, actor, days, expiry, event_id)


@bot.message_handler(commands=['start', 'help'])
def start(message):
    uid = message.from_user.id
    store.upsert_account(uid, message.from_user.first_name, message.from_user.username,
                         'user', 'active' if authorized_service_user(uid) else 'pending')
    if not authorized_service_user(uid):
        bot.send_message(message.chat.id,
            'Bienvenue. Votre compte est en attente d’activation. Envoyez votre message ici ou utilisez /contact pour joindre le super administrateur. Vous ne recevez aucun privilège avant activation.')
        return
    role = 'Super administrateur' if is_super_admin(uid) else ('Administrateur' if is_admin(uid) else ('Revendeur' if uid in RESELLER_IDS else 'Utilisateur'))
    bot.send_message(message.chat.id, f'{BANNER}\n\nBonjour <b>{safe(message.from_user.first_name)}</b> — {role}.\n/help : aide • /status : statut du compte • /contact : contacter le super administrateur', reply_markup=main_keyboard(is_admin(uid), is_super_admin(uid)))


@bot.message_handler(commands=['contact'])
def contact_command(message):
    store.upsert_account(message.from_user.id, message.from_user.first_name, message.from_user.username,
                         'user', 'active' if authorized_service_user(message.from_user.id) else 'pending')
    bot.reply_to(message, 'Écrivez votre message ici. Il sera transmis au super administrateur.')


@bot.message_handler(commands=['status'])
def status_command(message):
    if is_admin(message.from_user.id) or message.from_user.id in RESELLER_IDS:
        status = 'Actif — rôle configuré par le système.'
    else:
        row = store.account(message.from_user.id)
        status = {'pending': 'En attente d’activation.', 'active': 'Actif (utilisateur enregistré).', 'suspended': 'Suspendu.'}.get(row['status'] if row else 'pending', 'En attente d’activation.')
    bot.reply_to(message, f'📊 Statut du compte : <b>{status}</b>')


@bot.message_handler(commands=['admin'])
def admin_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, '⛔ Accès réservé aux administrateurs.')
        return
    if is_super_admin(message.from_user.id):
        pending = store.pending_accounts()
        text = f'Administration super admin\nMessages/utilisateurs en attente : <b>{len(pending)}</b>'
        bot.send_message(message.chat.id, text, reply_markup=main_keyboard(True, True))
    else:
        bot.send_message(message.chat.id, 'Menu administrateur.', reply_markup=main_keyboard(True, False))


@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if not is_super_admin(message.from_user.id):
        bot.reply_to(message, '⛔ Fonction réservée au super administrateur.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) == 1 or parts[1].strip().lower() not in ('admins', 'administrateurs', 'resellers', 'revendeurs'):
        bot.reply_to(message, 'Choisissez le groupe : /broadcast admins ou /broadcast revendeurs')
        return
    group = 'admins' if parts[1].strip().lower() in ('admins', 'administrateurs') else 'resellers'
    store.set_state(message.from_user.id, 'broadcast_message', target_group=group)
    bot.reply_to(message, f'Envoyez le texte à diffuser aux {"administrateurs" if group == "admins" else "revendeurs"}. /cancel pour annuler.')


@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    store.clear_state(message.from_user.id)
    store.clear_draft(message.from_user.id)
    bot.reply_to(message, 'Opération annulée.')


def _authorize(message):
    if not authorized_service_user(message.from_user.id):
        bot.reply_to(message, '⛔ Compte non activé. Utilisez /contact pour écrire au super administrateur.')
        return False
    return True


@bot.message_handler(commands=['ssh_create'])
def ssh_create(message):
    if not is_reseller(message.from_user.id):
        bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 4:
        bot.reply_to(message, 'Usage : /ssh_create <utilisateur> <mot_de_passe> <jours>'); return
    try:
        days = int(parts[3])
        if days < 1 or days > 3650: raise ValueError
    except ValueError:
        bot.reply_to(message, 'Durée invalide (1 à 3650 jours).'); return
    ok, result = ssh_core.create_ssh_account(parts[1], parts[2], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result)
    _notify_created(ok, parts[1], message.from_user.id, 'SSH', days, result)


@bot.message_handler(commands=['ssh_list'])
def ssh_list(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    bot.reply_to(message, '\n'.join(ssh_core.get_ssh_usernames()) or 'Aucun utilisateur SSH.')


@bot.message_handler(commands=['ssh_delete'])
def ssh_delete(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 2: bot.reply_to(message, 'Usage : /ssh_delete <utilisateur>'); return
    ok, result = ssh_core.delete_ssh_account(parts[1]); bot.reply_to(message, result)


@bot.message_handler(commands=['vless_create', 'vmess_create', 'trojan_create'])
def xray_create(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split(); command = parts[0].split('@')[0].lstrip('/').split('@')[0]
    protocol = command.removesuffix('_create').lower()
    if len(parts) < 3: bot.reply_to(message, f'Usage : /{command} <utilisateur> <jours>'); return
    try:
        days = int(parts[2])
        if days < 1 or days > 3650: raise ValueError
    except ValueError:
        bot.reply_to(message, 'Durée invalide (1 à 3650 jours).'); return
    ok, result = xray_core.create_xray_account(protocol, parts[1], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result)
    _notify_created(ok, parts[1], message.from_user.id, protocol.upper(), days, result)


@bot.message_handler(commands=['zivpn_create'])
def zivpn_create(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 4: bot.reply_to(message, 'Usage : /zivpn_create <utilisateur> <mot_de_passe> <jours>'); return
    try:
        days = int(parts[3])
        if days < 1 or days > 3650: raise ValueError
    except ValueError:
        bot.reply_to(message, 'Durée invalide (1 à 3650 jours).'); return
    ok, result = zivpn_core.create_zivpn_account(parts[1], parts[2], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result)
    _notify_created(ok, parts[1], message.from_user.id, 'ZIVPN', days, result)


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid, data = call.from_user.id, call.data or ''
    try:
        if data.startswith(('reply:', 'activate:', 'suspend:', 'info:')) and not is_super_admin(uid):
            bot.answer_callback_query(call.id, 'Action réservée au super administrateur.', show_alert=True); return
        if data == 'inbox' and not is_super_admin(uid):
            bot.answer_callback_query(call.id, 'Accès refusé.', show_alert=True); return
        if data.startswith('reply:'):
            target = int(data.split(':', 1)[1]); store.set_state(uid, 'reply', target_id=target)
            bot.answer_callback_query(call.id, 'Envoyez votre réponse.'); bot.send_message(uid, f'Réponse destinée à <code>{target}</code>. /cancel annule.'); return
        if data.startswith('activate:') or data.startswith('suspend:'):
            action, target = data.split(':', 1); target = int(target)
            row = store.account(target)
            if row:
                store.set_account_status(target, 'active' if action == 'activate' else 'suspended', 'user')
                bot.answer_callback_query(call.id, 'Compte activé.' if action == 'activate' else 'Compte suspendu.')
                bot.send_message(target, '✅ Votre compte a été activé.' if action == 'activate' else '⛔ Votre compte a été suspendu.')
                store.save_message(target, 'admin', 'Compte activé' if action == 'activate' else 'Compte suspendu')
            return
        if data.startswith('info:'):
            target = int(data.split(':', 1)[1]); row = store.account(target)
            if not row: bot.answer_callback_query(call.id, 'Informations introuvables.'); return
            history = store.history(target, 8)
            text = (f"Utilisateur : <code>{target}</code>\nNom : {safe(row['first_name'])}\n"
                    f"Username : @{safe(row['username']) if row['username'] else '—'}\n"
                    f"Rôle : {safe(row['role'])}\nStatut : {safe(row['status'])}\n"
                    f"Créé : {safe(row['created_at'])}\n\nÉchanges : {len(history)}")
            bot.send_message(uid, text, reply_markup=contact_keyboard(target)); bot.answer_callback_query(call.id); return
        if data == 'inbox':
            rows = store.pending_accounts()
            if not rows: bot.send_message(uid, 'Aucun utilisateur en attente.'); return
            for row in rows:
                label = f"{row['first_name']} (@{row['username']})" if row['username'] else row['first_name'] or 'Utilisateur'
                bot.send_message(uid, f"📩 <b>{safe(label)}</b> — <code>{row['telegram_id']}</code>\nStatut : {safe(row['status'])}", reply_markup=contact_keyboard(row['telegram_id']))
            return
        if data == 'broadcast_menu':
            if not is_super_admin(uid): bot.answer_callback_query(call.id, 'Accès refusé.', show_alert=True); return
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(types.InlineKeyboardButton('Administrateurs actifs', callback_data='broadcast:admins'),
                   types.InlineKeyboardButton('Revendeurs actifs', callback_data='broadcast:resellers'),
                   types.InlineKeyboardButton('Annuler', callback_data='cancel'))
            bot.send_message(uid, 'Choisissez les destinataires :', reply_markup=kb); return
        if data.startswith('broadcast:'):
            if not is_super_admin(uid): bot.answer_callback_query(call.id, 'Accès refusé.', show_alert=True); return
            group = data.split(':', 1)[1]
            if group not in ('admins', 'resellers'): return
            store.set_state(uid, 'broadcast_message', target_group=group)
            bot.send_message(uid, 'Envoyez le texte à prévisualiser. /cancel annule.'); return
        if data == 'broadcast_confirm':
            if not is_super_admin(uid): bot.answer_callback_query(call.id, 'Accès refusé.', show_alert=True); return
            draft = store.get_draft(uid)
            if not draft: bot.answer_callback_query(call.id, 'Aucun brouillon.'); return
            group = draft['target_group']
            recipients = set(ADMIN_IDS if group == 'admins' else RESELLER_IDS)
            if group == 'admins' and SUPER_ADMIN_ID is not None: recipients.add(SUPER_ADMIN_ID)
            recipients.discard(uid)
            campaign = store.begin_broadcast(uid, group, draft['body'])
            store.clear_draft(uid); store.clear_state(uid)
            sent = failed = 0
            for recipient in sorted(recipients):
                try:
                    bot.send_message(recipient, draft['body'])
                    store.record_delivery(campaign, recipient, True); sent += 1
                except Exception as exc:
                    store.record_delivery(campaign, recipient, False, type(exc).__name__); failed += 1
                    logger.info('Broadcast delivery failed for chat id %s: %s', recipient, type(exc).__name__)
                time.sleep(0.05)
            totals = store.finish_broadcast(campaign)
            bot.send_message(uid, f"Diffusion terminée. Envoyés : <b>{totals['sent']}</b> ; échecs : <b>{totals['failed']}</b>.")
            return
        if data in ('broadcast_cancel', 'cancel'):
            if is_super_admin(uid): store.clear_draft(uid); store.clear_state(uid)
            bot.answer_callback_query(call.id, 'Annulé.'); return
        if not authorized_service_user(uid):
            bot.answer_callback_query(call.id, 'Compte en attente d’activation.', show_alert=True); return
        if data.startswith('menu:'):
            menu = data.split(':', 1)[1]
            labels = {'ssh': 'Gestion SSH — /ssh_create, /ssh_list, /ssh_delete',
                      'xray': 'Gestion XRAY — /vless_create, /vmess_create, /trojan_create',
                      'zivpn': 'Gestion ZIVPN — /zivpn_create <utilisateur> <mot_de_passe> <jours>',
                      'status': f"📊 Statut du compte : {'actif' if registered_user(uid) or is_reseller(uid) else 'en attente'}"}
            if menu == 'admins' and is_admin(uid):
                bot.send_message(uid, '👥 Administrateurs configurés :\n' + '\n'.join(f'<code>{i}</code>' for i in sorted(ADMIN_IDS))); return
            bot.send_message(uid, labels.get(menu, 'Menu indisponible.')); return
        bot.answer_callback_query(call.id)
    except Exception:
        logger.exception('Callback processing failed')
        try: bot.answer_callback_query(call.id, 'Erreur interne. Consultez les logs.')
        except Exception: pass


@bot.message_handler(func=lambda message: True, content_types=['text'])
def text_handler(message):
    uid = message.from_user.id
    text = (message.text or '').strip()
    if not text or text.startswith('/'):
        return
    state = store.get_state(uid)
    if is_super_admin(uid) and state and state['action'] == 'reply' and state['target_id']:
        target = int(state['target_id'])
        try:
            bot.send_message(target, f'💬 Réponse du super administrateur :\n\n{safe(text)}')
            store.save_message(target, 'admin', text); store.clear_state(uid)
            bot.reply_to(message, 'Réponse envoyée. Le compte conserve son statut actuel.')
        except Exception:
            logger.exception('User reply delivery failed'); bot.reply_to(message, 'Envoi impossible (utilisateur inaccessible).')
        return
    if is_super_admin(uid) and state and state['action'] == 'broadcast_message':
        group = state.get('target_group')
        if group not in ('admins', 'resellers'):
            store.clear_state(uid); bot.reply_to(message, 'Groupe de diffusion invalide.'); return
        store.save_draft(uid, group, text); store.clear_state(uid)
        preview = types.InlineKeyboardMarkup(row_width=2)
        preview.add(types.InlineKeyboardButton('✅ Confirmer', callback_data='broadcast_confirm'),
                    types.InlineKeyboardButton('❌ Annuler', callback_data='broadcast_cancel'))
        bot.send_message(uid, f"<b>Prévisualisation ({'administrateurs' if group == 'admins' else 'revendeurs'})</b>\n\n{safe(text)}", reply_markup=preview)
        return
    if not authorized_service_user(uid):
        store.upsert_account(uid, message.from_user.first_name, message.from_user.username, 'user', 'pending')
        store.save_message(uid, 'user', text)
        if SUPER_ADMIN_ID is None:
            bot.reply_to(message, 'Votre message ne peut pas être transmis : le super administrateur n’est pas configuré.')
            return
        label = f"{message.from_user.first_name or 'Sans nom'} (@{message.from_user.username})" if message.from_user.username else (message.from_user.first_name or 'Sans nom')
        try:
            bot.send_message(SUPER_ADMIN_ID, f"📩 <b>Nouveau contact (en attente)</b>\nNom : {safe(label)}\nID Telegram : <code>{uid}</code>\nMessage :\n{safe(text)}", reply_markup=contact_keyboard(uid))
            bot.reply_to(message, 'Message transmis au super administrateur. Votre compte reste en attente d’activation.')
        except Exception:
            logger.exception('Contact forwarding failed'); bot.reply_to(message, 'Impossible de transmettre le message pour le moment. Réessayez plus tard.')
        return
    bot.reply_to(message, 'Utilisez /help pour voir les commandes disponibles ou /contact pour joindre le super administrateur.')


def main():
    logger.info('KATASHIE Telegram bot starting; config=%s', CONFIG_PATH)
    try:
        bot.remove_webhook()
        bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=False)
    except KeyboardInterrupt:
        logger.info('Bot shutdown requested')
    except Exception:
        logger.exception('Polling stopped unexpectedly')
        raise

if __name__ == '__main__':
    main()
