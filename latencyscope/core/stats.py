"""Pure statistics for latency samples.

A "sample" is a round-trip time in milliseconds, or ``None`` when the probe
timed out / was lost. Everything in this module is a pure function so it can be
unit-tested without touching the network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict


@dataclass
class LatencyStats:
    sent: int = 0
    received: int = 0
    lost: int = 0
    loss_pct: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None
    avg_ms: float | None = None
    # jitter: mean of absolute differences between consecutive received samples
    jitter_ms: float | None = None
    stdev_ms: float | None = None
    last_ms: float | None = None
    quality: str = "unknown"
    score: int = 0
    samples: list[float | None] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def compute_stats(samples: list[float | None]) -> LatencyStats:
    """Aggregate a list of RTT samples (ms) into a :class:`LatencyStats`."""
    sent = len(samples)
    received_vals = [s for s in samples if s is not None]
    received = len(received_vals)
    lost = sent - received
    loss_pct = (lost / sent * 100.0) if sent else 0.0

    st = LatencyStats(
        sent=sent,
        received=received,
        lost=lost,
        loss_pct=round(loss_pct, 2),
        samples=list(samples),
    )
    if not received_vals:
        st.quality, st.score = rate_quality(st)
        return st

    st.min_ms = round(min(received_vals), 3)
    st.max_ms = round(max(received_vals), 3)
    st.avg_ms = round(sum(received_vals) / received, 3)
    st.last_ms = received_vals[-1]

    # Jitter as mean absolute successive difference over received samples,
    # taken in arrival order (closer to how interactive apps perceive it).
    diffs = [abs(b - a) for a, b in zip(received_vals, received_vals[1:])]
    st.jitter_ms = round(sum(diffs) / len(diffs), 3) if diffs else 0.0

    if received > 1:
        mean = st.avg_ms
        var = sum((x - mean) ** 2 for x in received_vals) / (received - 1)
        st.stdev_ms = round(math.sqrt(var), 3)
    else:
        st.stdev_ms = 0.0

    st.quality, st.score = rate_quality(st)
    return st


# Thresholds tuned for real-time/online-game traffic. (label, max_avg, max_loss%, max_jitter)
_QUALITY_TIERS = [
    ("excellent", 30.0, 0.0, 5.0),
    ("good", 60.0, 1.0, 15.0),
    ("fair", 120.0, 5.0, 40.0),
]


def rate_quality(stats: LatencyStats) -> tuple[str, int]:
    """Return a ``(label, score 0-100)`` quality rating.

    Mirrors the kind of coarse "connection quality" indicator a game booster
    surfaces: a blend of latency, packet loss and jitter.
    """
    if stats.received == 0:
        return "unreachable", 0

    avg = stats.avg_ms or 0.0
    jit = stats.jitter_ms or 0.0
    loss = stats.loss_pct

    for label, max_avg, max_loss, max_jit in _QUALITY_TIERS:
        if avg <= max_avg and loss <= max_loss and jit <= max_jit:
            label_out = label
            break
    else:
        label_out = "poor"

    # Score: penalise latency, loss (heavily) and jitter on a 0-100 scale.
    lat_pen = min(avg / 2.0, 60.0)          # 120ms -> 60 pts off
    loss_pen = min(loss * 4.0, 60.0)        # 15% loss -> 60 pts off
    jit_pen = min(jit, 20.0)                # 20ms+ jitter -> 20 pts off
    score = int(max(0.0, 100.0 - lat_pen - loss_pen - jit_pen))
    return label_out, score
