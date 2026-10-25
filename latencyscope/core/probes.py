"""Network probes: TCP-connect, UDP and ICMP latency measurement.

Each probe returns the round-trip time in milliseconds, or ``None`` on
timeout / loss. These mirror the three measurement families a game booster
exposes (TCP ping / UDP ping / ICMP ping) so a single host can be rated by
whichever protocol actually gets through a firewall.
"""

from __future__ import annotations

import os
import socket
import struct
import time

from . import icmp

# Monotonic clock -> milliseconds.
def _now_ms() -> float:
    return time.perf_counter() * 1000.0


class ProbeError(Exception):
    """Raised for unrecoverable probe setup problems (e.g. DNS failure)."""


def resolve(host: str, *, ipv6: bool = False) -> str:
    """Resolve ``host`` to a literal IP address."""
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    try:
        infos = socket.getaddrinfo(host, None, family, socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        raise ProbeError(f"cannot resolve {host!r}: {exc}") from exc
    if not infos:
        raise ProbeError(f"cannot resolve {host!r}")
    return infos[0][4][0]


def tcp_ping(host: str, port: int, *, timeout: float = 1.0,
             ipv6: bool = False) -> float | None:
    """Measure TCP connect latency to ``host:port``.

    Works without special privileges, so it is the most portable probe. A
    refused connection still proves the host is reachable and is timed like a
    successful one (the round trip completed).
    """
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    start = _now_ms()
    try:
        sock.connect((host, port))
        return _now_ms() - start
    except ConnectionRefusedError:
        # Host answered (with RST): round trip completed.
        return _now_ms() - start
    except (socket.timeout, TimeoutError):
        return None
    except OSError as exc:
        # ECONNREFUSED can surface as a generic OSError on some platforms.
        if exc.errno in (socket.errno.ECONNREFUSED,):
            return _now_ms() - start
        return None
    finally:
        sock.close()


def udp_ping(host: str, port: int, *, timeout: float = 1.0,
             payload: bytes = b"latencyscope", ipv6: bool = False) -> float | None:
    """Measure UDP round-trip to ``host:port``.

    UDP has no connection, so we send one datagram and wait for either a reply
    or an ICMP port-unreachable (surfaced as ECONNREFUSED on a connected UDP
    socket). Either outcome means the host responded; a timeout means loss or a
    silently-dropping firewall.
    """
    family = socket.AF_INET6 if ipv6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    start = _now_ms()
    try:
        sock.connect((host, port))
        sock.send(payload)
        sock.recv(2048)
        return _now_ms() - start
    except ConnectionRefusedError:
        return _now_ms() - start
    except (socket.timeout, TimeoutError):
        return None
    except OSError as exc:
        if exc.errno in (socket.errno.ECONNREFUSED,):
            return _now_ms() - start
        return None
    finally:
        sock.close()


def icmp_available() -> bool:
    """Best-effort check for whether raw ICMP sockets can be opened."""
    # On most OSes raw sockets require root/admin. We detect lazily instead of
    # probing here to avoid side effects; callers catch PermissionError.
    return True


def icmp_ping(host: str, *, timeout: float = 1.0, sequence: int = 1,
              identifier: int | None = None, ipv6: bool = False) -> float | None:
    """Send one ICMP(v6) echo request and time the reply.

    Requires elevated privileges on most platforms (raises :class:`PermissionError`
    otherwise). Payload carries a timestamp so stray replies can be ignored.
    """
    if identifier is None:
        identifier = os.getpid() & 0xFFFF

    if ipv6:
        family, proto = socket.AF_INET6, socket.IPPROTO_ICMPV6
    else:
        family, proto = socket.AF_INET, socket.IPPROTO_ICMP

    try:
        sock = socket.socket(family, socket.SOCK_RAW, proto)
    except PermissionError:
        raise
    except OSError as exc:
        # Access denied -> raw sockets need elevation. Covers POSIX EPERM/EACCES
        # and Windows WSAEACCES (10013), which Python does not map to
        # PermissionError and which surfaces via errno and/or winerror.
        access_denied = {getattr(socket.errno, "EPERM", 1),
                         getattr(socket.errno, "EACCES", 13), 10013}
        if exc.errno in access_denied or getattr(exc, "winerror", None) == 10013:
            raise PermissionError("raw ICMP socket requires elevated privileges") from exc
        raise ProbeError(f"cannot open ICMP socket: {exc}") from exc

    sock.settimeout(timeout)
    payload = struct.pack("!d", time.perf_counter())
    packet = icmp.build_echo_request(identifier, sequence, payload, ipv6=ipv6)

    try:
        start = _now_ms()
        sock.sendto(packet, (host, 0))
        deadline = start + timeout * 1000.0
        while True:
            remaining = (deadline - _now_ms()) / 1000.0
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                return None
            reply = icmp.parse_echo_reply(data, ipv6=ipv6, has_ip_header=not ipv6)
            if reply is None:
                continue
            if reply.identifier == identifier and reply.sequence == (sequence & 0xFFFF):
                return _now_ms() - start
            # Not our reply (another ping running): keep waiting.
    finally:
        sock.close()
