# -*- coding: utf-8 -*-
import subprocess
import sys
import os
import threading
import time
import sqlite3
import json
import logging
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta
import requests
from flask import Flask
import telebot
from telebot import types

# ==================== AUTO INSTALL MISSING MODULES ====================
def auto_install(package):
    try:
        __import__(package)
    except ModuleNotFoundError:
        print(f"📦 Installing: {package}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

for mod in ["telebot", "psutil", "requests", "flask"]:
    auto_install(mod)

# ==================== CONFIGURATION ====================
TOKEN = '8618533412:AAFqb3BKFef4aV7DYSEGtH6Y_Fw_4YAdmyA'
OWNER_ID = 970170999
ADMIN_ID = 970170999
YOUR_USERNAME = '@notxsatvir'
UPDATE_CHANNEL = 'https://t.me/+3nStTF1MItdjMzc1'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False

# ==================== FLASK KEEP ALIVE ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 SATVIR_CODEX is Running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("✅ Flask Keep-Alive started.")

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
    conn.commit()
    conn.close()
    print("✅ Database initialized.")

def load_data():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT user_id, expiry FROM subscriptions')
    for user_id, expiry in c.fetchall():
        try:
            user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
        except:
            pass
    c.execute('SELECT user_id, file_name, file_type FROM user_files')
    for user_id, file_name, file_type in c.fetchall():
        if user_id not in user_files:
            user_files[user_id] = []
        user_files[user_id].append((file_name, file_type))
    c.execute('SELECT user_id FROM active_users')
    active_users.update(user_id for (user_id,) in c.fetchall())
    c.execute('SELECT user_id FROM admins')
    admin_ids.update(user_id for (user_id,) in c.fetchall())
    conn.close()
    print(f"✅ Data loaded: {len(active_users)} users, {len(user_subscriptions)} subs")

init_db()
load_data()

# ==================== HELPER FUNCTIONS ====================
def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_limit(user_id):
    if user_id == OWNER_ID:
        return float('inf')
    if user_id in admin_ids:
        return 999
    if user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
        return 50
    return 5

def is_bot_running(user_id, file_name):
    key = f"{user_id}_{file_name}"
    if key in bot_scripts:
        try:
            return bot_scripts[key]['process'].poll() is None
        except:
            return False
    return False

def save_file_db(user_id, file_name, file_type):
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO user_files VALUES (?, ?, ?)', (user_id, file_name, file_type))
    conn.commit()
    conn.close()
    if user_id not in user_files:
        user_files[user_id] = []
    user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
    user_files[user_id].append((file_name, file_type))

def remove_file_db(user_id, file_name):
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM user_files WHERE user_id=? AND file_name=?', (user_id, file_name))
    conn.commit()
    conn.close()
    if user_id in user_files:
        user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
        if not user_files[user_id]:
            del user_files[user_id]

def run_script(user_id, file_name, file_type, message_obj=None):
    key = f"{user_id}_{file_name}"
    if is_bot_running(user_id, file_name):
        return
    folder = get_user_folder(user_id)
    path = os.path.join(folder, file_name)
    if not os.path.exists(path):
        return
    log_path = os.path.join(folder, f"{os.path.splitext(file_name)[0]}.log")
    log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
    if file_type == 'py':
        process = subprocess.Popen([sys.executable, path], cwd=folder, stdout=log_file, stderr=log_file)
    else:
        process = subprocess.Popen(['node', path], cwd=folder, stdout=log_file, stderr=log_file)
    bot_scripts[key] = {'process': process, 'log_file': log_file, 'file_name': file_name, 'user_id': user_id}
    if message_obj:
        try:
            telebot.types.ReplyKeyboardRemove()
        except:
            pass

def stop_script(user_id, file_name):
    key = f"{user_id}_{file_name}"
    if key in bot_scripts:
        try:
            bot_scripts[key]['process'].terminate()
            time.sleep(1)
            if bot_scripts[key]['process'].poll() is None:
                bot_scripts[key]['process'].kill()
        except:
            pass
        try:
            bot_scripts[key]['log_file'].close()
        except:
            pass
        del bot_scripts[key]

def delete_script(user_id, file_name):
    stop_script(user_id, file_name)
    folder = get_user_folder(user_id)
    for f in [file_name, f"{os.path.splitext(file_name)[0]}.log"]:
        try:
            os.remove(os.path.join(folder, f))
        except:
            pass
    remove_file_db(user_id, file_name)

# ==================== KEYBOARDS ====================
def create_main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id in admin_ids:
        buttons = [
            ["📢 Updates Channel"], ["📤 Upload File", "📂 Check Files"],
            ["⚡ Bot Speed", "📊 Statistics"], ["💳 Subscriptions", "📢 Broadcast"],
            ["🔒 Lock Bot", "🟢 Running All Code"], ["👑 Admin Panel", "📞 Contact Admin"]
        ]
    else:
        buttons = [
            ["📢 Updates Channel"], ["📤 Upload File", "📂 Check Files"],
            ["⚡ Bot Speed", "📊 Statistics"], ["📞 Contact Admin"]
        ]
    for row in buttons:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_file_buttons(user_id, file_name):
    markup = types.InlineKeyboardMarkup(row_width=2)
    is_running = is_bot_running(user_id, file_name)
    markup.add(
        types.InlineKeyboardButton("🟢 Start" if not is_running else "🟢 Running", callback_data=f'start_{user_id}_{file_name}'),
        types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{user_id}_{file_name}')
    )
    markup.add(
        types.InlineKeyboardButton("🗑️ Delete", callback_data=f'del_{user_id}_{file_name}'),
        types.InlineKeyboardButton("📜 Logs", callback_data=f'log_{user_id}_{file_name}')
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_files'))
    return markup

# ==================== BOT COMMANDS ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    limit = get_user_limit(user_id)
    status = "👑 Owner" if user_id == OWNER_ID else ("🛡️ Admin" if user_id in admin_ids else ("⭐ Premium" if user_id in user_subscriptions else "🆓 Free"))
    text = f"〽️ Welcome to SATVIR_CODEX!\n🆔 ID: `{user_id}`\n🔰 Status: {status}\n📁 Files: {len(user_files.get(user_id, []))} / {limit}\n\nSend .py/.js/.zip file to run!"
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=create_main_keyboard(user_id))
    if user_id not in active_users:
        active_users.add(user_id)
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute('INSERT OR IGNORE INTO active_users VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

@bot.message_handler(func=lambda m: m.text == "📢 Updates Channel")
def updates_channel(m):
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('📢 Join Channel', url=UPDATE_CHANNEL))
    bot.reply_to(m, "Join our Updates Channel:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📤 Upload File")
def upload_file(m):
    bot.reply_to(m, "📤 Send your Python (.py), JavaScript (.js), or ZIP (.zip) file")

@bot.message_handler(func=lambda m: m.text == "📂 Check Files")
def check_files(m):
    user_id = m.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(m, "📂 No files uploaded yet.\nUse /upload to add files.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in files:
        markup.add(types.InlineKeyboardButton(f"📄 {fname} ({ftype})", callback_data=f'file_{user_id}_{fname}'))
    bot.reply_to(m, "📂 Your files:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⚡ Bot Speed")
def bot_speed(m):
    start = time.time()
    bot.send_chat_action(m.chat.id, 'typing')
    latency = round((time.time() - start) * 1000, 2)
    status = "🔒 Locked" if bot_locked else "🔓 Unlocked"
    bot.reply_to(m, f"⚡ SATVIR_CODEX\n📊 Response: {latency} ms\n🚦 Status: {status}")

@bot.message_handler(func=lambda m: m.text == "📊 Statistics")
def statistics(m):
    stats = f"📊 SATVIR_CODEX Stats\n\n👥 Users: {len(active_users)}\n📂 Files: {sum(len(f) for f in user_files.values())}\n🟢 Running Bots: {len(bot_scripts)}\n👑 Owner: {OWNER_ID}"
    bot.reply_to(m, stats)

@bot.message_handler(func=lambda m: m.text == "📞 Contact Admin")
def contact_admin(m):
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('📞 Contact', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(m, "Contact Admin:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💳 Subscriptions" and m.from_user.id in admin_ids)
def subscriptions(m):
    bot.reply_to(m, "💳 Subscription Management\n\n/addsub USER_ID DAYS\n/removesub USER_ID\n/checksub USER_ID")

@bot.message_handler(func=lambda m: m.text == "📢 Broadcast" and m.from_user.id in admin_ids)
def broadcast(m):
    msg = bot.reply_to(m, "📢 Send message to broadcast to all users.\nSend /cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast)

@bot.message_handler(func=lambda m: m.text == "🔒 Lock Bot" and m.from_user.id in admin_ids)
def lock_bot(m):
    global bot_locked
    bot_locked = True
    bot.reply_to(m, "🔒 SATVIR_CODEX Bot Locked! Only admins can use.")

@bot.message_handler(func=lambda m: m.text == "🟢 Running All Code" and m.from_user.id in admin_ids)
def run_all_code(m):
    bot.reply_to(m, "🟢 Starting all user scripts...")
    for uid, files in user_files.items():
        for fname, ftype in files:
            if not is_bot_running(uid, fname):
                run_script(uid, fname, ftype, m)
    bot.reply_to(m, "✅ All scripts started!")

@bot.message_handler(func=lambda m: m.text == "👑 Admin Panel" and m.from_user.id in admin_ids)
def admin_panel(m):
    bot.reply_to(m, "👑 Admin Panel\n\n/admins - List admins\n/addadmin ID\n/rmadmin ID")

# ==================== ADMIN COMMANDS ====================
@bot.message_handler(commands=['admins'])
def list_admins(m):
    if m.from_user.id not in admin_ids:
        return
    admins_list = "\n".join([f"👑 {aid}" if aid == OWNER_ID else f"🛡️ {aid}" for aid in sorted(admin_ids)])
    bot.reply_to(m, f"👑 Admins:\n{admins_list}")

@bot.message_handler(commands=['addadmin'])
def add_admin(m):
    if m.from_user.id != OWNER_ID:
        bot.reply_to(m, "❌ Only owner can add admins")
        return
    try:
        new_admin = int(m.text.split()[1])
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute('INSERT OR IGNORE INTO admins VALUES (?)', (new_admin,))
        conn.commit()
        conn.close()
        admin_ids.add(new_admin)
        bot.reply_to(m, f"✅ Added admin: {new_admin}")
    except:
        bot.reply_to(m, "❌ Usage: /addadmin USER_ID")

@bot.message_handler(commands=['rmadmin'])
def remove_admin(m):
    if m.from_user.id != OWNER_ID:
        bot.reply_to(m, "❌ Only owner can remove admins")
        return
    try:
        rm_admin = int(m.text.split()[1])
        if rm_admin == OWNER_ID:
            bot.reply_to(m, "❌ Cannot remove owner")
            return
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute('DELETE FROM admins WHERE user_id=?', (rm_admin,))
        conn.commit()
        conn.close()
        admin_ids.discard(rm_admin)
        bot.reply_to(m, f"✅ Removed admin: {rm_admin}")
    except:
        bot.reply_to(m, "❌ Usage: /rmadmin USER_ID")

@bot.message_handler(commands=['addsub'])
def add_subscription(m):
    if m.from_user.id not in admin_ids:
        return
    try:
        parts = m.text.split()
        uid = int(parts[1])
        days = int(parts[2])
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute('INSERT OR REPLACE INTO subscriptions VALUES (?, ?)', (uid, expiry))
        conn.commit()
        conn.close()
        user_subscriptions[uid] = {'expiry': datetime.fromisoformat(expiry)}
        bot.reply_to(m, f"✅ Added subscription for {uid} ({days} days)")
    except:
        bot.reply_to(m, "❌ Usage: /addsub USER_ID DAYS")

@bot.message_handler(commands=['removesub'])
def remove_subscription(m):
    if m.from_user.id not in admin_ids:
        return
    try:
        uid = int(m.text.split()[1])
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute('DELETE FROM subscriptions WHERE user_id=?', (uid,))
        conn.commit()
        conn.close()
        user_subscriptions.pop(uid, None)
        bot.reply_to(m, f"✅ Removed subscription for {uid}")
    except:
        bot.reply_to(m, "❌ Usage: /removesub USER_ID")

@bot.message_handler(commands=['checksub'])
def check_subscription(m):
    if m.from_user.id not in admin_ids:
        return
    try:
        uid = int(m.text.split()[1])
        if uid in user_subscriptions:
            expiry = user_subscriptions[uid]['expiry']
            days = (expiry - datetime.now()).days
            bot.reply_to(m, f"✅ User {uid} has subscription\nExpires: {expiry.strftime('%Y-%m-%d')}\nDays left: {days}")
        else:
            bot.reply_to(m, f"❌ User {uid} has no subscription")
    except:
        bot.reply_to(m, "❌ Usage: /checksub USER_ID")

# ==================== FILE HANDLER ====================
@bot.message_handler(content_types=['document'])
def handle_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "🔒 Bot locked. Try later.")
        return
    
    limit = get_user_limit(user_id)
    if len(user_files.get(user_id, [])) >= limit and limit != float('inf'):
        bot.reply_to(message, f"⚠️ File limit reached ({len(user_files.get(user_id, []))}/{limit})")
        return
    
    doc = message.document
    fname = doc.file_name
    ext = os.path.splitext(fname)[1].lower()
    
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "❌ Only .py, .js, .zip files are allowed")
        return
    
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "❌ File too large (max 20MB)")
        return
    
    msg = bot.reply_to(message, f"⏳ Downloading {fname}...")
    file_info = bot.get_file(doc.file_id)
    content = bot.download_file(file_info.file_path)
    
    user_folder = get_user_folder(user_id)
    
    if ext == '.zip':
        bot.edit_message_text("📦 Extracting ZIP...", message.chat.id, msg.message_id)
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, fname)
        with open(zip_path, 'wb') as f:
            f.write(content)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)
        
        extracted = False
        for item in os.listdir(temp_dir):
            if item.endswith('.py') or item.endswith('.js'):
                shutil.move(os.path.join(temp_dir, item), os.path.join(user_folder, item))
                save_file_db(user_id, item, 'py' if item.endswith('.py') else 'js')
                extracted = True
                bot.edit_message_text(f"✅ Extracted: {item}", message.chat.id, msg.message_id)
        shutil.rmtree(temp_dir)
        if not extracted:
            bot.edit_message_text("❌ No .py or .js found in ZIP", message.chat.id, msg.message_id)
    else:
        file_path = os.path.join(user_folder, fname)
        with open(file_path, 'wb') as f:
            f.write(content)
        save_file_db(user_id, fname, 'py' if ext == '.py' else 'js')
        bot.edit_message_text(f"✅ Saved: {fname}", message.chat.id, msg.message_id)
        run_script(user_id, fname, 'py' if ext == '.py' else 'js', message)

# ==================== BROADCAST ====================
def process_broadcast(message):
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Broadcast cancelled.")
        return
    sent = 0
    for uid in list(active_users):
        try:
            bot.send_message(uid, message.text)
            sent += 1
            time.sleep(0.05)
        except:
            pass
    bot.reply_to(message, f"✅ Broadcast sent to {sent} users")

# ==================== CALLBACKS ====================
@bot.callback_query_handler(func=lambda call: call.data == 'back_files')
def back_files(call):
    user_id = call.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.edit_message_text("📂 No files", call.message.chat.id, call.message.message_id)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in files:
        markup.add(types.InlineKeyboardButton(f"📄 {fname} ({ftype})", callback_data=f'file_{user_id}_{fname}'))
    bot.edit_message_text("📂 Your files:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('file_'))
def file_menu(call):
    _, uid, fname = call.data.split('_', 2)
    uid = int(uid)
    if call.from_user.id != uid and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Not your file")
        return
    markup = create_file_buttons(uid, fname)
    bot.edit_message_text(f"⚙️ Managing: {fname}", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def start_file(call):
    _, uid, fname = call.data.split('_', 2)
    uid = int(uid)
    if call.from_user.id != uid and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Not your file")
        return
    ftype = 'py' if fname.endswith('.py') else 'js'
    run_script(uid, fname, ftype, call.message)
    time.sleep(1)
    markup = create_file_buttons(uid, fname)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id, "✅ Starting...")

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_file(call):
    _, uid, fname = call.data.split('_', 2)
    uid = int(uid)
    if call.from_user.id != uid and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Not your file")
        return
    stop_script(uid, fname)
    markup = create_file_buttons(uid, fname)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id, "✅ Stopped")

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def delete_file(call):
    _, uid, fname = call.data.split('_', 2)
    uid = int(uid)
    if call.from_user.id != uid and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Not your file")
        return
    delete_script(uid, fname)
    bot.edit_message_text(f"🗑️ Deleted: {fname}", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "✅ Deleted")

@bot.callback_query_handler(func=lambda call: call.data.startswith('log_'))
def view_logs(call):
    _, uid, fname = call.data.split('_', 2)
    uid = int(uid)
    if call.from_user.id != uid and call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "❌ Not your file")
        return
    log_path = os.path.join(get_user_folder(uid), f"{os.path.splitext(fname)[0]}.log")
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()[-3500:]
        if not log_content.strip():
            log_content = "(Log is empty)"
        bot.send_message(call.message.chat.id, f"📜 Logs for {fname}:\n```\n{log_content}\n```", parse_mode='Markdown')
    else:
        bot.answer_callback_query(call.id, "No logs found")

# ==================== MAIN ====================
if __name__ == '__main__':
    keep_alive()
    print("="*50)
    print("🤖 SATVIR_CODEX Bot Started!")
    print(f"👑 Owner: {OWNER_ID}")
    print(f"🛡️ Admins: {admin_ids}")
    print("="*50)
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(5)
