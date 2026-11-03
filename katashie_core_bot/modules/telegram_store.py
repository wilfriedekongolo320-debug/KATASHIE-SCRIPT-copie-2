#!/usr/bin/env python3
"""SQLite persistence for Telegram pending users, conversations and broadcasts."""
from __future__ import annotations
import os
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK = threading.RLock()
_DB_PATH = os.environ.get('KATASHIE_BOT_DB', '/var/lib/katashie_bot/state.sqlite3')
def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(_DB_PATH)), mode=0o700, exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=15); db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA busy_timeout=15000')
    return db

def initialize():
    with _LOCK, _connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS accounts(telegram_id INTEGER PRIMARY KEY,first_name TEXT NOT NULL DEFAULT '',username TEXT NOT NULL DEFAULT '',role TEXT NOT NULL DEFAULT 'user',status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_id INTEGER NOT NULL,direction TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_state(admin_id INTEGER PRIMARY KEY,action TEXT NOT NULL,target_id INTEGER,target_group TEXT,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS broadcast_drafts(admin_id INTEGER PRIMARY KEY,target_group TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS broadcasts(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER NOT NULL,target_group TEXT NOT NULL,body TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,completed_at TEXT);
        CREATE TABLE IF NOT EXISTS broadcast_delivery(broadcast_id INTEGER NOT NULL,recipient_id INTEGER NOT NULL,success INTEGER NOT NULL,error TEXT NOT NULL DEFAULT '',PRIMARY KEY(broadcast_id,recipient_id));
        CREATE TABLE IF NOT EXISTS notified_events(event_key TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        ''')
def upsert_account(user_id,first_name='',username='',role='user',status='pending'):
    with _LOCK,_connect() as db:
        db.execute('''INSERT INTO accounts VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET first_name=excluded.first_name,username=excluded.username,updated_at=excluded.updated_at''',(int(user_id),first_name or '',username or '',role,status,_now(),_now()))
def account(user_id):
    with _LOCK,_connect() as db:
        row=db.execute('SELECT * FROM accounts WHERE telegram_id=?',(int(user_id),)).fetchone(); return dict(row) if row else None
def set_account_status(user_id,status,role=None):
    with _LOCK,_connect() as db:
        if role is None: db.execute('UPDATE accounts SET status=?,updated_at=? WHERE telegram_id=?',(status,_now(),int(user_id)))
        else: db.execute('UPDATE accounts SET status=?,role=?,updated_at=? WHERE telegram_id=?',(status,role,_now(),int(user_id)))
def pending_accounts(limit=30):
    with _LOCK,_connect() as db: return [dict(r) for r in db.execute("SELECT * FROM accounts WHERE status='pending' ORDER BY updated_at DESC LIMIT ?",(limit,))]
def save_message(user_id,direction,body):
    with _LOCK,_connect() as db: db.execute('INSERT INTO messages(telegram_id,direction,body,created_at) VALUES(?,?,?,?)',(int(user_id),direction,body,_now()))
def history(user_id,limit=20):
    with _LOCK,_connect() as db: return [dict(r) for r in db.execute('SELECT direction,body,created_at FROM messages WHERE telegram_id=? ORDER BY id DESC LIMIT ?',(int(user_id),limit))][::-1]
def set_state(admin_id,action,target_id=None,target_group=None):
    with _LOCK,_connect() as db: db.execute('INSERT INTO admin_state VALUES(?,?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET action=excluded.action,target_id=excluded.target_id,target_group=excluded.target_group,updated_at=excluded.updated_at',(int(admin_id),action,target_id,target_group,_now()))
def get_state(admin_id):
    with _LOCK,_connect() as db:
        row=db.execute('SELECT * FROM admin_state WHERE admin_id=?',(int(admin_id),)).fetchone(); return dict(row) if row else None
def clear_state(admin_id):
    with _LOCK,_connect() as db: db.execute('DELETE FROM admin_state WHERE admin_id=?',(int(admin_id),))
def save_draft(admin_id,target_group,body):
    with _LOCK,_connect() as db: db.execute('INSERT INTO broadcast_drafts VALUES(?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET target_group=excluded.target_group,body=excluded.body,created_at=excluded.created_at',(int(admin_id),target_group,body,_now()))
def get_draft(admin_id):
    with _LOCK,_connect() as db:
        row=db.execute('SELECT * FROM broadcast_drafts WHERE admin_id=?',(int(admin_id),)).fetchone(); return dict(row) if row else None
def clear_draft(admin_id):
    with _LOCK,_connect() as db: db.execute('DELETE FROM broadcast_drafts WHERE admin_id=?',(int(admin_id),))
def begin_broadcast(admin_id,group,body):
    with _LOCK,_connect() as db: return db.execute('INSERT INTO broadcasts(admin_id,target_group,body,created_at) VALUES(?,?,?,?)',(int(admin_id),group,body,_now())).lastrowid
def record_delivery(campaign,recipient,success,error=''):
    with _LOCK,_connect() as db:
        db.execute('INSERT OR IGNORE INTO broadcast_delivery VALUES(?,?,?,?)',(int(campaign),int(recipient),int(bool(success)),(error or '')[:300]))
        db.execute('UPDATE broadcasts SET sent=(SELECT COUNT(*) FROM broadcast_delivery WHERE broadcast_id=? AND success=1),failed=(SELECT COUNT(*) FROM broadcast_delivery WHERE broadcast_id=? AND success=0) WHERE id=?',(int(campaign),int(campaign),int(campaign)))
def finish_broadcast(campaign):
    with _LOCK,_connect() as db:
        db.execute('UPDATE broadcasts SET completed_at=? WHERE id=?',(_now(),int(campaign))); row=db.execute('SELECT sent,failed FROM broadcasts WHERE id=?',(int(campaign),)).fetchone(); return dict(row) if row else {'sent':0,'failed':0}
def log_event_once(event_key):
    with _LOCK,_connect() as db: return db.execute('INSERT OR IGNORE INTO notified_events VALUES(?,?)',(str(event_key),_now())).rowcount==1
