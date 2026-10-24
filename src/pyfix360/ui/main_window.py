"""Main application window with the sidebar navigation.

The window owns the application theme. Any screen can ask for a theme change
by calling ``apply_theme`` (the Settings Center does exactly that on every
preview), which re-generates the stylesheet and re-colours the theme-aware
widgets. Layout adapts to the window size: the sidebar can be collapsed, the
top bar wraps, and the status bar can be hidden.

Closing the window hides it when the tray icon is up and quits the application
when it is not — see :meth:`MainWindow.closeEvent`, which is the only place that
decision is made.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from pyfix360 import APP_NAME, __version__
from pyfix360.branding import icon_path
from pyfix360.db.models import ProjectRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.screens.ai_providers import AIScreen
from pyfix360.ui.screens.api_db import ApiDbScreen
from pyfix360.ui.screens.assistant import AssistantScreen
from pyfix360.ui.screens.autopilot import AutopilotScreen
from pyfix360.ui.screens.dashboard import DashboardScreen
from pyfix360.ui.screens.dependencies import DependenciesScreen
from pyfix360.ui.screens.explorer import ExplorerScreen
from pyfix360.ui.screens.features import FeatureScreen
from pyfix360.ui.screens.gates import GatesScreen
from pyfix360.ui.screens.inspection import InspectionScreen
from pyfix360.ui.screens.integrations import IntegrationsScreen
from pyfix360.ui.screens.projects import ProjectsScreen
from pyfix360.ui.screens.repair import PatchViewerScreen, RepairScreen
from pyfix360.ui.screens.reports import ReportsScreen
from pyfix360.ui.screens.security import SecurityScreen
from pyfix360.ui.screens.settings import SettingsScreen
from pyfix360.ui.screens.testing import RollbackScreen, TestingScreen
from pyfix360.ui.screens.ui_inspector import UiInspectorScreen
from pyfix360.ui.theme import get_theme, stylesheet, theme_with_accent
from pyfix360.ui.widgets import set_current_theme

SCREENS: list[tuple[str, type]] = [
    ("Overview", DashboardScreen),
    ("Autopilot", AutopilotScreen),
    ("Project Manager", ProjectsScreen),
    ("Project Explorer", ExplorerScreen),
    ("Inspection Center", InspectionScreen),
    ("Security Audit", SecurityScreen),
    ("API & Database", ApiDbScreen),
    ("Website & UI", UiInspectorScreen),
    ("Feature Completeness", FeatureScreen),
    ("Integrations & CLIs", IntegrationsScreen),
    ("Quality Gates", GatesScreen),
    ("AI Providers", AIScreen),
    ("AI Assistant", AssistantScreen),
    ("Repair Planning", RepairScreen),
    ("Patch & Diff Viewer", PatchViewerScreen),
    ("Testing & Verification", TestingScreen),
    ("Dependency Manager", DependenciesScreen),
    ("Reports & Export", ReportsScreen),
    ("Repair History", RollbackScreen),
    ("Settings Center", SettingsScreen),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1480, 940)
        self.setMinimumSize(900, 600)

        self._theme = theme_with_accent(get_theme(ctx.config.theme),
                                     ctx.config.accent_override)
        self._font_size = ctx.config.font_size
        self._density = ctx.config.density
        self._radius = ctx.config.corner_radius
        self._motion = ctx.config.motion_enabled

        #: The tray icon, when the app has one. See :meth:`closeEvent`.
        self.tray = None
        self._quitting = False

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(min(340, max(160, ctx.config.sidebar_width)))
        for label, _cls in SCREENS:
            self.sidebar.addItem(QListWidgetItem(label))
        self.sidebar.currentRowChanged.connect(self._switch)
        body_lay.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.screens = []
        for _label, cls in SCREENS:
            screen = cls(ctx)
            self.screens.append(screen)
            self.stack.addWidget(screen)
        body_lay.addWidget(self.stack, 1)
        outer.addWidget(body, 1)

        self.setCentralWidget(central)
        self._status_bar = None
        if getattr(ctx.config, "show_status_bar", True):
            self._status_bar = QStatusBar()
            self.setStatusBar(self._status_bar)
            self.ctx.status_message.connect(self._status_bar.showMessage)
        self.ctx.data_changed.connect(self._reload_projects)
        self.autopilot = ctx.autopilot
        self.autopilot.changed.connect(self._refresh_autopilot_badge)
        if self._status_bar is not None:
            self._status_bar.showMessage(
                "Offline-first. No source code leaves this machine unless you enable "
                "and approve a remote AI provider.")

        self.apply_theme(self._theme, font_size=self._font_size,
                         density=self._density, radius=self._radius,
                         motion=self._motion)

        self._select_start_screen()
        self._reload_projects()

    # ------------------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        top = QWidget()
        top.setObjectName("topbar")
        lay = QHBoxLayout(top)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(10)

        self.menu_button = QPushButton("☰")
        self.menu_button.setObjectName("ghost")
        self.menu_button.setFixedWidth(36)
        self.menu_button.setToolTip("Collapse or expand the navigation sidebar")
        self.menu_button.clicked.connect(self._toggle_sidebar)
        lay.addWidget(self.menu_button)

        self.logo = QLabel()
        self.logo.setFixedSize(26, 26)
        self.logo.setToolTip(f"{APP_NAME} {__version__}")
        icon = icon_path()
        if icon is not None:
            from PySide6.QtGui import QIcon

            self.logo.setPixmap(QIcon(str(icon)).pixmap(26, 26))
        lay.addWidget(self.logo)

        self.brand = QLabel(
            f"<b>{APP_NAME}</b> <span style='color:gray'>v{__version__}</span>")
        lay.addWidget(self.brand)
        lay.addStretch(1)

        lay.addWidget(QLabel("Active project:"))
        self.project_selector = QComboBox()
        self.project_selector.setMinimumWidth(240)
        self.project_selector.setMinimumContentsLength(18)
        self.project_selector.currentIndexChanged.connect(self._project_selected)
        lay.addWidget(self.project_selector, 2)

        self.privacy_badge = QLabel("")
        self.privacy_badge.setObjectName("badge")
        lay.addWidget(self.privacy_badge)

        self.gate_badge = QLabel("")
        self.gate_badge.setObjectName("badge")
        lay.addWidget(self.gate_badge)

        self.autopilot_badge = QLabel("")
        self.autopilot_badge.setObjectName("badge")
        self.autopilot_badge.setToolTip("Autopilot run state")
        lay.addWidget(self.autopilot_badge)

        self.theme_button = QPushButton("Theme")
        self.theme_button.setObjectName("ghost")
        self.theme_button.setToolTip("Cycle the colour theme")
        self.theme_button.clicked.connect(self._cycle_theme)
        lay.addWidget(self.theme_button)
        return top

    # ------------------------------------------------------------------
    def apply_theme(self, theme, *, font_size: int | None = None,
                    density: str | None = None, radius: int | None = None,
                    motion: bool | None = None) -> None:
        # The configured accent only applies when the caller did not already
        # pick one — the Settings screen previews its own colour this way.
        base = get_theme(getattr(self.ctx.config, "theme", None))
        override = getattr(self.ctx.config, "accent_override", "")
        if override and theme.accent == base.accent:
            theme = theme_with_accent(theme, override)
        self._theme = theme
        if font_size is not None:
            self._font_size = font_size
        if density is not None:
            self._density = density
        if radius is not None:
            self._radius = radius
        if motion is not None:
            self._motion = motion
        set_current_theme(theme)
        self.setStyleSheet(stylesheet(
            theme, font_size=self._font_size, density=self._density,
            radius=self._radius, motion=self._motion))
        # refresh theme-aware dynamic styles (badges, cell colours)
        self._refresh_badges()
        self._apply_font()

    def _apply_font(self) -> None:
        from PySide6.QtGui import QFont

        family = self.ctx.config.font_family if hasattr(
            self.ctx.config, "font_family") else ""
        font = QFont(family, self._font_size) if family else QFont(
            self.font().family(), self._font_size)
        self.setFont(font)

    def _cycle_theme(self) -> None:
        from pyfix360.ui.theme import THEMES

        keys = list(THEMES.keys())
        current = self._theme.key
        index = (keys.index(current) + 1) % len(keys) if current in keys else 0
        self.apply_theme(get_theme(keys[index]))
        self.ctx.status(f"Theme: {self._theme.label}")

    def _toggle_sidebar(self) -> None:
        width = self.sidebar.width()
        if width > 60:
            self._sidebar_restore = width
            self.sidebar.setFixedWidth(0)
            self.sidebar.setVisible(False)
        else:
            self.sidebar.setVisible(True)
            self.sidebar.setFixedWidth(getattr(self, "_sidebar_restore", 210))

    def _select_start_screen(self) -> None:
        wanted = getattr(self.ctx.config, "start_screen", "Overview")
        for row, (label, _cls) in enumerate(SCREENS):
            if label == wanted:
                self.sidebar.setCurrentRow(row)
                return
        self.sidebar.setCurrentRow(0)

    def _refresh_badges(self) -> None:
        from pyfix360.services import gate_service, settings_service

        theme = self._theme
        try:
            with self.ctx.session() as s:
                settings = settings_service.get_settings(s)
                pid = self.ctx.project_id
                remote = settings.privacy_mode.value != "local_only"
                self.privacy_badge.setText(settings.privacy_mode.value)
                color = theme.warning if remote else theme.success
                self.privacy_badge.setStyleSheet(
                    f"background:{color}22; color:{color};"
                    f"border:1px solid {color}66; border-radius:9px;"
                    "padding:2px 9px; font-weight:600;")
                if pid:
                    report = gate_service.evaluate(s, pid)
                    line = gate_service.summary_line(report)
                    failed = bool(report.failed_blocking)
                    gcolor = theme.danger if failed else theme.success
                    self.gate_badge.setText(
                        "gates: FAIL" if failed else "gates: pass")
                    self.gate_badge.setToolTip(line)
                else:
                    self.gate_badge.setText("gates: —")
                    gcolor = theme.text_muted
                self.gate_badge.setStyleSheet(
                    f"background:{gcolor}22; color:{gcolor};"
                    f"border:1px solid {gcolor}66; border-radius:9px;"
                    "padding:2px 9px; font-weight:600;")
        except Exception:  # pragma: no cover - first run/no DB
            self.privacy_badge.setText("local-only")
        self._refresh_autopilot_badge()

    def _refresh_autopilot_badge(self) -> None:
        """The run state never hides behind a page: it lives in the toolbar.

        A gesture that pauses a run from anywhere in the app has to be answerable
        from anywhere too, so the badge states what keypress is wanted *now*
        rather than making the operator find the Autopilot screen first.
        """
        from pyfix360.services.autopilot_service import State

        theme = self._theme
        pilot = self.autopilot.pilot
        if pilot.pending is not None:
            text, color = "press Enter to approve", theme.danger
        elif pilot.state is State.RUNNING:
            done, total = pilot.progress
            text, color = f"autopilot: running {done}/{total}", theme.success
        elif pilot.state is State.PAUSED:
            text, color = "autopilot: paused", theme.warning
        else:
            text, color = "autopilot: idle", theme.text_muted
        self.autopilot_badge.setText(text)
        self.autopilot_badge.setToolTip(pilot.status_line())
        self.autopilot_badge.setStyleSheet(
            f"background:{color}22; color:{color};"
            f"border:1px solid {color}66; border-radius:9px;"
            "padding:2px 9px; font-weight:600;")

    # ------------------------------------------------------------------
    def _switch(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _reload_projects(self) -> None:
        current = self.ctx.project_id
        self.project_selector.blockSignals(True)
        self.project_selector.clear()
        self.project_selector.addItem("— none —", None)
        with self.ctx.session() as s:
            for p in s.query(ProjectRow).order_by(ProjectRow.id.desc()).all():
                self.project_selector.addItem(f"#{p.id} {p.name}", p.id)
        if current is not None:
            idx = self.project_selector.findData(current)
            if idx >= 0:
                self.project_selector.setCurrentIndex(idx)
        self.project_selector.blockSignals(False)
        self._refresh_badges()

    def _project_selected(self, _index: int) -> None:
        pid = self.project_selector.currentData()
        self.ctx.set_project(pid)
        if pid:
            self.ctx.status(f"Active project #{pid}")
        self._refresh_badges()

    # ------------------------------------------------------------------ lifetime
    def set_tray(self, tray) -> None:
        """Hand the window its tray icon, and connect the two ways in and out."""
        self.tray = tray
        if tray is None:
            return
        tray.restore_window.connect(self.show_from_tray)
        tray.show_autopilot.connect(self.show_autopilot_screen)
        tray.quit_application.connect(self.quit_application)
        self.ctx.notification_requested.connect(self._show_notification)

    def _show_notification(self, title: str, body: str) -> None:
        """Deliver a notification: the tray balloon if there is one."""
        if self.tray is not None:
            self.tray.message(title, body)
        elif self._status_bar is not None:
            self._status_bar.showMessage(f"{title}: {body}", 8000)

    def show_from_tray(self) -> None:
        """Undo a close-to-tray, including on the desktops that minimise to nothing."""
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def show_autopilot_screen(self) -> None:
        self.show_from_tray()
        self.sidebar.setCurrentRow(_screen_row("Autopilot"))

    def quit_application(self) -> None:
        """The real exit, from the tray menu or from a close the tray refused."""
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        from PySide6.QtWidgets import QApplication

        self.close()
        app = QApplication.instance()
        if app is not None:                        # pragma: no branch
            app.quit()

    def closeEvent(self, event) -> None:
        """Close either hides or ends the application; it never just strands it.

        Hiding is only allowed while the tray icon is up, because the icon is the
        way back and the only Quit. The background host is a separate process
        either way — closing this window does not stop jobs, and saying otherwise
        here would be the same lie the requirement was written to avoid.
        """
        if self._quitting or self.tray is None or not self.tray.should_hide_on_close:
            self._quitting = True
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.explain_hide()


def _screen_row(label: str) -> int:
    """The sidebar row for a screen label, or Overview."""
    for row, (name, _cls) in enumerate(SCREENS):
        if name == label:
            return row
    return 0

