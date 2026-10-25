"""Frozen-executable entry point for LatencyScope.

PyInstaller runs its entry script as the top-level ``__main__`` module, which
has no package context, so this script uses **absolute** imports (relative
imports would fail with "attempted relative import with no known parent
package"). ``python -m latencyscope`` keeps working via ``__main__.py``.

With command-line arguments it runs the CLI; with none (the default for the
windowed executable) it launches the GUI.
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from latencyscope.cli import run
        return run()
    from latencyscope.gui import launch
    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
