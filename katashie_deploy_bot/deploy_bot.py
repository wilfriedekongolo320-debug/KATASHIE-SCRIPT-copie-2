#!/usr/bin/env python3
"""
KATASHIE VPN — Bot Deploy Multi-VPS v2
Déploie KATASHIE VPN sur plusieurs VPS en parallèle via SSH Paramiko
Contact: @abess237
"""
import telebot, paramiko, json, os, threading, time, logging
from telebot import types

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('katashie-deploy')

CONFIG_PATH = os.environ.get('KATASHIE_DEPLOY_CONFIG', '/etc/katashie_deploy_bot/config.json')
DEFAULT_CFG = {'bot_token': '', 'admin_ids': []}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        cfg = {**DEFAULT_CFG, **json.load(f)}
else:
    cfg = DEFAULT_CFG

BOT_TOKEN  = cfg.get('bot_token', '')
ADMIN_IDS  = cfg.get('admin_ids', [])
INSTALL_URL = cfg.get('install_url', 'https://raw.githubusercontent.com/wilfriedekongolo320-debug/KATASHIE-SCRIPT-copie-2/main')

if not BOT_TOKEN:
    raise SystemExit('Missing Telegram bot token. Set KATASHIE_DEPLOY_CONFIG or /etc/katashie_deploy_bot/config.json')

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# VPS registry: stored in memory + config file
vps_registry: dict[str, dict] = {}
if os.path.exists('/etc/katashie_deploy_bot/vps_registry.json'):
    with open('/etc/katashie_deploy_bot/vps_registry.json') as f:
        vps_registry = json.load(f)

def save_registry():
    os.makedirs('/etc/katashie_deploy_bot', exist_ok=True)
    with open('/etc/katashie_deploy_bot/vps_registry.json', 'w') as f:
        json.dump(vps_registry, f, indent=2)

def is_admin(uid): return uid in ADMIN_IDS
def require_admin(msg):
    if not is_admin(msg.from_user.id):
        bot.reply_to(msg, "⛔ Accès réservé aux administrateurs.")
        return False
    return True

BANNER = "🚀 <b>KATASHIE VPN Deploy Bot</b>\n📲 @abess237"

def main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Ajouter VPS", callback_data="add_vps"),
        types.InlineKeyboardButton("📋 Lister VPS", callback_data="list_vps"),
        types.InlineKeyboardButton("🚀 Déployer 1 VPS", callback_data="deploy_one"),
        types.InlineKeyboardButton("🌐 Déployer TOUS", callback_data="deploy_all"),
        types.InlineKeyboardButton("🔍 Tester VPS", callback_data="test_vps"),
        types.InlineKeyboardButton("🗑 Supprimer VPS", callback_data="remove_vps"),
    )
    return kb

def deploy_to_vps(host, port, user, password, ssh_key, vps_name, chat_id, msg_id):
    """Deploy KATASHIE VPN on a single VPS with live streaming"""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if ssh_key:
            key = paramiko.RSAKey.from_private_key_file(ssh_key)
            client.connect(host, port=int(port), username=user, pkey=key, timeout=30)
        else:
            client.connect(host, port=int(port), username=user, password=password, timeout=30)

        cmd = f"wget -qO /tmp/katashie_install.sh '{INSTALL_URL}' && bash /tmp/katashie_install.sh 2>&1"
        _, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=600)

        output_lines = []
        last_update = time.time()
        update_interval = 5

        for line in stdout:
            line_str = line.strip()
            if line_str:
                output_lines.append(line_str)

            if time.time() - last_update > update_interval:
                last_update = time.time()
                recent = '\n'.join(output_lines[-8:]) if output_lines else '...'
                try:
                    bot.edit_message_text(
                        f"🚀 <b>Déploiement: {vps_name}</b> ({host})\n\n<pre>{recent[:800]}</pre>",
                        chat_id, msg_id
                    )
                except Exception: pass

        exit_code = stdout.channel.recv_exit_status()
        final_output = '\n'.join(output_lines[-20:])
        client.close()

        if exit_code == 0:
            bot.edit_message_text(
                f"✅ <b>Déploiement réussi: {vps_name}</b> ({host})\n\n<pre>{final_output[:800]}</pre>",
                chat_id, msg_id
            )
            return True
        else:
            bot.edit_message_text(
                f"❌ <b>Déploiement échoué: {vps_name}</b> ({host}) — code {exit_code}\n\n<pre>{final_output[:800]}</pre>",
                chat_id, msg_id
            )
            return False

    except paramiko.AuthenticationException:
        bot.edit_message_text(f"❌ <b>{vps_name}</b>: Erreur d'authentification SSH", chat_id, msg_id)
        return False
    except paramiko.NoValidConnectionsError:
        bot.edit_message_text(f"❌ <b>{vps_name}</b>: Impossible de se connecter à {host}:{port}", chat_id, msg_id)
        return False
    except Exception as e:
        bot.edit_message_text(f"❌ <b>{vps_name}</b>: {str(e)[:200]}", chat_id, msg_id)
        return False

def deploy_all_parallel(vps_list, chat_id):
    """Deploy to multiple VPS in parallel threads"""
    threads = []
    results = {}

    bot.send_message(chat_id, f"🌐 Déploiement sur <b>{len(vps_list)}</b> serveur(s) en parallèle...")

    for name, vps in vps_list.items():
        msg = bot.send_message(chat_id, f"⏳ <b>{name}</b> ({vps['host']}): Connexion...")

        def deploy_thread(n=name, v=vps, m=msg):
            ok = deploy_to_vps(
                v['host'], v.get('port', 22), v['user'],
                v.get('password'), v.get('ssh_key'),
                n, chat_id, m.message_id
            )
            results[n] = ok

        t = threading.Thread(target=deploy_thread)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=700)

    success = sum(1 for v in results.values() if v)
    total = len(results)
    bot.send_message(chat_id, f"📊 <b>Résultat final:</b> {success}/{total} serveurs déployés avec succès")

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
@bot.message_handler(commands=['start', 'help'])
def start(msg):
    if not require_admin(msg): return
    bot.send_message(msg.chat.id, f"{BANNER}\n\nGérez vos déploiements multi-VPS:", reply_markup=main_keyboard())

@bot.message_handler(commands=['add_vps'])
def add_vps_cmd(msg):
    if not require_admin(msg): return
    parts = msg.text.split()
    if len(parts) < 5:
        bot.reply_to(msg, "Usage: /add_vps &lt;nom&gt; &lt;host&gt; &lt;port&gt; &lt;user&gt; &lt;password&gt;")
        return
    name, host, port, user, password = parts[1], parts[2], parts[3], parts[4], parts[5] if len(parts) > 5 else ''
    vps_registry[name] = {'host': host, 'port': int(port), 'user': user, 'password': password}
    save_registry()
    bot.reply_to(msg, f"✅ VPS <b>{name}</b> ajouté ({host}:{port})")

@bot.message_handler(commands=['list_vps'])
def list_vps_cmd(msg):
    if not require_admin(msg): return
    if not vps_registry:
        bot.reply_to(msg, "Aucun VPS configuré. Utilisez /add_vps")
        return
    lines = [f"📋 <b>VPS configurés ({len(vps_registry)}):</b>"]
    for name, v in vps_registry.items():
        lines.append(f"• <b>{name}</b>: {v['user']}@{v['host']}:{v.get('port',22)}")
    bot.reply_to(msg, '\n'.join(lines))

@bot.message_handler(commands=['deploy'])
def deploy_cmd(msg):
    if not require_admin(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /deploy &lt;nom_vps&gt; ou /deploy all\n\nVPS disponibles: " + ', '.join(vps_registry.keys()))
        return

    target = parts[1].lower()
    if target == 'all':
        deploy_all_parallel(vps_registry, msg.chat.id)
    elif target in vps_registry:
        vps = vps_registry[target]
        progress_msg = bot.send_message(msg.chat.id, f"⏳ Connexion à <b>{target}</b> ({vps['host']})...")
        threading.Thread(target=deploy_to_vps, args=(
            vps['host'], vps.get('port', 22), vps['user'],
            vps.get('password'), vps.get('ssh_key'),
            target, msg.chat.id, progress_msg.message_id
        ), daemon=True).start()
    else:
        bot.reply_to(msg, f"VPS <b>{target}</b> inconnu. Disponibles: {', '.join(vps_registry.keys())}")

@bot.message_handler(commands=['test_vps'])
def test_vps_cmd(msg):
    if not require_admin(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.reply_to(msg, "Usage: /test_vps &lt;nom_vps&gt;")
        return
    name = parts[1]
    if name not in vps_registry:
        bot.reply_to(msg, f"VPS {name} inconnu")
        return
    vps = vps_registry[name]
    bot.send_message(msg.chat.id, f"🔍 Test de connexion à <b>{name}</b>...")
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(vps['host'], port=vps.get('port', 22), username=vps['user'],
                       password=vps.get('password'), timeout=15)
        _, stdout, _ = client.exec_command("uname -a && uptime && free -h | head -2")
        out = stdout.read().decode()
        client.close()
        bot.send_message(msg.chat.id, f"✅ <b>{name}</b> connecté:\n<pre>{out}</pre>")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ <b>{name}</b>: {str(e)}")

# ─── INLINE CALLBACKS ─────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Accès refusé"); return
    bot.answer_callback_query(call.id)

    if call.data == "list_vps":
        if not vps_registry:
            bot.send_message(call.message.chat.id, "Aucun VPS. Utilisez /add_vps <nom> <host> <port> <user> <password>")
        else:
            lines = [f"📋 <b>VPS ({len(vps_registry)}):</b>"]
            for name, v in vps_registry.items():
                lines.append(f"• <b>{name}</b>: {v['user']}@{v['host']}:{v.get('port',22)}")
            bot.send_message(call.message.chat.id, '\n'.join(lines))

    elif call.data == "deploy_all":
        if not vps_registry:
            bot.send_message(call.message.chat.id, "Aucun VPS configuré")
        else:
            threading.Thread(target=deploy_all_parallel, args=(dict(vps_registry), call.message.chat.id), daemon=True).start()

    elif call.data in ["add_vps", "deploy_one", "test_vps", "remove_vps"]:
        hints = {
            "add_vps": "/add_vps &lt;nom&gt; &lt;host&gt; &lt;port&gt; &lt;user&gt; &lt;password&gt;",
            "deploy_one": "/deploy &lt;nom_vps&gt;",
            "test_vps": "/test_vps &lt;nom_vps&gt;",
            "remove_vps": "/remove_vps &lt;nom_vps&gt;",
        }
        bot.send_message(call.message.chat.id, f"📝 {hints[call.data]}")

if __name__ == '__main__':
    logger.info("KATASHIE Deploy Bot v2 starting...")
    bot.infinity_polling(timeout=30, long_polling_timeout=15)
