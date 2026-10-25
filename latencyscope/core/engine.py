"""LatencyEngine — continuous background latency probing for the live dashboard.

Pings one or more endpoints on a daemon thread and hands each round-trip result
to a callback. The caller (e.g. the GUI) is responsible for marshalling that
callback onto its own thread; the engine itself never touches any UI.
"""

from __future__ import annotations

import threading
from typing import Callable

from . import probes

# Callback receives the RTT in ms, or None for a timed-out / lost probe.
SampleCallback = Callable[[float | None], None]


class LatencyEngine:
    def __init__(self, targets: list[tuple[str, int]], *,
                 interval: float = 1.0, timeout: float = 1.5):
        if not targets:
            raise ValueError("LatencyEngine needs at least one (host, port) target")
        self._targets = list(targets)      # the recorded destination array
        self._interval = interval
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def targets(self) -> list[tuple[str, int]]:
        return list(self._targets)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_sample: SampleCallback) -> None:
        """Begin probing; ``on_sample`` is invoked once per probe (worker thread)."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(on_sample,), daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        """Signal the loop to stop; optionally block until the thread exits."""
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self, on_sample: SampleCallback) -> None:
        # Round-robin across the target array so multi-IP regions are sampled.
        i = 0
        while not self._stop.is_set():
            host, port = self._targets[i % len(self._targets)]
            rtt = probes.tcp_ping(host, port, timeout=self._timeout)
            if self._stop.is_set():
                break
            on_sample(rtt)
            i += 1
            self._stop.wait(self._interval)  # interruptible sleep
