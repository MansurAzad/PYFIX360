"""Frozen-build entry point for the headless CLI.

PyInstaller needs a concrete script file per executable. The GUI launcher uses
``app.py``; this module is the CLI counterpart so that ``PYFIX360-cli.exe`` runs
the command-line interface rather than opening a window.
"""

from __future__ import annotations

import sys

from pyfix360.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
