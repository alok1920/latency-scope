# Building the standalone executable

LatencyScope packages into a single, self-contained executable with
[PyInstaller]. The runtime depends only on the Python standard library
(including Tkinter), so the build has no third-party runtime dependencies.

> **Important:** PyInstaller **does not cross-compile**. You must run the build
> on the operating system you want an executable for. Building on macOS yields
> a macOS binary; to get `LatencyScope.exe` you must build on **Windows**.

## Windows — produce `LatencyScope.exe`

On a Windows machine with Python 3.10+ installed:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install pyinstaller
pyinstaller build\latencyscope.spec
```

Output: `dist\LatencyScope.exe` — a single file, launches the GUI with **no
console window** (the spec sets `console=False`, i.e. `--windowed`).

Equivalent one-liner without the spec file (note: the entry point is `app.py`,
**not** `latencyscope\__main__.py` — a package module used as PyInstaller's entry
cannot do its relative imports):

```bat
pyinstaller --onefile --windowed --name LatencyScope app.py
```

### Notes for the Windows build
- **ICMP ping needs Administrator.** Raw sockets require elevation on Windows.
  TCP and UDP probes work for a normal user; the app degrades gracefully and
  shows a permission message if ICMP can't open a raw socket.
- **Icon / branding:** add your *own* `.ico` and set `icon='...'` in
  `build/latencyscope.spec`. Do not reuse another product's icon or name.
- **Antivirus / SmartScreen:** unsigned one-file PyInstaller apps are sometimes
  flagged. Code-sign the `.exe` for distribution (not required for personal use).

## macOS / Linux (for local validation)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyinstaller
pyinstaller build/latencyscope.spec
```

Output: `dist/LatencyScope` for the current OS. On macOS, ICMP similarly needs
`sudo` for raw sockets; run the CLI with `sudo` if you want ICMP probes.

## Running from source (no build)

```bash
python -m latencyscope                 # launches the GUI
python -m latencyscope example.com:443 1.1.1.1:443/tcp 8.8.8.8:53/udp
python -m latencyscope --json host/icmp > report.json
```
