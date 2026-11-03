#!/usr/bin/env python3
"""KATASHIE Telegram bot. VPN operations remain in the existing modules."""
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import telebot
from telebot import types

from modules import ssh_core, xray_core, zivpn_core
from modules import telegram_store as store

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('katashie-bot')
CONFIG_PATH = os.environ.get('KATASHIE_BOT_CONFIG', '/etc/katashie_bot/config.json')
try:
    with open(CONFIG_PATH, encoding='utf-8') as config_file:
        cfg = json.load(config_file)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f'Cannot read bot config {CONFIG_PATH}: {exc}')

TOKEN = str(cfg.get('bot_token') or '').strip()
if not TOKEN or TOKEN == 'VOTRE_TOKEN_TELEGRAM':
    raise SystemExit('Missing real Telegram bot token in config (bot_token).')

def _ids(*sources):
    found = set()
    for source in sources:
        for value in (source if isinstance(source, (list, tuple, set)) else [source]):
            try:
                if value is not None and str(value).strip():
                    found.add(int(value))
            except (TypeError, ValueError):
                logger.warning('Ignoring invalid configured Telegram ID')
    return found

SUPER_ADMIN_ID = next(iter(_ids(cfg.get('super_admin'), cfg.get('super_admin_id'))), None)
ADMIN_IDS = _ids(cfg.get('admin_ids'), cfg.get('admins'))
RESELLER_IDS = _ids(cfg.get('reseller_ids'))
if SUPER_ADMIN_ID is None:
    logger.warning('No super admin configured; contact forwarding and broadcasts are unavailable.')

store.initialize()
bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True, num_threads=4)
BANNER = '🛡 <b>KATASHIE VPN</b> — Panel Telegram'


def safe(value):
    return html.escape(str(value or ''), quote=False)


def is_super_admin(user_id):
    return SUPER_ADMIN_ID is not None and int(user_id) == SUPER_ADMIN_ID


def is_admin(user_id):
    return is_super_admin(user_id) or int(user_id) in ADMIN_IDS


def is_reseller(user_id):
    return is_admin(user_id) or int(user_id) in RESELLER_IDS


def active_user(user_id):
    row = store.account(user_id)
    return bool(row and row['status'] == 'active')


def allowed_user(user_id):
    return is_reseller(user_id) or active_user(user_id)


def role_for(user_id):
    if is_super_admin(user_id): return 'super_admin'
    if int(user_id) in ADMIN_IDS: return 'admin'
    if int(user_id) in RESELLER_IDS: return 'reseller'
    return 'user'


def main_keyboard(admin=False, super_admin=False):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('SSH', callback_data='menu:ssh'),
           types.InlineKeyboardButton('XRAY', callback_data='menu:xray'),
           types.InlineKeyboardButton('ZIVPN', callback_data='menu:zivpn'),
           types.InlineKeyboardButton('Statut', callback_data='menu:status'))
    if admin: kb.add(types.InlineKeyboardButton('Administrateurs', callback_data='menu:admins'))
    if super_admin:
        kb.add(types.InlineKeyboardButton('Messages en attente', callback_data='inbox'),
               types.InlineKeyboardButton('Diffusion', callback_data='broadcast_menu'))
    return kb


def contact_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('Répondre', callback_data=f'reply:{user_id}'),
           types.InlineKeyboardButton('Activer le compte', callback_data=f'activate:{user_id}'),
           types.InlineKeyboardButton('Suspendre le compte', callback_data=f'suspend:{user_id}'),
           types.InlineKeyboardButton('Voir les informations', callback_data=f'info:{user_id}'),
           types.InlineKeyboardButton('Retour', callback_data='inbox'))
    return kb


def notify_superadmin(protocol, username, creator, days, expiry, event_key):
    if SUPER_ADMIN_ID is None or not store.log_event_once(event_key): return
    created = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    text = (f'🆕 <b>Configuration VPN créée</b>\n\nType : <b>{safe(protocol)}</b>\n'
            f'Utilisateur : <code>{safe(username)}</code>\nCréateur Telegram ID : <code>{creator}</code>\n'
            f'Date : {created}\nDurée : {days} jour(s)\nExpiration : <code>{safe(expiry)}</code>')
    try: bot.send_message(SUPER_ADMIN_ID, text)
    except Exception: logger.exception('Could not send VPN creation notification')


def notify_created(ok, username, actor, protocol, days, result):
    if not ok: return
    found = re.search(r'(?:Expiration|Nouvelle exp\.)\s*:??\s*<code>([^<]+)', str(result), re.I)
    expiry = found.group(1) if found else 'non précisée'
    notify_superadmin(protocol, username, actor, days, expiry, f'{protocol}:{username}:{expiry}:{actor}')


@bot.message_handler(commands=['start', 'help'])
def start(message):
    uid = message.from_user.id
    role = role_for(uid)
    status = 'active' if role != 'user' or active_user(uid) else 'pending'
    store.upsert_account(uid, message.from_user.first_name, message.from_user.username, role, status)
    if status != 'active':
        bot.send_message(message.chat.id, 'Bienvenue. Votre compte est en attente d’activation. Envoyez un message ici pour contacter le super administrateur. Aucun privilège ne sera accordé avant son activation.')
        return
    bot.send_message(message.chat.id, f'{BANNER}\nBonjour <b>{safe(message.from_user.first_name)}</b> — {role}.\n/help • /status • /contact', reply_markup=main_keyboard(is_admin(uid), is_super_admin(uid)))


@bot.message_handler(commands=['contact'])
def contact(message):
    uid = message.from_user.id
    role = role_for(uid)
    store.upsert_account(uid, message.from_user.first_name, message.from_user.username, role,
                         'active' if role != 'user' or active_user(uid) else 'pending')
    bot.reply_to(message, 'Écrivez votre message ici. Il sera transmis au super administrateur.')


@bot.message_handler(commands=['status'])
def status(message):
    uid = message.from_user.id
    row = store.account(uid)
    state = 'Actif' if is_reseller(uid) or (row and row['status'] == 'active') else ('Suspendu' if row and row['status'] == 'suspended' else 'En attente d’activation')
    bot.reply_to(message, f'Statut du compte : <b>{state}</b>')


@bot.message_handler(commands=['admin'])
def admin(message):
    uid = message.from_user.id
    if not is_admin(uid): bot.reply_to(message, '⛔ Accès réservé aux administrateurs.'); return
    bot.send_message(message.chat.id, 'Menu administratif.', reply_markup=main_keyboard(True, is_super_admin(uid)))


@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if not is_super_admin(message.from_user.id): bot.reply_to(message, '⛔ Fonction réservée au super administrateur.'); return
    args = (message.text or '').split(maxsplit=1)
    if len(args) != 2 or args[1].lower() not in ('admins', 'administrateurs', 'resellers', 'revendeurs'):
        bot.reply_to(message, 'Usage : /broadcast admins ou /broadcast revendeurs'); return
    group = 'admins' if args[1].lower() in ('admins', 'administrateurs') else 'resellers'
    store.set_state(message.from_user.id, 'broadcast_message', target_group=group)
    bot.reply_to(message, 'Envoyez le message pour prévisualisation. /cancel annule.')


@bot.message_handler(commands=['cancel'])
def cancel(message):
    store.clear_state(message.from_user.id); store.clear_draft(message.from_user.id)
    bot.reply_to(message, 'Opération annulée.')


@bot.message_handler(commands=['ssh_create'])
def ssh_create(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 4: bot.reply_to(message, 'Usage : /ssh_create <utilisateur> <mot_de_passe> <jours>'); return
    try:
        days = int(parts[3])
        if not 1 <= days <= 3650: raise ValueError
    except ValueError: bot.reply_to(message, 'Durée invalide (1–3650 jours).'); return
    ok, result = ssh_core.create_ssh_account(parts[1], parts[2], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result); notify_created(ok, parts[1], message.from_user.id, 'SSH', days, result)


@bot.message_handler(commands=['ssh_list'])
def ssh_list(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    bot.reply_to(message, '\n'.join(ssh_core.get_ssh_usernames()) or 'Aucun utilisateur SSH.')


@bot.message_handler(commands=['ssh_delete'])
def ssh_delete(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 2: bot.reply_to(message, 'Usage : /ssh_delete <utilisateur>'); return
    _, result = ssh_core.delete_ssh_account(parts[1]); bot.reply_to(message, result)


@bot.message_handler(commands=['vless_create', 'vmess_create', 'trojan_create'])
def xray_create(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    command = parts[0].split('@')[0].lstrip('/')
    protocol = command.removesuffix('_create').lower()
    if len(parts) < 3: bot.reply_to(message, f'Usage : /{command} <utilisateur> <jours>'); return
    try:
        days = int(parts[2])
        if not 1 <= days <= 3650: raise ValueError
    except ValueError: bot.reply_to(message, 'Durée invalide (1–3650 jours).'); return
    ok, result = xray_core.create_xray_account(protocol, parts[1], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result); notify_created(ok, parts[1], message.from_user.id, protocol.upper(), days, result)


@bot.message_handler(commands=['zivpn_create'])
def zivpn_create(message):
    if not is_reseller(message.from_user.id): bot.reply_to(message, '⛔ Accès revendeur requis.'); return
    parts = (message.text or '').split()
    if len(parts) < 4: bot.reply_to(message, 'Usage : /zivpn_create <utilisateur> <mot_de_passe> <jours>'); return
    try:
        days = int(parts[3])
        if not 1 <= days <= 3650: raise ValueError
    except ValueError: bot.reply_to(message, 'Durée invalide (1–3650 jours).'); return
    ok, result = zivpn_core.create_zivpn_account(parts[1], parts[2], days, created_by_id=message.from_user.id)
    bot.reply_to(message, result); notify_created(ok, parts[1], message.from_user.id, 'ZIVPN', days, result)


def _broadcast_targets(group):
    configured = set(ADMIN_IDS if group == 'admins' else RESELLER_IDS)
    role = 'admin' if group == 'admins' else 'reseller'
    targets = configured | store.active_ids(role)
    return {uid for uid in targets if not store.is_suspended(uid)}


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid, data = call.from_user.id, call.data or ''
    try:
        if data.startswith(('reply:', 'activate:', 'suspend:', 'info:', 'broadcast')) and not is_super_admin(uid):
            bot.answer_callback_query(call.id, 'Réservé au super administrateur.', show_alert=True); return
        if data == 'inbox' and not is_super_admin(uid):
            bot.answer_callback_query(call.id, 'Accès refusé.', show_alert=True); return
        if data.startswith('reply:'):
            target = int(data.split(':', 1)[1]); store.set_state(uid, 'reply', target_id=target)
            bot.answer_callback_query(call.id); bot.send_message(uid, f'Envoyez votre réponse à <code>{target}</code>. /cancel annule.'); return
        if data.startswith(('activate:', 'suspend:')):
            action, raw_id = data.split(':', 1); target = int(raw_id); row = store.account(target)
            if not row: bot.answer_callback_query(call.id, 'Utilisateur introuvable.'); return
            new_status = 'active' if action == 'activate' else 'suspended'
            store.set_account_status(target, new_status, 'user')
            bot.answer_callback_query(call.id, 'Compte activé.' if new_status == 'active' else 'Compte suspendu.')
            bot.send_message(target, 'Votre compte a été activé.' if new_status == 'active' else 'Votre compte a été suspendu.')
            store.save_message(target, 'admin', f'Compte {new_status}')
            return
        if data.startswith('info:'):
            target = int(data.split(':', 1)[1]); row = store.account(target)
            if not row: bot.answer_callback_query(call.id, 'Utilisateur introuvable.'); return
            text = (f"Utilisateur : <code>{target}</code>\nNom : {safe(row['first_name'])}\n"
                    f"Nom Telegram : @{safe(row['username']) if row['username'] else '—'}\n"
                    f"Rôle : {safe(row['role'])}\nStatut : {safe(row['status'])}\n"
                    f"Messages conservés : {len(store.history(target, 100))}")
            bot.answer_callback_query(call.id); bot.send_message(uid, text, reply_markup=contact_keyboard(target)); return
        if data == 'inbox':
            pending = store.pending_accounts()
            if not pending: bot.answer_callback_query(call.id, 'Aucun utilisateur en attente.'); return
            bot.answer_callback_query(call.id)
            for row in pending:
                label = f"{row['first_name']} (@{row['username']})" if row['username'] else row['first_name'] or 'Utilisateur'
                bot.send_message(uid, f"📩 <b>{safe(label)}</b> — <code>{row['telegram_id']}</code>", reply_markup=contact_keyboard(row['telegram_id']))
            return
        if data == 'broadcast_menu':
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(types.InlineKeyboardButton('Administrateurs actifs', callback_data='broadcast:admins'),
                   types.InlineKeyboardButton('Revendeurs actifs', callback_data='broadcast:resellers'),
                   types.InlineKeyboardButton('Annuler', callback_data='broadcast_cancel'))
            bot.answer_callback_query(call.id); bot.send_message(uid, 'Choisissez les destinataires :', reply_markup=kb); return
        if data.startswith('broadcast:'):
            group = data.split(':', 1)[1]
            if group not in ('admins', 'resellers'): bot.answer_callback_query(call.id, 'Groupe invalide.'); return
            store.set_state(uid, 'broadcast_message', target_group=group)
            bot.answer_callback_query(call.id); bot.send_message(uid, 'Envoyez le message à prévisualiser. /cancel annule.'); return
        if data == 'broadcast_confirm':
            draft = store.get_draft(uid)
            if not draft: bot.answer_callback_query(call.id, 'Brouillon introuvable.'); return
            recipients = _broadcast_targets(draft['target_group'])
            recipients.discard(uid)
            campaign = store.begin_broadcast(uid, draft['target_group'], draft['body'])
            store.clear_draft(uid); store.clear_state(uid)
            for recipient in sorted(recipients):
                try:
                    bot.send_message(recipient, draft['body'])
                    store.record_delivery(campaign, recipient, True)
                except Exception as exc:
                    store.record_delivery(campaign, recipient, False, type(exc).__name__)
                    logger.warning('Broadcast send failed (recipient id %s): %s', recipient, type(exc).__name__)
                time.sleep(1.05)
            totals = store.finish_broadcast(campaign)
            bot.answer_callback_query(call.id, 'Diffusion terminée.')
            bot.send_message(uid, f"Diffusion terminée. Envoyés : <b>{totals['sent']}</b> ; échecs : <b>{totals['failed']}</b>.")
            return
        if data == 'broadcast_cancel':
            store.clear_draft(uid); store.clear_state(uid)
            bot.answer_callback_query(call.id, 'Diffusion annulée.'); return
        if data == 'cancel':
            store.clear_draft(uid); store.clear_state(uid)
            bot.answer_callback_query(call.id, 'Annulé.'); return
        if not allowed_user(uid):
            bot.answer_callback_query(call.id, 'Compte en attente d’activation.', show_alert=True); return
        if data.startswith('menu:'):
            menu = data.split(':', 1)[1]
            labels = {'ssh': 'Gestion SSH : /ssh_create, /ssh_list, /ssh_delete',
                      'xray': 'Gestion XRAY : /vless_create, /vmess_create, /trojan_create',
                      'zivpn': 'Gestion ZIVPN : /zivpn_create <utilisateur> <mot_de_passe> <jours>',
                      'status': 'Utilisez /status pour consulter votre état.'}
            if menu == 'admins' and is_admin(uid):
                bot.answer_callback_query(call.id); bot.send_message(uid, 'IDs administrateurs configurés :\n' + '\n'.join(f'<code>{i}</code>' for i in sorted(ADMIN_IDS))); return
            bot.answer_callback_query(call.id); bot.send_message(uid, labels.get(menu, 'Menu indisponible.')); return
        bot.answer_callback_query(call.id)
    except Exception:
        logger.exception('Callback processing failed')
        try: bot.answer_callback_query(call.id, 'Erreur interne. Consultez les logs.')
        except Exception: pass


@bot.message_handler(func=lambda message: True, content_types=['text'])
def text_handler(message):
    uid, text = message.from_user.id, (message.text or '').strip()
    if not text or text.startswith('/'): return
    state = store.get_state(uid)
    if is_super_admin(uid) and state and state['action'] == 'reply' and state['target_id']:
        target = int(state['target_id'])
        try:
            bot.send_message(target, f'💬 Réponse du super administrateur :\n\n{safe(text)}')
            store.save_message(target, 'admin', text); store.clear_state(uid)
            bot.reply_to(message, 'Réponse envoyée. Le statut du compte n’a pas été modifié.')
        except Exception:
            logger.exception('User reply delivery failed'); bot.reply_to(message, 'Envoi impossible. Vérifiez que l’utilisateur n’a pas bloqué le bot.')
        return
    if is_super_admin(uid) and state and state['action'] == 'broadcast_message':
        group = state.get('target_group')
        if group not in ('admins', 'resellers'):
            store.clear_state(uid); bot.reply_to(message, 'Groupe invalide.'); return
        store.save_draft(uid, group, text); store.clear_state(uid)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton('Confirmer', callback_data='broadcast_confirm'),
               types.InlineKeyboardButton('Annuler', callback_data='broadcast_cancel'))
        bot.send_message(uid, f"<b>Prévisualisation ({group})</b>\n\n{safe(text)}", reply_markup=kb)
        return
    if not allowed_user(uid):
        store.upsert_account(uid, message.from_user.first_name, message.from_user.username, 'user', 'pending')
        store.save_message(uid, 'user', text)
        if SUPER_ADMIN_ID is None:
            bot.reply_to(message, 'Le super administrateur n’est pas configuré. Réessayez ultérieurement.'); return
        label = f"{message.from_user.first_name or 'Sans nom'} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name or 'Sans nom'
        try:
            bot.send_message(SUPER_ADMIN_ID, f"📩 <b>Nouveau contact (en attente)</b>\nNom : {safe(label)}\nID : <code>{uid}</code>\nMessage :\n{safe(text)}", reply_markup=contact_keyboard(uid))
            bot.reply_to(message, 'Message transmis au super administrateur. Votre compte reste en attente d’activation.')
        except Exception:
            logger.exception('Contact forwarding failed'); bot.reply_to(message, 'Transmission impossible pour le moment. Réessayez plus tard.')
        return
    bot.reply_to(message, 'Utilisez /help pour les commandes, ou /contact pour joindre le super administrateur.')


def main():
    logger.info('KATASHIE Telegram bot starting; config=%s', CONFIG_PATH)
    bot.remove_webhook()
    bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=False)

if __name__ == '__main__':
    main()
