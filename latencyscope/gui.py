"""Tkinter GUI for LatencyScope — a dark-mode live latency dashboard.

Pick a target region, hit the tracking toggle, and the app continuously probes
a region-pinned endpoint on a background thread, streaming results to a live
status label (colour-coded), a metrics strip and a sparkline canvas.

Tkinter ships with CPython, so the packaged executable needs no third-party GUI
dependency.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import __app_name__, __version__
from .core import probes
from .core.stats import RTT_WARN_MS, compute_stats, route_status

# Region -> (host, port). These are PUBLIC, region-pinned reference endpoints
# (AWS S3 regional hostnames that answer on TCP 443), used purely to measure
# round-trip latency per region. They are not tied to any commercial
# accelerator; swap them for your own relay/region endpoints as needed.
REGIONS: dict[str, tuple[str, int]] = {
    "North America (NA)": ("s3.us-east-1.amazonaws.com", 443),
    "Europe (EU)": ("s3.eu-west-1.amazonaws.com", 443),
    "East Asia (ASIA)": ("s3.ap-northeast-1.amazonaws.com", 443),
    "Southeast Asia (SEA)": ("s3.ap-southeast-1.amazonaws.com", 443),
    "Oceania (OCE)": ("s3.ap-southeast-2.amazonaws.com", 443),
}

# Dark "matrix" palette.
CANVAS_BG = "#0B111E"   # outer canvas / window background
WIDGET_BG = "#121A2E"   # internal display widgets
FG_TEXT = "#E6EDF7"
FG_MUTED = "#5A6B86"
GRID = "#1E2A44"
COLOR = {
    "optimal": "#2ECC71",
    "warning": "#E67E22",
    "critical": "#E74C3C",
    "idle": "#5A6B86",
}

WINDOW_SAMPLES = 60   # rolling window for stats + sparkline
PROBE_INTERVAL = 1.0  # seconds between probes
PROBE_TIMEOUT = 1.5   # per-probe timeout (seconds)


class LatencyDashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{__app_name__} {__version__}")
        root.geometry("640x560")
        root.minsize(560, 500)
        root.configure(bg=CANVAS_BG)

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._samples: list[float | None] = []
        self.tracking = False

        self._init_style()
        self._build_widgets()
        self.root.after(80, self._drain_queue)

    # ---- theme ------------------------------------------------------------
    def _init_style(self) -> None:
        style = ttk.Style(self.root)
        # 'clam' honours colour options on all platforms (unlike aqua/win themes).
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox",
                        fieldbackground=WIDGET_BG, background=WIDGET_BG,
                        foreground=FG_TEXT, arrowcolor=FG_TEXT, bordercolor=GRID)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", WIDGET_BG)],
                  foreground=[("readonly", FG_TEXT)])
        style.configure("Track.TButton", background="#1C7ED6", foreground="#FFFFFF",
                        bordercolor="#1C7ED6", focuscolor=WIDGET_BG, padding=8)
        style.map("Track.TButton", background=[("active", "#1A6FBE")])

    # ---- layout -----------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 14}

        header = tk.Label(self.root, text="LATENCYSCOPE  ·  ROUTE MONITOR",
                          bg=CANVAS_BG, fg=FG_MUTED, font=("Menlo", 11, "bold"))
        header.pack(anchor="w", pady=(12, 6), **pad)

        # Region selector row.
        row = tk.Frame(self.root, bg=CANVAS_BG)
        row.pack(fill=tk.X, **pad)
        tk.Label(row, text="Target region", bg=CANVAS_BG, fg=FG_TEXT,
                 font=("Menlo", 11)).pack(side=tk.LEFT)
        self.var_region = tk.StringVar(value=next(iter(REGIONS)))
        self.cmb_region = ttk.Combobox(row, textvariable=self.var_region,
                                       values=list(REGIONS), state="readonly",
                                       style="Dark.TCombobox", width=24)
        self.cmb_region.pack(side=tk.LEFT, padx=10)
        self.cmb_region.bind("<<ComboboxSelected>>", self._on_region_change)

        self.btn_track = ttk.Button(row, text="▶  Start Tracking",
                                    style="Track.TButton", command=self.toggle_tracking)
        self.btn_track.pack(side=tk.RIGHT)

        # Live status label (colour-coded).
        self.var_status = tk.StringVar(value="IDLE — SELECT A REGION AND START")
        self.lbl_status = tk.Label(self.root, textvariable=self.var_status,
                                   bg=WIDGET_BG, fg=COLOR["idle"],
                                   font=("Menlo", 15, "bold"), pady=16)
        self.lbl_status.pack(fill=tk.X, pady=14, **pad)

        # Big current-RTT readout.
        panel = tk.Frame(self.root, bg=WIDGET_BG)
        panel.pack(fill=tk.X, **pad)
        self.var_rtt = tk.StringVar(value="-- ms")
        tk.Label(panel, text="CURRENT RTT", bg=WIDGET_BG, fg=FG_MUTED,
                 font=("Menlo", 10)).pack(anchor="w", padx=14, pady=(12, 0))
        self.lbl_rtt = tk.Label(panel, textvariable=self.var_rtt, bg=WIDGET_BG,
                                fg=FG_TEXT, font=("Menlo", 34, "bold"))
        self.lbl_rtt.pack(anchor="w", padx=14, pady=(0, 10))

        # Metrics strip.
        strip = tk.Frame(self.root, bg=CANVAS_BG)
        strip.pack(fill=tk.X, pady=(10, 0), **pad)
        self._metric_vars: dict[str, tk.StringVar] = {}
        for key, title in (("avg", "AVG"), ("min", "MIN"), ("max", "MAX"),
                           ("jitter", "JITTER"), ("loss", "LOSS")):
            cell = tk.Frame(strip, bg=WIDGET_BG)
            cell.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=3)
            tk.Label(cell, text=title, bg=WIDGET_BG, fg=FG_MUTED,
                     font=("Menlo", 9)).pack(pady=(8, 0))
            v = tk.StringVar(value="--")
            self._metric_vars[key] = v
            tk.Label(cell, textvariable=v, bg=WIDGET_BG, fg=FG_TEXT,
                     font=("Menlo", 13, "bold")).pack(pady=(0, 8))

        # Sparkline canvas.
        tk.Label(self.root, text="LATENCY TIMELINE", bg=CANVAS_BG, fg=FG_MUTED,
                 font=("Menlo", 9)).pack(anchor="w", pady=(14, 2), **pad)
        self.canvas = tk.Canvas(self.root, bg=CANVAS_BG, height=130,
                                highlightthickness=1, highlightbackground=GRID)
        self.canvas.pack(fill=tk.BOTH, expand=True, pady=(0, 14), **pad)

    # ---- actions ----------------------------------------------------------
    def toggle_tracking(self) -> None:
        if self.tracking:
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self) -> None:
        # Requirement: clear old data cleanly on each (re)start.
        self._clear_data()
        region = self.var_region.get()
        host, port = REGIONS[region]

        self._stop.clear()
        self.tracking = True
        self.btn_track.config(text="■  Stop Tracking")
        self.cmb_region.config(state="disabled")
        self.var_status.set(f"TRACKING {region} …")
        self.lbl_status.config(fg=COLOR["idle"])

        self._worker = threading.Thread(
            target=self._run_probe_loop, args=(host, port), daemon=True)
        self._worker.start()

    def _stop_tracking(self) -> None:
        self._stop.set()
        self.tracking = False
        self.btn_track.config(text="▶  Start Tracking")
        self.cmb_region.config(state="readonly")
        self.var_status.set("STOPPED")
        self.lbl_status.config(fg=COLOR["idle"])

    def _on_region_change(self, _event=None) -> None:
        # Switching region resets the view; user clicks Start to begin afresh.
        if self.tracking:
            self._stop_tracking()
        self._clear_data()
        self.var_status.set("IDLE — SELECT A REGION AND START")
        self.lbl_status.config(fg=COLOR["idle"])

    def _clear_data(self) -> None:
        self._samples.clear()
        # Drain any stale samples still queued from a previous run.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self.var_rtt.set("-- ms")
        self.lbl_rtt.config(fg=FG_TEXT)
        for v in self._metric_vars.values():
            v.set("--")
        self.canvas.delete("all")

    # ---- worker -----------------------------------------------------------
    def _run_probe_loop(self, host: str, port: int) -> None:
        while not self._stop.is_set():
            rtt = probes.tcp_ping(host, port, timeout=PROBE_TIMEOUT)
            if self._stop.is_set():
                break
            self._queue.put(rtt)
            self._stop.wait(PROBE_INTERVAL)  # interruptible sleep

    # ---- UI pump ----------------------------------------------------------
    def _drain_queue(self) -> None:
        updated = False
        try:
            while True:
                rtt = self._queue.get_nowait()
                self._samples.append(rtt)
                if len(self._samples) > WINDOW_SAMPLES:
                    self._samples = self._samples[-WINDOW_SAMPLES:]
                updated = True
        except queue.Empty:
            pass
        if updated:
            self._refresh()
        self.root.after(80, self._drain_queue)

    def _refresh(self) -> None:
        st = compute_stats(self._samples)
        last = self._samples[-1] if self._samples else None

        # Current RTT readout, coloured by the per-sample threshold.
        if last is None:
            self.var_rtt.set("timeout")
            self.lbl_rtt.config(fg=COLOR["critical"])
        else:
            self.var_rtt.set(f"{last:.0f} ms")
            self.lbl_rtt.config(fg=COLOR["warning"] if last > RTT_WARN_MS else COLOR["optimal"])

        # Metrics.
        self._metric_vars["avg"].set(f"{st.avg_ms:.0f}" if st.avg_ms is not None else "--")
        self._metric_vars["min"].set(f"{st.min_ms:.0f}" if st.min_ms is not None else "--")
        self._metric_vars["max"].set(f"{st.max_ms:.0f}" if st.max_ms is not None else "--")
        self._metric_vars["jitter"].set(f"{st.jitter_ms:.0f}" if st.jitter_ms is not None else "--")
        self._metric_vars["loss"].set(f"{st.loss_pct:.0f}%")

        # Colour-coded live status label.
        message, severity = route_status(st)
        self.var_status.set(message)
        self.lbl_status.config(fg=COLOR[severity])

        self._draw_sparkline()

    def _draw_sparkline(self) -> None:
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 130
        pad = 8
        plot_h = h - 2 * pad

        received = [s for s in self._samples if s is not None]
        y_max = max(RTT_WARN_MS * 1.3, max(received) * 1.15 if received else RTT_WARN_MS)

        def y_of(ms: float) -> float:
            return pad + plot_h * (1.0 - min(ms, y_max) / y_max)

        # 130ms warning threshold line.
        ty = y_of(RTT_WARN_MS)
        c.create_line(pad, ty, w - pad, ty, fill=COLOR["warning"], dash=(4, 3))
        c.create_text(w - pad - 2, ty - 8, text=f"{RTT_WARN_MS:.0f}ms", anchor="e",
                      fill=COLOR["warning"], font=("Menlo", 8))

        if len(self._samples) < 2:
            return

        n = len(self._samples)
        step = (w - 2 * pad) / max(1, n - 1)
        pts: list[tuple[float, float, bool]] = []
        for i, s in enumerate(self._samples):
            x = pad + i * step
            if s is None:
                # Mark a loss with a short red tick at the baseline.
                c.create_line(x, h - pad, x, h - pad - 10, fill=COLOR["critical"])
                pts.append((x, h - pad, False))
            else:
                pts.append((x, y_of(s), True))

        # Draw the line only across consecutive received samples.
        run: list[tuple[float, float]] = []
        for x, y, ok in pts:
            if ok:
                run.append((x, y))
            else:
                if len(run) >= 2:
                    c.create_line(*[v for xy in run for v in xy], fill=COLOR["optimal"], width=2)
                run = []
        if len(run) >= 2:
            c.create_line(*[v for xy in run for v in xy], fill=COLOR["optimal"], width=2)


def launch() -> None:
    root = tk.Tk()
    LatencyDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
