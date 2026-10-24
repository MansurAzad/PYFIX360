"""Where the application's artwork lives.

The icon ships inside the frozen bundle and sits in ``packaging/`` in a
checkout. Both the window, the notification area and the installer need to
find it, so the lookup lives here instead of being written twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

ICON_NAME = "pyfix360.ico"


def icon_path() -> Path | None:
    """The application icon, or None when it cannot be found."""
    if getattr(sys, "frozen", False):
        candidate = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        path = candidate / ICON_NAME
        if path.is_file():
            return path
    candidate = Path(__file__).resolve().parent.parent.parent / "packaging" / ICON_NAME
    return candidate if candidate.is_file() else None
