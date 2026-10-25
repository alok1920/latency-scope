"""Tkinter GUI for LatencyScope (the windowed entry point).

Runs probes on a worker thread and streams results back to the UI through a
queue so the window stays responsive. Tkinter ships with CPython, so the
packaged executable needs no third-party GUI dependency.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import __app_name__, __version__
from .core.analyzer import (
    Protocol,
    Target,
    TargetResult,
    parse_target,
    ping_target,
    rank_targets,
)
from .core.stats import compute_stats

_QUALITY_COLORS = {
    "excellent": "#1b7f3b",
    "good": "#4a9c2d",
    "fair": "#b8860b",
    "poor": "#c0392b",
    "unreachable": "#7f8c8d",
    "unknown": "#7f8c8d",
}

_DEFAULT_TARGETS = "example.com:443/tcp\n1.1.1.1:443/tcp\n8.8.8.8:53/udp"


class LatencyScopeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{__app_name__} {__version__}")
        root.geometry("760x520")
        root.minsize(620, 420)

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._rows: dict[str, str] = {}  # target label -> tree item id

        self._build_widgets()
        self.root.after(100, self._drain_queue)

    # ---- layout -----------------------------------------------------------
    def _build_widgets(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Targets (one per line, host[:port][/tcp|udp|icmp]):").grid(
            row=0, column=0, columnspan=6, sticky="w")
        self.txt_targets = tk.Text(top, height=4, width=60)
        self.txt_targets.insert("1.0", _DEFAULT_TARGETS)
        self.txt_targets.grid(row=1, column=0, columnspan=6, sticky="we", pady=(2, 6))

        ttk.Label(top, text="Count:").grid(row=2, column=0, sticky="e")
        self.var_count = tk.IntVar(value=10)
        ttk.Spinbox(top, from_=1, to=1000, textvariable=self.var_count, width=6).grid(
            row=2, column=1, sticky="w", padx=(2, 10))

        ttk.Label(top, text="Interval (s):").grid(row=2, column=2, sticky="e")
        self.var_interval = tk.DoubleVar(value=0.5)
        ttk.Spinbox(top, from_=0.0, to=10.0, increment=0.1, textvariable=self.var_interval,
                    width=6).grid(row=2, column=3, sticky="w", padx=(2, 10))

        ttk.Label(top, text="Timeout (s):").grid(row=2, column=4, sticky="e")
        self.var_timeout = tk.DoubleVar(value=1.0)
        ttk.Spinbox(top, from_=0.1, to=10.0, increment=0.1, textvariable=self.var_timeout,
                    width=6).grid(row=2, column=5, sticky="w", padx=2)

        top.columnconfigure(0, weight=1)

        btns = ttk.Frame(self.root, padding=(8, 0))
        btns.pack(side=tk.TOP, fill=tk.X)
        self.btn_start = ttk.Button(btns, text="Start", command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_stop = ttk.Button(btns, text="Stop", command=self.stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        cols = ("ip", "avg", "min", "max", "jitter", "loss", "quality")
        headings = ("Resolved IP", "Avg ms", "Min", "Max", "Jitter", "Loss %", "Quality")
        self.tree = ttk.Treeview(self.root, columns=cols, show="tree headings", height=10)
        self.tree.heading("#0", text="Target")
        self.tree.column("#0", width=200, anchor="w")
        for c, h in zip(cols, headings):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=78, anchor="center")
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=8)
        for q, color in _QUALITY_COLORS.items():
            self.tree.tag_configure(q, foreground=color)

        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.var_status, relief=tk.SUNKEN,
                  anchor="w", padding=4).pack(side=tk.BOTTOM, fill=tk.X)

    # ---- actions ----------------------------------------------------------
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        raw = self.txt_targets.get("1.0", tk.END).strip().splitlines()
        specs = [s.strip() for s in raw if s.strip()]
        if not specs:
            messagebox.showwarning(__app_name__, "Enter at least one target.")
            return
        try:
            targets = [parse_target(s) for s in specs]
        except ValueError as exc:
            messagebox.showerror(__app_name__, f"Invalid target: {exc}")
            return

        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for t in targets:
            item = self.tree.insert("", tk.END, text=t.label,
                                    values=("…", "", "", "", "", "", "pending"))
            self._rows[t.label] = item

        self._stop_flag.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.var_status.set("Probing…")

        count = max(1, self.var_count.get())
        interval = max(0.0, self.var_interval.get())
        timeout = max(0.1, self.var_timeout.get())
        self._worker = threading.Thread(
            target=self._run_probes, args=(targets, count, interval, timeout), daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_flag.set()
        self.var_status.set("Stopping…")

    # ---- worker -----------------------------------------------------------
    def _run_probes(self, targets, count, interval, timeout) -> None:
        results: list[TargetResult] = []
        for t in targets:
            if self._stop_flag.is_set():
                break

            # Keep a running sample list so each row updates live as probes land.
            running: list = []

            def on_sample(tgt: Target, seq: int, rtt, _running=running):
                _running.append(rtt)
                self._queue.put(("update", tgt.label, compute_stats(_running), None))

            res = ping_target(t, count=count, interval=interval, timeout=timeout,
                              on_sample=on_sample, stop=self._stop_flag.is_set)
            results.append(res)
            self._queue.put(("final", t.label, res.stats, res))
        self._queue.put(("done", None, None, results))

    # ---- UI pump ----------------------------------------------------------
    def _drain_queue(self) -> None:
        try:
            while True:
                kind, label, stats, payload = self._queue.get_nowait()
                if kind in ("update", "final") and label in self._rows:
                    self._write_row(label, stats, payload)
                elif kind == "done":
                    self._finish(payload or [])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _write_row(self, label, stats, res: TargetResult | None) -> None:
        item = self._rows[label]
        ip = res.resolved_ip if res and res.resolved_ip else self.tree.set(item, "ip")
        if res and res.error:
            self.tree.item(item, values=(res.resolved_ip or "?", "", "", "", "", "",
                                         "error"), tags=("unreachable",))
            return
        if stats.received == 0 and stats.sent > 0 and res is not None:
            self.tree.item(item, values=(ip or "?", "", "", "", "", "100", "unreachable"),
                           tags=("unreachable",))
            return
        self.tree.item(item, values=(
            ip or "…",
            f"{stats.avg_ms:.1f}" if stats.avg_ms is not None else "",
            f"{stats.min_ms:.1f}" if stats.min_ms is not None else "",
            f"{stats.max_ms:.1f}" if stats.max_ms is not None else "",
            f"{stats.jitter_ms:.1f}" if stats.jitter_ms is not None else "",
            f"{stats.loss_pct:.0f}",
            f"{stats.quality} ({stats.score})",
        ), tags=(stats.quality,))

    def _finish(self, results: list[TargetResult]) -> None:
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        if not results:
            self.var_status.set("Stopped.")
            return
        ranked = rank_targets(results)
        best = next((r for r in ranked if r.stats.received > 0 and not r.error), None)
        if best:
            self.var_status.set(
                f"Best: {best.target.label} — {best.stats.avg_ms:.1f} ms, "
                f"{best.stats.quality} (score {best.stats.score})")
        else:
            self.var_status.set("Done — no endpoint responded.")


def launch() -> None:
    root = tk.Tk()
    LatencyScopeApp(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
