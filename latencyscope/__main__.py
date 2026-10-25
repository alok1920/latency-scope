"""Entry point.

With command-line arguments it runs the CLI; with none (the default for the
windowed executable) it launches the GUI.
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import run
        return run()
    from .gui import launch
    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
