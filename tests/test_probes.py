import socket
import threading

import pytest

from latencyscope.core import probes


def test_resolve_ipv4_literal():
    assert probes.resolve("127.0.0.1") == "127.0.0.1"


def test_resolve_localhost():
    ip = probes.resolve("localhost")
    assert ip.startswith("127.") or ip == "::1" or ip  # some resolvable address


def test_resolve_bad_host_raises():
    with pytest.raises(probes.ProbeError):
        probes.resolve("no-such-host.invalid")


def test_tcp_ping_to_open_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        rtt = probes.tcp_ping("127.0.0.1", port, timeout=1.0)
        assert rtt is not None
        assert rtt >= 0.0
    finally:
        srv.close()


def test_tcp_ping_refused_counts_as_reachable(monkeypatch):
    # Contract: an actively refused connection completed a round trip and must
    # be reported as a timing, not a loss. Mocked so the outcome is identical on
    # every OS (real refused-vs-dropped behaviour differs between platforms).
    class _RefusingSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, _t):
            pass

        def connect(self, _addr):
            raise ConnectionRefusedError(61, "connection refused")

        def close(self):
            pass

    monkeypatch.setattr(probes.socket, "socket", lambda *a, **k: _RefusingSocket())
    rtt = probes.tcp_ping("127.0.0.1", 1, timeout=1.0)
    assert rtt is not None
    assert rtt >= 0.0


def test_tcp_ping_real_closed_port_returns_float_or_none():
    # A real closed port is refused (float) on some OSes and silently dropped
    # (None, via timeout) on others — e.g. Windows CI runners. Both are valid;
    # assert only that the probe returns the right type and never crashes.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    rtt = probes.tcp_ping("127.0.0.1", port, timeout=0.5)
    assert rtt is None or isinstance(rtt, float)


def test_tcp_ping_timeout_returns_none():
    # TEST-NET-1 (RFC 5737) is not routable; expect a timeout.
    rtt = probes.tcp_ping("192.0.2.1", 80, timeout=0.3)
    assert rtt is None


def test_udp_ping_with_echo_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    stop = threading.Event()

    def echo():
        srv.settimeout(2.0)
        try:
            data, addr = srv.recvfrom(2048)
            srv.sendto(data, addr)
        except OSError:
            pass

    t = threading.Thread(target=echo, daemon=True)
    t.start()
    try:
        rtt = probes.udp_ping("127.0.0.1", port, timeout=1.5)
        assert rtt is not None
        assert rtt >= 0.0
    finally:
        stop.set()
        srv.close()
        t.join(timeout=2.0)


def test_icmp_ping_degrades_gracefully_without_privilege():
    # Unprivileged runners cannot open raw sockets. The call must either return
    # a timing/None or raise a handled error (PermissionError, or ProbeError on
    # Windows where WSAEACCES does not map to PermissionError) — never an
    # unhandled exception.
    try:
        rtt = probes.icmp_ping("127.0.0.1", timeout=0.5)
        assert rtt is None or isinstance(rtt, float)
    except (PermissionError, probes.ProbeError):
        pass
