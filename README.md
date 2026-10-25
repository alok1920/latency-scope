# LatencyScope

A standalone, multi-protocol **network latency analyzer** — measures round-trip
time, jitter and packet loss to one or more endpoints over **ICMP, TCP, or UDP**,
and ranks them by connection quality so you can pick the best one.

It has a graphical interface (default) and a command-line interface, and builds
into a single standalone executable with no third-party runtime dependencies
(uses only the Python standard library + Tkinter).

## What it does

- **Three probe families**, like a game booster's latency engine:
  - **ICMP** echo ping (classic ping; needs admin/root for raw sockets)
  - **TCP** connect-time ping (no privileges; works through most firewalls)
  - **UDP** round-trip ping (useful for game servers that only speak UDP)
- **Per-target metrics:** min / avg / max RTT, **jitter** (mean successive
  difference), **packet loss %**, standard deviation, and a **quality rating**
  (excellent / good / fair / poor) with a 0–100 score.
- **Best-endpoint selection:** probes several candidates and ranks them
  (reachable first, then by quality, loss, latency) — the "which server is best"
  question a booster answers before connecting.
- **Live GUI** table that updates as probes land, plus a **CLI** with JSON output
  for scripting.

## Quick start (from source)

```bash
python -m latencyscope                                   # GUI
python -m latencyscope example.com:443 1.1.1.1:443/tcp   # CLI, TCP
python -m latencyscope 8.8.8.8:53/udp                    # UDP probe
python -m latencyscope yourserver/icmp                   # ICMP (needs sudo/admin)
python -m latencyscope --json host:443 > report.json     # machine-readable
```

Target syntax: `host[:port][/tcp|udp|icmp]` (IPv6: `[2001:db8::1]:443`).

## Build a standalone executable

See [build/BUILD.md](build/BUILD.md). In short, on the target OS:

```bash
pip install pyinstaller
pyinstaller build/latencyscope.spec     # -> dist/LatencyScope(.exe)
```

PyInstaller cannot cross-compile: build the Windows `.exe` **on Windows**.

## Tests

```bash
pip install pytest
pytest
```

## Scope & provenance

This is an independent, clean-room utility. It reproduces a *generic,
well-understood* capability — latency/jitter/loss measurement and endpoint
ranking — using standard OS networking APIs. It contains **no third-party
application's code, assets, branding, or server endpoints**, and it does not
connect to any commercial accelerator's infrastructure. To actually route or
"accelerate" traffic you would need your own relay server; that is intentionally
out of scope here.
