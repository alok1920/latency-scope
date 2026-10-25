# PyInstaller spec for LatencyScope.
#
# Build (run on the TARGET OS — PyInstaller does not cross-compile):
#     pyinstaller build/latencyscope.spec
#
# On Windows this produces dist/LatencyScope.exe (single file, no console window).
# On macOS/Linux it produces an equivalent single-file app for that OS.
#
# To add a custom icon, drop an .ico (Windows) next to this spec and set
# `icon='youricon.ico'` in the EXE() call below. Do NOT reuse any third-party
# application's icon or branding.

import os

# SPECPATH is injected by PyInstaller and points at this file's directory,
# so the build works no matter which directory you invoke pyinstaller from.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

block_cipher = None

a = Analysis(
    # Entry point lives OUTSIDE the package and uses absolute imports, so it
    # works when PyInstaller runs it as the top-level __main__ module.
    [os.path.join(PROJECT_ROOT, 'app.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LatencyScope',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # --windowed: no terminal window pops up
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # set to 'youricon.ico' to brand it (use your own art)
)
