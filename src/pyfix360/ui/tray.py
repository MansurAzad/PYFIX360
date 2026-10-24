"""The notification-area icon: the only honest way back into a hidden window.

The desktop app can keep running after its window is closed. That is the GUI half
of "background mode", and it is only defensible together with an icon that shows
the window again and offers a real Quit: an application with no window, no icon
and no exit is one the operator cannot stop.

Two limits stated where they are configured:

* **Closing the window does not hide the process.** It stays in Task Manager and
  in ``tasklist``. Anything that removes it from those lists is a process-hiding
  technique and is not implemented here.
* **The tray is optional infrastructure.** Windows has a notification area;
  plenty of Linux desktops and every headless session do not. With no tray the
  menu cannot be offered, so the app quits when told to rather than hiding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from pyfix360 import APP_NAME
from pyfix360.services import host_service, job_service
from pyfix360.ui.context import AppContext

logger = logging.getLogger(__name__)

#: The queue states a closed window does not make the operator forget about.
_OPEN = ("queued", "running", "waiting_approval")


class TrayController(QObject):
    """A tray icon whose menu is rebuilt from live state every time it opens.

    The menu reads the database and the host's own pid file instead of keeping a
    copy of the truth, so a host started from a CLI in another window appears here
    on the next click.
    """

    restore_window = Signal()
    show_autopilot = Signal()
    quit_application = Signal()

    def __init__(self, ctx: AppContext, *, icon_path: Path | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._hint_shown = False
        self._icon = QSystemTrayIcon(parent=self)
        if icon_path is not None:
            self._icon.setIcon(QIcon(str(icon_path)))
        self._icon.setToolTip(f"{APP_NAME} — running with the window closed")
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        # The Settings Center writes the config and broadcasts; the icon that
        # ignores that would keep offering a Quit path the user just switched off.
        ctx.data_changed.connect(self.sync_to_settings)

    # ------------------------------------------------------------------ availability
    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def enabled(self) -> bool:
        """Whether an icon should be on screen at all."""
        return self.available and self.ctx.config.tray_enabled

    @property
    def should_hide_on_close(self) -> bool:
        """The rule the main window asks before swallowing a close.

        Both conditions matter: an icon the user turned off, and an icon this
        desktop cannot show, each leave a hidden window nobody can recall.
        """
        return self.enabled and self.ctx.config.close_hides_to_tray

    def show(self) -> bool:
        """Put the icon up. False when this desktop has nowhere to put it."""
        if not self.enabled:
            return False
        self._icon.show()
        return True

    def hide(self) -> None:
        self._icon.hide()

    def sync_to_settings(self) -> None:
        """Follow a tray setting the user changed without restarting the app."""
        if self._icon.isVisible() != self.enabled:
            (self.show if self.enabled else self.hide)()

    def message(self, title: str, body: str) -> None:
        """A balloon note, when the desktop can show one."""
        if self.available and QSystemTrayIcon.supportsMessages():
            self._icon.showMessage(title, body, self._icon.icon(), 5000)

    def explain_hide(self) -> None:
        """Say how to get the window back — at first, then only on request."""
        first = not self._hint_shown
        self._hint_shown = True
        if not first:
            self.message(APP_NAME, "The window is closed but the app is running. "
                                   "Double-click the tray icon to open it again.")
            return
        self.message(APP_NAME,
                     "Closed to the tray: scans, repairs and background jobs keep "
                     "running. Double-click the icon to open the window, or use "
                     "Quit in its menu to leave.")

    # ------------------------------------------------------------------ menu
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_window.emit()

    def _rebuild(self) -> None:
        try:
            self.build_menu(self._menu)
        except Exception:                          # noqa: BLE001 - the menu must open
            logger.exception("the tray menu could not read the current state")

    def build_menu(self, menu: QMenu) -> None:
        """Fill a menu from the state other processes wrote.

        Split out from the signal handler so a test can build it without a
        notification area.
        """
        menu.clear()
        _add(menu, f"Open {APP_NAME}", lambda: self.restore_window.emit())
        _add(menu, "Go to the Autopilot screen", lambda: self.show_autopilot.emit())
        menu.addSeparator()

        state = host_state()
        _add(menu, state["line"], None, note=state["detail"])
        if state["can_start"]:
            _add(menu, "Start the background host", self.start_host,
                 note="a detached process: nothing is registered with Windows, "
                      "and it ends with your session. Use "
                      "`pyfix360 service install` for autostart.")
        if state["can_stop"]:
            _add(menu, "Stop the background host", self.stop_host,
                 note="asks it to finish the step it is on; a job caught "
                      "mid-step is recorded interrupted, not rolled back")
        menu.addSeparator()

        counts = self.job_counts()
        open_jobs = sum(counts.get(name, 0) for name in _OPEN)
        waiting = counts.get("waiting_approval", 0)
        _add(menu, f"{open_jobs} job(s) on the queue"
                   + (f", {waiting} waiting for you" if waiting else ""), None)
        menu.addSeparator()
        _add(menu, f"Quit {APP_NAME}", lambda: self.quit_application.emit(),
             note="a background host started from this window is a separate "
                  "process and keeps running; stop it from this menu first")

    # ------------------------------------------------------------------ state
    def job_counts(self) -> dict[str, int]:
        try:
            with self.ctx.session() as s:
                return job_service.counters(s)
        except Exception:                          # noqa: BLE001 - first run
            logger.debug("the tray could not read the job queue", exc_info=True)
            return {}

    # ------------------------------------------------------------------ actions
    def start_host(self) -> None:
        report = host_service.install(mode=host_service.MODE_PROCESS)
        self.message("Background host", report.summary)
        if not report.ok:
            logger.warning("the tray could not start the host: %s", report.summary)

    def stop_host(self) -> None:
        report = host_service.stop()
        self.message("Background host", report.summary)


def host_state() -> dict:
    """What the menu says about the background host, in one line.

    :func:`host_service.status` is not used here because it shells out to
    ``schtasks`` for a scheduled-task install, and a menu that waits on Windows
    is worse than a menu that is one poll behind.
    """
    pid = host_service.live_pid()
    running = f"Background host: running (pid {pid})" if pid \
        else "Background host: not running"
    return {
        "line": running,
        "detail": f"installed as: {host_service.installed_mode() or 'nothing'}",
        "can_start": pid is None,
        "can_stop": pid is not None,
    }


def _add(menu: QMenu, text: str, slot: Callable[[], None] | None, *,
         note: str = "") -> QAction:
    """A menu row. ``slot=None`` makes it a read-only line of state."""
    action = QAction(text, menu)
    action.setEnabled(slot is not None)
    if note:
        action.setToolTip(note)
    if slot is not None:
        action.triggered.connect(lambda _checked=False: slot())
    menu.addAction(action)
    return action
