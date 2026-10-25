"""Orchestration: run repeated probes against one or more targets, aggregate
into stats, and rank targets to pick the best reachable one.

The ranking mirrors a booster's node-selection step (``GetPingableIp``): among
candidate endpoints, prefer the one with the best measured quality.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from . import probes
from .stats import LatencyStats, compute_stats


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


@dataclass
class Target:
    host: str
    port: int | None = None
    protocol: Protocol = Protocol.TCP
    ipv6: bool = False

    @property
    def label(self) -> str:
        if self.protocol is Protocol.ICMP or self.port is None:
            return f"{self.host} [{self.protocol.value}]"
        return f"{self.host}:{self.port} [{self.protocol.value}]"


@dataclass
class TargetResult:
    target: Target
    resolved_ip: str | None = None
    stats: LatencyStats = field(default_factory=LatencyStats)
    error: str | None = None


# Callback signature: (target, seq, rtt_ms_or_None)
ProbeCallback = Callable[[Target, int, float | None], None]


def parse_target(spec: str, *, default_protocol: Protocol = Protocol.TCP,
                 default_port: int = 443) -> Target:
    """Parse a ``host[:port][/proto]`` spec into a :class:`Target`.

    Examples: ``example.com``, ``1.2.3.4:27015/udp``, ``host/icmp``,
    ``[2001:db8::1]:443``.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty target")

    protocol = default_protocol
    if "/" in spec:
        spec, proto_str = spec.rsplit("/", 1)
        protocol = Protocol(proto_str.lower())

    ipv6 = False
    host: str
    port: int | None

    if spec.startswith("["):  # bracketed IPv6 literal, optional :port
        end = spec.find("]")
        if end == -1:
            raise ValueError(f"malformed IPv6 target: {spec!r}")
        host = spec[1:end]
        ipv6 = True
        rest = spec[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") else None
    elif spec.count(":") > 1:  # bare IPv6 literal, no port
        host, port, ipv6 = spec, None, True
    elif ":" in spec:
        host, port_str = spec.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = spec, None

    if protocol in (Protocol.TCP, Protocol.UDP) and port is None:
        port = default_port
    return Target(host=host, port=port, protocol=protocol, ipv6=ipv6)


def _probe_once(target: Target, timeout: float, seq: int) -> float | None:
    if target.protocol is Protocol.TCP:
        return probes.tcp_ping(target.host, target.port, timeout=timeout, ipv6=target.ipv6)
    if target.protocol is Protocol.UDP:
        return probes.udp_ping(target.host, target.port, timeout=timeout, ipv6=target.ipv6)
    if target.protocol is Protocol.ICMP:
        return probes.icmp_ping(target.host, timeout=timeout, sequence=seq, ipv6=target.ipv6)
    raise ValueError(f"unknown protocol {target.protocol!r}")


def ping_target(target: Target, *, count: int = 5, interval: float = 1.0,
                timeout: float = 1.0, on_sample: ProbeCallback | None = None,
                stop: Callable[[], bool] | None = None) -> TargetResult:
    """Probe ``target`` ``count`` times and return aggregated stats."""
    result = TargetResult(target=target)
    try:
        result.resolved_ip = probes.resolve(target.host, ipv6=target.ipv6)
    except probes.ProbeError as exc:
        result.error = str(exc)
        return result

    samples: list[float | None] = []
    for seq in range(1, count + 1):
        if stop is not None and stop():
            break
        try:
            rtt = _probe_once(target, timeout, seq)
        except PermissionError as exc:
            result.error = str(exc)
            break
        except probes.ProbeError as exc:
            result.error = str(exc)
            break
        samples.append(rtt)
        if on_sample is not None:
            on_sample(target, seq, rtt)
        if seq < count and interval > 0 and (stop is None or not stop()):
            time.sleep(interval)

    result.stats = compute_stats(samples)
    return result


def _rank_key(result: TargetResult):
    """Sort key: reachable first, then higher score, lower loss, lower latency."""
    st = result.stats
    reachable = st.received > 0 and result.error is None
    avg = st.avg_ms if st.avg_ms is not None else float("inf")
    return (
        0 if reachable else 1,
        -st.score,
        st.loss_pct,
        avg,
    )


def rank_targets(results: list[TargetResult]) -> list[TargetResult]:
    """Return ``results`` ordered best-first (the booster's node pick)."""
    return sorted(results, key=_rank_key)


def best_target(results: list[TargetResult]) -> TargetResult | None:
    """Return the single best reachable target, or ``None`` if none respond."""
    ranked = rank_targets(results)
    if ranked and ranked[0].stats.received > 0 and ranked[0].error is None:
        return ranked[0]
    return None
