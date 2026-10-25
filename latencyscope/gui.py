"""Tkinter GUI for LatencyScope — a dark-themed, multi-screen latency dashboard.

Structural layout (generic UI patterns, no third-party branding or assets):

  Screen 1  Catalog  — game grid + search; a card opens the region modal.
  Screen 2  Modal    — centered server-selection dialog listing regions.
  Screen 3  Boost    — split view: control panel (left) + live telemetry (right),
                       hooked to LatencyEngine which refreshes once per second.

All network work runs on LatencyEngine's background thread; results reach the UI
through a queue drained on the Tk main thread, so the window never locks up.
Tkinter ships with CPython, so the packaged executable needs no third-party GUI
dependency.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from . import __app_name__, __version__
from .core.engine import LatencyEngine
from .core.stats import RTT_WARN_MS, compute_stats, route_status

# ---------------------------------------------------------------------------
# Data: regions -> PUBLIC, region-pinned reference endpoints (AWS regional S3
# hosts that answer on TCP 443, one per geography). These measure real
# per-region latency and are NOT any game's or commercial accelerator's server
# list. Swap them for your own relay endpoints as needed.
# ---------------------------------------------------------------------------
REGION_ENDPOINTS: dict[str, list[tuple[str, int]]] = {
    "Hong Kong": [("s3.ap-east-1.amazonaws.com", 443)],
    "Tokyo": [("s3.ap-northeast-1.amazonaws.com", 443)],
    "Korea": [("s3.ap-northeast-2.amazonaws.com", 443)],
    "Singapore": [("s3.ap-southeast-1.amazonaws.com", 443)],
    "Middle East": [("s3.me-south-1.amazonaws.com", 443)],
    "North America": [("s3.us-east-1.amazonaws.com", 443)],
}

# Catalog entries. (title, is_featured) — generic placeholder titles; WARDOGS is
# a plain text label only (no logo/art), per the no-trademarked-assets rule.
GAMES: list[tuple[str, bool]] = [
    ("WARDOGS", True),
    ("Strike Arena", False),
    ("Nova Royale", False),
    ("Pixel Racers", False),
    ("Shadow Ops", False),
    ("Mecha Clash", False),
    ("Frontier Z", False),
    ("Hyper Drift", False),
    ("Orbit Siege", False),
]

# Dark palette.
BG = "#0B111E"          # window / canvas background
WIDGET = "#121A2E"      # internal display widgets
FG = "#E6EDF7"
MUTED = "#5A6B86"
GRID = "#1E2A44"
ACCENT = "#1C7ED6"      # bright blue action button
COLOR = {
    "optimal": "#2ECC71",
    "warning": "#E67E22",
    "critical": "#E74C3C",
    "idle": "#5A6B86",
}

WINDOW_SAMPLES = 20     # rolling window for loss / stats
PROBE_INTERVAL = 1.0    # seconds (refresh once per second, per spec)
PROBE_TIMEOUT = 1.5


class BoosterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{__app_name__} {__version__}")
        root.geometry("820x560")
        root.minsize(720, 520)
        root.configure(bg=BG)

        # Runtime state.
        self._queue: queue.Queue = queue.Queue()
        self.engine: LatencyEngine | None = None
        self.active_region: str | None = None
        self.active_game: str | None = None
        self.active_targets: list[tuple[str, int]] = []
        self._samples: list[float | None] = []

        self._init_style()

        # Stacked screens; switch with tkraise().
        self.container = tk.Frame(root, bg=BG)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.container.rowconfigure(0, weight=1)
        self.container.columnconfigure(0, weight=1)

        self.screen_catalog = tk.Frame(self.container, bg=BG)
        self.screen_boost = tk.Frame(self.container, bg=BG)
        for s in (self.screen_catalog, self.screen_boost):
            s.grid(row=0, column=0, sticky="nsew")

        self._build_catalog()
        self._build_boost()
        self._show(self.screen_catalog)

        self.root.after(80, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- theme ------------------------------------------------------------
    def _init_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                        bordercolor=ACCENT, focuscolor=WIDGET, padding=10,
                        font=("Menlo", 12, "bold"))
        style.map("Accent.TButton", background=[("active", "#1A6FBE")])
        style.configure("Stop.TButton", background="#C0392B", foreground="#FFFFFF",
                        bordercolor="#C0392B", focuscolor=WIDGET, padding=8)
        style.map("Stop.TButton", background=[("active", "#A93226")])
        style.configure("Region.TButton", background=WIDGET, foreground=FG,
                        bordercolor=GRID, focuscolor=WIDGET, padding=10, anchor="w")
        style.map("Region.TButton", background=[("active", "#1B2740")])

    def _show(self, frame: tk.Frame) -> None:
        frame.tkraise()

    # ---- Screen 1: catalog -----------------------------------------------
    def _build_catalog(self) -> None:
        s = self.screen_catalog

        header = tk.Frame(s, bg=BG)
        header.pack(fill=tk.X, padx=18, pady=(16, 8))
        tk.Label(header, text="LATENCYSCOPE  ·  BOOST CATALOG", bg=BG, fg=FG,
                 font=("Menlo", 14, "bold")).pack(side=tk.LEFT)

        # Search bar, top-right of the header.
        search_box = tk.Frame(header, bg=WIDGET, highlightthickness=1,
                              highlightbackground=GRID)
        search_box.pack(side=tk.RIGHT)
        tk.Label(search_box, text="⌕", bg=WIDGET, fg=MUTED,
                 font=("Menlo", 12)).pack(side=tk.LEFT, padx=(8, 2))
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._render_grid())
        ent = tk.Entry(search_box, textvariable=self.var_search, width=22, bd=0,
                       bg=WIDGET, fg=FG, insertbackground=FG, font=("Menlo", 11))
        ent.pack(side=tk.LEFT, padx=(0, 8), pady=6)

        tk.Label(s, text="Select a game to boost", bg=BG, fg=MUTED,
                 font=("Menlo", 10)).pack(anchor="w", padx=18)

        self.grid_frame = tk.Frame(s, bg=BG)
        self.grid_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        self._render_grid()

    def _render_grid(self) -> None:
        for w in self.grid_frame.winfo_children():
            w.destroy()
        query = self.var_search.get().strip().lower()
        cols = 3
        shown = [g for g in GAMES if query in g[0].lower()]
        for c in range(cols):
            self.grid_frame.columnconfigure(c, weight=1, uniform="cards")
        for idx, (title, featured) in enumerate(shown):
            r, c = divmod(idx, cols)
            self._make_card(self.grid_frame, title, featured, r, c)
        if not shown:
            tk.Label(self.grid_frame, text="No games match your search.",
                     bg=BG, fg=MUTED, font=("Menlo", 11)).grid(row=0, column=0,
                                                               columnspan=cols, pady=30)

    def _make_card(self, parent, title: str, featured: bool, r: int, c: int) -> None:
        border = ACCENT if featured else GRID
        card = tk.Frame(parent, bg=WIDGET, highlightthickness=2,
                        highlightbackground=border, cursor="hand2")
        card.grid(row=r, column=c, sticky="nsew", padx=8, pady=8, ipady=6)
        parent.rowconfigure(r, weight=1)

        # Generic "cover art" placeholder block (colored rectangle, no real art).
        art = tk.Frame(card, bg="#17213A", height=78)
        art.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(art, text=title[:2].upper(), bg="#17213A",
                 fg=ACCENT if featured else MUTED,
                 font=("Menlo", 22, "bold")).pack(expand=True, pady=14)

        tk.Label(card, text=title, bg=WIDGET, fg=FG,
                 font=("Menlo", 12, "bold")).pack(anchor="w", padx=10)
        tk.Label(card, text="★ Featured" if featured else "Ready to boost",
                 bg=WIDGET, fg=(ACCENT if featured else MUTED),
                 font=("Menlo", 9)).pack(anchor="w", padx=10, pady=(0, 10))

        # Whole card is clickable -> opens the server-selection modal.
        for widget in (card, art, *card.winfo_children(), *art.winfo_children()):
            widget.bind("<Button-1>", lambda _e, t=title: self._open_region_modal(t))

    # ---- Screen 2: server-selection modal --------------------------------
    def _open_region_modal(self, game: str) -> None:
        modal = tk.Toplevel(self.root, bg=WIDGET)
        modal.title(f"Select server — {game}")
        modal.configure(highlightthickness=1, highlightbackground=ACCENT)
        modal.transient(self.root)
        modal.resizable(False, False)

        tk.Label(modal, text=f"{game}", bg=WIDGET, fg=FG,
                 font=("Menlo", 15, "bold")).pack(anchor="w", padx=20, pady=(18, 2))
        tk.Label(modal, text="Choose a server region", bg=WIDGET, fg=MUTED,
                 font=("Menlo", 10)).pack(anchor="w", padx=20, pady=(0, 12))

        for region in REGION_ENDPOINTS:
            ttk.Button(modal, text=f"   {region}", style="Region.TButton", width=30,
                       command=lambda rg=region: self._choose_region(game, rg, modal)
                       ).pack(fill=tk.X, padx=20, pady=4)

        ttk.Button(modal, text="Cancel", style="Region.TButton",
                   command=modal.destroy).pack(fill=tk.X, padx=20, pady=(12, 18))

        # Center the modal over the main window and make it modal.
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - 360) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - 420) // 2
        modal.geometry(f"360x440+{max(x, 0)}+{max(y, 0)}")
        modal.grab_set()

    def _choose_region(self, game: str, region: str, modal: tk.Toplevel) -> None:
        # Record the target destination array for the chosen region.
        self.active_game = game
        self.active_region = region
        self.active_targets = list(REGION_ENDPOINTS[region])
        modal.destroy()
        self._enter_boost()

    # ---- Screen 3: boost dashboard (split) -------------------------------
    def _build_boost(self) -> None:
        s = self.screen_boost
        s.columnconfigure(0, weight=1, uniform="half")
        s.columnconfigure(1, weight=1, uniform="half")
        s.rowconfigure(0, weight=1)

        # Left: control panel.
        left = tk.Frame(s, bg=WIDGET)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)

        self.boost_art = tk.Frame(left, bg="#17213A", height=120)
        self.boost_art.pack(fill=tk.X, padx=16, pady=16)
        self.var_game = tk.StringVar(value="—")
        tk.Label(self.boost_art, textvariable=self.var_game, bg="#17213A", fg=ACCENT,
                 font=("Menlo", 20, "bold")).pack(expand=True, pady=28)

        tk.Label(left, text="TARGET SERVER", bg=WIDGET, fg=MUTED,
                 font=("Menlo", 9)).pack(anchor="w", padx=16, pady=(6, 0))
        self.var_region = tk.StringVar(value="—")
        tk.Label(left, textvariable=self.var_region, bg=WIDGET, fg=FG,
                 font=("Menlo", 15, "bold")).pack(anchor="w", padx=16)
        self.var_endpoint = tk.StringVar(value="")
        tk.Label(left, textvariable=self.var_endpoint, bg=WIDGET, fg=MUTED,
                 font=("Menlo", 9)).pack(anchor="w", padx=16, pady=(2, 0))

        ttk.Button(left, text="■  Disconnect / Stop Boost", style="Stop.TButton",
                   command=self._stop_boost).pack(side=tk.BOTTOM, fill=tk.X,
                                                  padx=16, pady=16)

        # Right: telemetry.
        right = tk.Frame(s, bg=WIDGET)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)

        ttk.Button(right, text="▶  Launch Game", style="Accent.TButton",
                   command=self._launch_game).pack(fill=tk.X, padx=16, pady=16)

        tk.Label(right, text="LIVE TELEMETRY", bg=WIDGET, fg=MUTED,
                 font=("Menlo", 9)).pack(anchor="w", padx=16)

        tiles = tk.Frame(right, bg=WIDGET)
        tiles.pack(fill=tk.X, padx=12, pady=8)
        self.var_ping = tk.StringVar(value="-- ms")
        self.var_loss = tk.StringVar(value="-- %")
        self.lbl_ping = self._make_tile(tiles, "LIVE PING", self.var_ping, 0)
        self.lbl_loss = self._make_tile(tiles, "PACKET LOSS", self.var_loss, 1)

        self.var_route = tk.StringVar(value="IDLE")
        self.lbl_route = tk.Label(right, textvariable=self.var_route, bg=WIDGET,
                                  fg=COLOR["idle"], font=("Menlo", 13, "bold"), pady=12)
        self.lbl_route.pack(fill=tk.X, padx=16, pady=(8, 0))

    def _make_tile(self, parent, title: str, var: tk.StringVar, col: int) -> tk.Label:
        parent.columnconfigure(col, weight=1)
        cell = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=GRID)
        cell.grid(row=0, column=col, sticky="nsew", padx=4)
        tk.Label(cell, text=title, bg=BG, fg=MUTED,
                 font=("Menlo", 9)).pack(pady=(14, 2))
        value = tk.Label(cell, textvariable=var, bg=BG, fg=FG,
                         font=("Menlo", 26, "bold"))
        value.pack(pady=(0, 16))
        return value

    def _enter_boost(self) -> None:
        self._reset_telemetry()
        self.var_game.set(self.active_game or "—")
        self.var_region.set(self.active_region or "—")
        host, port = self.active_targets[0]
        self.var_endpoint.set(f"{host}:{port}")
        self.var_route.set("CONNECTING …")
        self.lbl_route.config(fg=COLOR["idle"])
        self._show(self.screen_boost)

        # Start the live engine for the chosen region's target array.
        self.engine = LatencyEngine(self.active_targets, interval=PROBE_INTERVAL,
                                    timeout=PROBE_TIMEOUT)
        self.engine.start(self._on_sample)

    def _stop_boost(self) -> None:
        self._shutdown_engine()
        self._reset_telemetry()
        self._show(self.screen_catalog)

    def _launch_game(self) -> None:
        # No real game is bundled; this is a labelled placeholder, not an
        # arbitrary process launcher.
        messagebox.showinfo(
            __app_name__,
            f"No game executable is configured for '{self.active_game}'.\n"
            "LatencyScope measures route latency; it does not launch or tunnel games.")

    # ---- engine callback + telemetry -------------------------------------
    def _on_sample(self, rtt: float | None) -> None:
        # Called on the engine thread; hand off to the UI thread via the queue.
        self._queue.put(rtt)

    def _reset_telemetry(self) -> None:
        self._samples.clear()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self.var_ping.set("-- ms")
        self.var_loss.set("-- %")
        self.lbl_ping.config(fg=FG)
        self.lbl_loss.config(fg=FG)

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
            self._refresh_telemetry()
        self.root.after(80, self._drain_queue)

    def _refresh_telemetry(self) -> None:
        st = compute_stats(self._samples)
        last = self._samples[-1] if self._samples else None

        if last is None:
            self.var_ping.set("timeout")
            self.lbl_ping.config(fg=COLOR["critical"])
        else:
            self.var_ping.set(f"{last:.0f} ms")
            self.lbl_ping.config(
                fg=COLOR["warning"] if last > RTT_WARN_MS else COLOR["optimal"])

        self.var_loss.set(f"{st.loss_pct:.0f} %")
        self.lbl_loss.config(
            fg=COLOR["warning"] if st.loss_pct >= 5.0 else COLOR["optimal"])

        message, severity = route_status(st)
        self.var_route.set(message)
        self.lbl_route.config(fg=COLOR[severity])

    # ---- lifecycle --------------------------------------------------------
    def _shutdown_engine(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.engine = None

    def _on_close(self) -> None:
        self._shutdown_engine()
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    BoosterApp(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
