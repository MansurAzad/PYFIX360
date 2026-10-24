"""Shared UI application context."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from pyfix360.config import get_config
from pyfix360.db import init_db, session_scope


class AppContext(QObject):
    """Holds the selected project and broadcasts data changes."""

    project_changed = Signal(object)   # int | None
    data_changed = Signal()
    status_message = Signal(str)
    notification_requested = Signal(str, str)   # title, body

    def __init__(self) -> None:
        super().__init__()
        self._project_id: int | None = None
        self._autopilot = None
        init_db()

    @property
    def autopilot(self):
        """The one autopilot controller, created on first use.

        Lazy because constructing it installs a filter on the live
        ``QApplication`` and starts owning a ``QTimer``; the context itself is
        built by callers that may not have widgets at all. Screens and the main
        window both read the controller here — it must be the same object, or
        the badge and the screen would report two different runs.
        """
        if self._autopilot is None:
            from PySide6.QtWidgets import QApplication

            from pyfix360.ui.autopilot import AutopilotController

            app = QApplication.instance()
            if app is None:                                    # pragma: no cover
                raise RuntimeError(
                    "autopilot needs a running QApplication; no gestures will work")
            controller = AutopilotController(self)
            controller.install(app)
            self._autopilot = controller
        return self._autopilot

    @property
    def project_id(self) -> int | None:
        return self._project_id

    def set_project(self, project_id: int | None) -> None:
        self._project_id = project_id
        self.project_changed.emit(project_id)

    def session(self):
        return session_scope()

    def notify_changed(self) -> None:
        self.data_changed.emit()

    def status(self, message: str) -> None:
        self.status_message.emit(message)

    def notify(self, kind: str, title: str, body: str) -> bool:
        """Ask for a desktop notification, if the user wants this kind.

        ``kind`` is ``"scan"`` or ``"repair"`` and selects the switch in
        Settings → Notifications. Returns whether a notification was asked
        for, so a caller can fall back to the status bar.
        """
        from pyfix360.services import settings_service

        with self.session() as session:
            settings = settings_service.get_settings(session)
        if kind == "scan" and not settings.notify_on_scan_complete:
            return False
        if kind == "repair" and not settings.notify_on_repair_verified:
            return False
        if kind not in ("scan", "repair"):
            return False
        self.notification_requested.emit(title, body)
        return True

    @property
    def config(self):
        return get_config()

    # ----------------------------------------------------------------------------------
    # access control
    # ----------------------------------------------------------------------------------

    def access_level(self, capability: str):
        """The policy level for ``capability`` as the policy table stores it."""
        from pyfix360.services import settings_service

        with self.session() as session:
            return settings_service.access_level(session, capability)

    def confirm_access(self, capability: str, action: str, parent=None) -> bool:
        """Ask the user for consent to an action the policy marks as ASK.

        Returns True only when the action may proceed: FULL passes silently,
        ASK puts the question in front of the human, BLOCKED never passes. The
        service layer re-checks the same level, so this dialog is the *source*
        of the ``confirmed`` flag, not the thing that enforces it.
        """
        from PySide6.QtWidgets import QMessageBox

        from pyfix360.models import AccessLevel

        level = self.access_level(capability)
        if level is AccessLevel.BLOCKED:
            self._warn_blocked(capability, action, parent)
            return False
        if level is AccessLevel.FULL:
            return True
        answer = QMessageBox.question(
            parent,
            "Confirm action",
            f"{action}\n\n"
            f"'{capability}' is set to ASK in Settings → Access Control.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def access_denied(self, capability: str, action: str, parent=None) -> bool:
        """True when the policy blocks the action. The reason is shown.

        For screens that already ask their own question before acting: the
        question *is* the consent, so the caller passes ``confirmed=True`` and
        only needs the BLOCKED case handled up front.
        """
        from pyfix360.models import AccessLevel

        if self.access_level(capability) is not AccessLevel.BLOCKED:
            return False
        self._warn_blocked(capability, action, parent)
        return True

    @staticmethod
    def _warn_blocked(capability: str, action: str, parent) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            parent,
            "Blocked by policy",
            f"'{capability}' is set to BLOCKED in Settings → Access Control.\n\n"
            f"{action} is not allowed.",
        )

    def check_access(self, capability: str, *, confirmed: bool = False) -> None:
        """Service-level check; raises :class:`AccessDenied` when refused."""
        from pyfix360.services import settings_service

        with self.session() as session:
            settings_service.check_access(session, capability, confirmed=confirmed)
