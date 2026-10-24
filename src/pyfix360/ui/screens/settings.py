"""Settings Center — 360° configuration.

Tabs, each mapped to a group in :class:`pyfix360.models.AppSettings`:

* Appearance      theme, accent, typography, density, motion, startup screen
* Appearance also previews contrast ratios so the 'readable' claim is checkable
* Scanning        globs, limits, which analyzers run, incremental mode
* Findings        severity floor, grouping, acknowledgement behaviour
* AI              behaviour, default provider, prompt caps, repo-content gate
* Privacy         global privacy mode; redaction is mandatory and cannot be off
* Repair & Safety approval requirements, dry-run, backup retention
* Access Control  per-capability FULL / ASK / BLOCKED matrix
* Quality Gates   CRUD for the declarative gates
* Tools           custom test command and tool paths
* Notifications   what to surface, log level and retention
* Background      tray icon, close-to-tray, the background host's interval and
                  what is installed right now
* Data & Backup   export/import settings, inspect the data directory, reset

Every tab reads from the same :class:`AppSettings` object so a single "Save"
writes consistent state. Destructive actions live in a clearly marked section
and are gated by the access policy itself.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyfix360.models import (
    AccessLevel,
    AIBehavior,
    AppSettings,
    Density,
    PrivacyMode,
    Severity,
    ThemeKey,
)
from pyfix360.services.gate_service import METRICS, OPERATORS
from pyfix360.ui.context import AppContext
from pyfix360.ui.theme import get_theme, theme_labels
from pyfix360.ui.widgets import (
    fill_table,
    make_button,
    make_table,
    page_header,
    scrollable,
)
from pyfix360.ui.workers import run_async

#: capabilities exposed in the Access Control matrix, with a plain description
ACCESS_MATRIX: list[tuple[str, str, str]] = [
    ("access_apply_to_original", "Apply a verified patch to the original project",
     "The single most consequential action: it edits the user's real files."),
    ("access_rollback", "Roll a patch back from a snapshot",
     "Always safe — it restores prior state — but still recorded."),
    ("access_run_tests", "Run the project's test suite in the sandbox",
     "Executes third-party code, so isolation settings apply."),
    ("access_delete_project", "Delete a project and its history",
     "Removes the record; the imported source folder is never touched."),
    ("access_purge_history", "Purge audit history permanently",
     "Breaks the audit trail. Blocked unless you deliberately allow it."),
    ("access_remote_ai", "Send anything to a remote AI endpoint",
     "Local runtimes are unaffected by this switch."),
    ("access_export_reports", "Export reports to disk",
     "Writes markdown/JSON/HTML outside the data directory."),
    ("access_plugin_tools", "Run third-party analyzer adapters",
     "ruff/bandit/pip-audit and any future plugins."),
    ("access_edit_quality_gates", "Change the quality gates",
     "Gates define what counts as 'done' for this project."),
    ("access_autopilot", "Start an unattended autopilot run",
     "Autopilot performs every step the pipeline reaches without further "
     "input, so starting it is itself an action worth confirming. Steps that "
     "hit an ASK capability still pause and ask."),
    ("access_danger_zone", "Use the Danger Zone actions",
     "Irreversible maintenance operations (purge, hard reset)."),
    ("access_check_for_updates", "Check GitHub for a new version",
     "A remote call. ASK keeps the check behind a click, FULL lets the app "
     "check on its own, BLOCKED means it never phones home."),
]

def _note(text: str) -> QLabel:
    """A muted, wrapping paragraph of consequence next to a control."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("muted")
    return label


ACCESS_LEVEL_HELP = {
    AccessLevel.FULL: "Allowed silently.",
    AccessLevel.ASK: "Allowed only after an explicit confirmation dialog.",
    AccessLevel.BLOCKED: "Refused; the action is disabled in the UI and rejected "
                         "by the service.",
}


class SettingsScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._settings: AppSettings | None = None
        self._update_info = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(10)
        outer.addWidget(page_header(
            "Settings Center",
            "Every option in one place: appearance, scanning, AI, privacy, "
            "safety approvals, access control and quality gates. Changes are "
            "written only when you press Save.",
        ))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._tab_appearance(), "Appearance")
        self.tabs.addTab(self._tab_scanning(), "Scanning")
        self.tabs.addTab(self._tab_findings(), "Findings")
        self.tabs.addTab(self._tab_ai(), "AI")
        self.tabs.addTab(self._tab_privacy(), "Privacy")
        self.tabs.addTab(self._tab_repair(), "Repair & Safety")
        self.tabs.addTab(self._tab_access(), "Access Control")
        self.tabs.addTab(self._tab_gates(), "Quality Gates")
        self.tabs.addTab(self._tab_tools(), "Tools & Commands")
        self.tabs.addTab(self._tab_notifications(), "Notifications")
        self.tabs.addTab(self._tab_background(), "Background")
        self.tabs.addTab(self._tab_data(), "Data & Backup")
        outer.addWidget(self.tabs, 1)

        bar = QHBoxLayout()
        bar.addWidget(make_button("Save all settings", self._save))
        bar.addWidget(make_button("Reload", self.refresh, secondary=True))
        bar.addWidget(make_button("Reset to defaults", self._reset, danger=True))
        bar.addStretch(1)
        self.dirty = QLabel("")
        self.dirty.setObjectName("muted")
        bar.addWidget(self.dirty)
        outer.addLayout(bar)

        self.refresh()

    # ==================================================================
    # Appearance
    # ==================================================================
    def _tab_appearance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        theme_box = QGroupBox("Theme and colour")
        form = QFormLayout(theme_box)
        self.theme = QComboBox()
        for key, label in theme_labels():
            self.theme.addItem(label, key)
        self.theme.currentIndexChanged.connect(self._preview_theme)
        form.addRow("Theme", self.theme)

        accent_row = QHBoxLayout()
        self.accent = QLineEdit()
        self.accent.setPlaceholderText("blank = the theme's own accent")
        self.accent.textChanged.connect(self._preview_theme)
        pick = make_button("Pick…", self._pick_accent, secondary=True)
        clear = make_button("Theme default", self._clear_accent, ghost=True)
        accent_row.addWidget(self.accent, 1)
        accent_row.addWidget(pick)
        accent_row.addWidget(clear)
        accent_w = QWidget()
        accent_w.setLayout(accent_row)
        form.addRow("Accent override", accent_w)

        self.font_size = QSpinBox()
        self.font_size.setRange(10, 20)
        self.font_size.setSuffix(" px")
        self.font_size.valueChanged.connect(self._preview_theme)
        form.addRow("Base font size", self.font_size)

        self.font_family = QLineEdit()
        self.font_family.setPlaceholderText("blank = system default")
        form.addRow("Font family", self.font_family)

        self.density = QComboBox()
        for d in Density:
            self.density.addItem(d.value, d.value)
        form.addRow("Density", self.density)

        self.radius = QSpinBox()
        self.radius.setRange(0, 16)
        form.addRow("Corner radius", self.radius)

        self.motion = QCheckBox("enable subtle transitions / hover feedback")
        form.addRow("Motion", self.motion)

        self.sidebar_width = QSlider(Qt.Horizontal)
        self.sidebar_width.setRange(160, 340)
        form.addRow("Sidebar width", self.sidebar_width)
        lay.addWidget(theme_box)

        # contrast audit — makes the readability claim verifiable
        self.contrast_box = QGroupBox("Contrast audit (WCAG)")
        self.contrast_grid = QGridLayout(self.contrast_box)
        lay.addWidget(self.contrast_box)

        start_box = QGroupBox("Startup")
        sform = QFormLayout(start_box)
        self.start_screen = QLineEdit()
        self.start_screen.setPlaceholderText("e.g. Overview")
        sform.addRow("Open this screen on launch", self.start_screen)
        lay.addWidget(start_box)

        lay.addStretch(1)
        return scrollable(page)

    def _pick_accent(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self.accent.setText(color.name())

    def _clear_accent(self) -> None:
        self.accent.setText("")

    def _preview_theme(self) -> None:
        """Apply the theme immediately so the user sees the result live."""
        from dataclasses import replace

        theme = get_theme(self.theme.currentData())
        override = self.accent.text().strip()
        if override:
            # Clearing accent_text lets the derived ``button_text`` pick
            # black or white from the *new* accent, so a custom colour cannot
            # silently make button labels unreadable.
            theme = replace(theme, accent=override, accent_text="")
        self._refresh_contrast(theme)
        window = self.window()
        if hasattr(window, "apply_theme"):
            window.apply_theme(
                theme,
                font_size=self.font_size.value(),
                density=self.density.currentData(),
                radius=self.radius.value(),
                motion=self.motion.isChecked(),
            )

    def _refresh_contrast(self, theme) -> None:
        while self.contrast_grid.count():
            item = self.contrast_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        checks = [
            ("Body text on surface", theme.text, theme.surface, 7.0),
            ("Muted text on surface", theme.text_muted, theme.surface, 4.5),
            ("Button text on accent", theme.button_text, theme.accent, 4.5),
            ("Body text on background", theme.text, theme.bg, 7.0),
            ("Danger on surface", theme.danger, theme.surface, 3.0),
            ("Success on surface", theme.success, theme.surface, 3.0),
        ]
        for row, (label, fg, bg, target) in enumerate(checks):
            ratio = theme.contrast(fg, bg)
            ok = ratio >= target
            name = QLabel(label)
            value = QLabel(f"{ratio:.2f}:1  (target {target:.1f})")
            value.setStyleSheet(
                f"color:{theme.success if ok else theme.danger}; font-weight:600;")
            verdict = QLabel("AA/AAA" if ok else "below target")
            verdict.setStyleSheet(
                f"color:{theme.success if ok else theme.warning};")
            self.contrast_grid.addWidget(name, row, 0)
            self.contrast_grid.addWidget(value, row, 1)
            self.contrast_grid.addWidget(verdict, row, 2)

    # ==================================================================
    # Scanning
    # ==================================================================
    def _tab_scanning(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        scope = QGroupBox("Scope")
        form = QFormLayout(scope)
        self.include_globs = QPlainTextEdit()
        self.include_globs.setPlaceholderText("one glob per line, blank = everything")
        self.include_globs.setMaximumHeight(80)
        form.addRow("Include globs", self.include_globs)
        self.exclude_globs = QPlainTextEdit()
        self.exclude_globs.setPlaceholderText(
            "one glob per line, e.g. **/vendor/**\n**/.venv/**")
        self.exclude_globs.setMaximumHeight(80)
        form.addRow("Exclude globs", self.exclude_globs)
        self.skip_generated = QCheckBox("skip files that look generated")
        form.addRow("Generated files", self.skip_generated)
        self.max_file_kb = QSpinBox()
        self.max_file_kb.setRange(8, 200000)
        self.max_file_kb.setSuffix(" KB")
        form.addRow("Skip files larger than", self.max_file_kb)
        self.max_files = QSpinBox()
        self.max_files.setRange(10, 1000000)
        form.addRow("Maximum files per scan", self.max_files)
        lay.addWidget(scope)

        perf = QGroupBox("Performance")
        pform = QFormLayout(perf)
        self.workers = QSpinBox()
        self.workers.setRange(0, 64)
        self.workers.setSpecialValueText("auto")
        pform.addRow("Parallel workers", self.workers)
        self.incremental = QCheckBox(
            "incremental: re-analyse only files whose contents changed")
        pform.addRow("Incremental scanning", self.incremental)
        lay.addWidget(perf)

        tools = QGroupBox("Analyzers")
        tools.setToolTip(
            "The builtin analyzer always runs. Unavailable optional tools make "
            "the scan 'partial' and are listed explicitly in the report.")
        tlay = QVBoxLayout(tools)
        self.analyzer_checks: dict[str, QCheckBox] = {}
        for name, desc in [
            ("builtin", "PYFIX builtin AST analyzer (always available)"),
            ("ruff", "Ruff — lint and style"),
            ("bandit", "Bandit — security"),
            ("pip-audit", "pip-audit — dependency vulnerabilities"),
        ]:
            cb = QCheckBox(f"{name} — {desc}")
            self.analyzer_checks[name] = cb
            tlay.addWidget(cb)
        lay.addWidget(tools)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # Findings
    # ==================================================================
    def _tab_findings(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        box = QGroupBox("Filtering and grouping")
        form = QFormLayout(box)
        self.min_severity = QComboBox()
        for s in Severity:
            self.min_severity.addItem(s.value, s.value)
        form.addRow("Hide findings below", self.min_severity)
        self.group_root = QCheckBox(
            "group findings that share a root cause into one row")
        form.addRow("Root-cause grouping", self.group_root)
        self.auto_ack_info = QCheckBox(
            "auto-acknowledge informational findings on first view")
        form.addRow("Auto-acknowledge info", self.auto_ack_info)
        self.show_fp_rules = QCheckBox(
            "keep showing rules you previously marked as false positives")
        form.addRow("False-positive rules", self.show_fp_rules)
        lay.addWidget(box)

        note = QLabel(
            "Severity here only affects what the lists show. It never changes "
            "the stored finding, so lowering the filter cannot lose data.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # AI
    # ==================================================================
    def _tab_ai(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        box = QGroupBox("Behaviour")
        form = QFormLayout(box)
        self.ai_behavior = QComboBox()
        for b, desc in [
            (AIBehavior.OFF, "off — AI is never used"),
            (AIBehavior.SUGGEST, "suggest — AI may propose, you always decide"),
            (AIBehavior.EXPLAIN_ONLY,
             "explain only — AI explains but never drafts patches"),
        ]:
            self.ai_behavior.addItem(desc, b.value)
        form.addRow("AI behaviour", self.ai_behavior)

        self.ai_default = QComboBox()
        form.addRow("Default provider", self.ai_default)

        self.ai_auto_explain = QCheckBox(
            "offer an explanation automatically when a finding is selected")
        form.addRow("Auto-explain", self.ai_auto_explain)
        lay.addWidget(box)

        bounds = QGroupBox("Prompt bounds")
        bform = QFormLayout(bounds)
        self.ai_max_chars = QSpinBox()
        self.ai_max_chars.setRange(1000, 500000)
        self.ai_max_chars.setSingleStep(1000)
        self.ai_max_chars.setSuffix(" chars")
        bform.addRow("Maximum prompt size", self.ai_max_chars)
        self.ai_repo_gate = QCheckBox(
            "allow sending repository source to a remote provider")
        bform.addRow("Repository content", self.ai_repo_gate)
        self.redact_ai = QCheckBox("redact credentials and secrets before sending")
        self.redact_ai.setChecked(True)
        self.redact_ai.setEnabled(False)
        bform.addRow("Redaction", self.redact_ai)
        lay.addWidget(bounds)

        redaction_note = QLabel(
            "Redaction is always on. It is a guarantee of the privacy model "
            "rather than a preference, so it cannot be switched off here or "
            "through the settings store.")
        redaction_note.setWordWrap(True)
        redaction_note.setObjectName("muted")
        lay.addWidget(redaction_note)

        warn = QLabel(
            "<b>What this does not do.</b> These switches never make an "
            "unavailable model work. If a provider has no key, is offline, or "
            "has retired the model, PYFIX will report the failure verbatim "
            "rather than substituting a different model. Use the AI Providers "
            "screen to press <i>Test connection</i> and confirm a model really "
            "answers; only then is it shown as live-verified.")
        warn.setWordWrap(True)
        warn.setObjectName("muted")
        lay.addWidget(warn)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # Privacy
    # ==================================================================
    def _tab_privacy(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        box = QGroupBox("Global privacy mode")
        form = QFormLayout(box)
        self.privacy_mode = QComboBox()
        for mode, desc in [
            (PrivacyMode.LOCAL_ONLY,
             "local only — nothing is ever transmitted"),
            (PrivacyMode.REDACTED_REMOTE,
             "redacted remote — only redacted snippets may be sent"),
            (PrivacyMode.FULL_REMOTE_APPROVED,
             "full remote approved — remote processing explicitly approved"),
        ]:
            self.privacy_mode.addItem(desc, mode.value)
        form.addRow("Mode", self.privacy_mode)
        lay.addWidget(box)

        info = QLabel(
            "<b>How enforcement works.</b> The privacy mode is checked inside "
            "the AI gateway every single time a request is built — it is not a "
            "UI-only hint. A remote endpoint with mode <i>local only</i> is "
            "refused with a message telling you which setting to change. "
            "Credentials are redacted first and the request is logged as "
            "metadata only (a prompt hash, never the prompt).")
        info.setWordWrap(True)
        lay.addWidget(info)

        tele = QLabel(
            "<b>No telemetry.</b> PYFIX 360 collects nothing and sends "
            "nothing about how you use it — no usage counters, no crash "
            "reports, no analytics. The only data that ever leaves this "
            "machine is an AI request, and only under the privacy mode chosen "
            "above.")
        tele.setWordWrap(True)
        tele.setObjectName("muted")
        lay.addWidget(tele)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # Repair & Safety
    # ==================================================================
    def _tab_repair(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        exec_box = QGroupBox("Execution and snapshots")
        form = QFormLayout(exec_box)
        self.isolate = QCheckBox("run tests in an isolated workspace copy")
        form.addRow("Isolation", self.isolate)
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 7200)
        self.timeout.setSuffix(" s")
        form.addRow("Execution timeout", self.timeout)
        self.memory = QSpinBox()
        self.memory.setRange(128, 65536)
        self.memory.setSuffix(" MB")
        form.addRow("Memory limit (best-effort)", self.memory)
        self.network = QCheckBox("allow network inside the sandbox")
        form.addRow("Sandbox network", self.network)
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        form.addRow("Max repair retries", self.retries)
        self.require_snapshot = QCheckBox(
            "refuse to write anything before a snapshot exists")
        form.addRow("Snapshot requirement", self.require_snapshot)
        self.dry_run = QCheckBox("preview repairs without applying by default")
        form.addRow("Dry-run default", self.dry_run)
        lay.addWidget(exec_box)

        approvals = QGroupBox("Approval requirements")
        aform = QVBoxLayout(approvals)
        self.approvals: dict[str, QCheckBox] = {}
        for key, label in [
            ("require_approval_destructive", "destructive operations"),
            ("require_approval_security", "security-related changes"),
            ("require_approval_dependency",
             "major dependency upgrades"),
            ("require_approval_migration", "database migrations"),
            ("require_approval_production", "anything touching production"),
            ("block_write_outside_project", "block writes outside the project"),
        ]:
            cb = QCheckBox(f"require explicit approval for {label}")
            self.approvals[key] = cb
            aform.addWidget(cb)
        self.auto_commit = QCheckBox(
            "stage verified changes automatically (never commits without review)")
        aform.addWidget(self.auto_commit)
        lay.addWidget(approvals)

        retention = QGroupBox("Retention")
        rform = QFormLayout(retention)
        self.backup_days = QSpinBox()
        self.backup_days.setRange(1, 3650)
        self.backup_days.setSuffix(" days")
        rform.addRow("Keep snapshots for", self.backup_days)
        lay.addWidget(retention)

        auto_box = QGroupBox("Autopilot gesture window")
        autoform = QFormLayout(auto_box)
        self.gesture_window = QSpinBox()
        self.gesture_window.setRange(150, 2000)
        self.gesture_window.setSingleStep(50)
        self.gesture_window.setSuffix(" ms")
        self.gesture_window.setToolTip(
            "How long the app waits for a third Enter before it treats a double "
            "Enter as a pause. Measured from the *first* Enter, so a slow triple "
            "counts as a double — raise this if the pause fires when you meant "
            "to resume.")
        autoform.addRow("Double vs triple Enter", self.gesture_window)
        lay.addWidget(auto_box)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # Access Control
    # ==================================================================
    def _tab_access(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        intro = QLabel(
            "<b>Three levels per capability.</b> FULL allows the action "
            "silently, ASK allows it after a confirmation, BLOCKED refuses it "
            "in both the interface and the service layer. Blocking here is "
            "real, not cosmetic.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGroupBox("Permission matrix")
        form = QFormLayout(grid)
        self.access_combos: dict[str, QComboBox] = {}
        for key, label, help_text in ACCESS_MATRIX:
            combo = QComboBox()
            for level in AccessLevel:
                combo.addItem(level.value, level.value)
            combo.setToolTip(help_text)
            # a small label that explains the currently chosen level
            explain = QLabel("")
            explain.setObjectName("muted")
            explain.setWordWrap(True)

            def make_handler(cmb=combo, lbl=explain, cap=key):
                def handler() -> None:
                    lbl.setText(ACCESS_LEVEL_HELP[AccessLevel(cmb.currentData())])
                    # the Danger Zone buttons follow the danger-zone policy
                    if cap == "access_danger_zone":
                        self._refresh_danger_buttons()
                return handler

            combo.currentIndexChanged.connect(make_handler())
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(combo)
            hl.addWidget(explain)
            self.access_combos[key] = combo
            form.addRow(label, holder)
        lay.addWidget(grid)

        danger = QGroupBox("Danger Zone")
        danger.setStyleSheet("QGroupBox { border-color: palette(mid); }")
        dlay = QVBoxLayout(danger)
        warn = QLabel(
            "These actions are irreversible. They stay disabled while "
            "'Danger Zone actions' above is set to <b>blocked</b> (the default).")
        warn.setWordWrap(True)
        dlay.addWidget(warn)
        drow = QHBoxLayout()
        self.danger_buttons = [
            make_button("Purge all audit history", self._purge_history,
                        danger=True),
            make_button("Delete every project record",
                        self._delete_all_projects, danger=True),
            make_button("Delete stored AI credentials",
                        self._purge_credentials, danger=True),
        ]
        for button in self.danger_buttons:
            drow.addWidget(button)
        drow.addStretch(1)
        dlay.addLayout(drow)
        lay.addWidget(danger)
        lay.addStretch(1)
        self._refresh_danger_buttons()
        return scrollable(page)

    def _refresh_danger_buttons(self) -> None:
        """Grey out the Danger Zone buttons while the policy blocks them.

        The buttons are still refused by the service (see ``_confirm_danger``);
        disabling them here only makes the refusal visible before the click.
        """
        combo = self.access_combos.get("access_danger_zone")
        blocked = (combo is not None
                   and combo.currentData() == AccessLevel.BLOCKED.value)
        for button in getattr(self, "danger_buttons", []):
            button.setEnabled(not blocked)
            button.setToolTip(
                "Blocked by the access policy — change 'Danger Zone actions' "
                "in the Permission matrix to allow this." if blocked else "")

    def _confirm_danger(self, capability: str, action: str) -> bool:
        """Two gates: the Danger Zone master switch, then the action's own one.

        The master switch decides whether the zone is reachable at all; the
        per-action capability decides whether *this* irreversible act is
        allowed. Both must pass.
        """
        from pyfix360.services import settings_service

        with self.ctx.session() as s:
            zone = settings_service.access_level(s, "danger_zone")
            own = settings_service.access_level(s, capability)
        if zone is AccessLevel.BLOCKED:
            QMessageBox.warning(
                self, "Blocked",
                "Danger Zone actions are blocked. Set 'Danger Zone actions' to "
                "ask or full in the Permission matrix, save, and try again.")
            return False
        if own is AccessLevel.BLOCKED:
            QMessageBox.warning(
                self, "Blocked",
                f"'{capability}' is blocked. Set it to ask or full in the "
                "Permission matrix, save, and try again.")
            return False
        if zone is AccessLevel.ASK or own is AccessLevel.ASK:
            answer = QMessageBox.warning(
                self, "Confirm",
                f"{action}\n\nThis is irreversible. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            return answer == QMessageBox.Yes
        return True

    def _purge_history(self) -> None:
        if not self._confirm_danger("purge_history", "Purge every audit event?"):
            return
        from pyfix360.services import audit

        with self.ctx.session() as s:
            count = audit.purge_history(s, confirmed=True)
        QMessageBox.information(self, "Done", f"Purged {count} audit events.")

    def _delete_all_projects(self) -> None:
        if not self._confirm_danger("delete_project",
                                     "Delete every project record?"):
            return
        from pyfix360.db.models import ProjectRow
        from pyfix360.services import project_service

        with self.ctx.session() as s:
            ids = [row.id for row in s.query(ProjectRow).order_by(ProjectRow.id).all()]
            count = sum(
                1 for pid in ids
                if project_service.delete_project(s, pid, confirmed=True)
            )
        self.ctx.notify_changed()
        QMessageBox.information(self, "Done", f"Deleted {count} project records. "
                                              "Imported source folders were not touched.")

    def _purge_credentials(self) -> None:
        if not self._confirm_danger("danger_zone",
                                     "Delete every stored AI credential?"):
            return
        from pyfix360.credentials import store as cred_store
        from pyfix360.db.models import AIProviderRow

        removed = 0
        with self.ctx.session() as s:
            for row in s.query(AIProviderRow).all():
                if cred_store.delete_credential(f"provider-{row.id}"):
                    removed += 1
        QMessageBox.information(self, "Done", f"Removed {removed} stored credential(s).")

    # ==================================================================
    # Quality Gates
    # ==================================================================
    def _tab_gates(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        intro = QLabel(
            "Gates define what 'done' means for this project. Each gate is "
            "evaluated against the latest scan and reports the exact findings "
            "that caused a failure.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form_box = QGroupBox("Gate editor")
        gform = QFormLayout(form_box)
        self.gate_name = QLineEdit()
        gform.addRow("Name", self.gate_name)
        self.gate_metric_box = QComboBox()
        for key, desc in METRICS.items():
            self.gate_metric_box.addItem(f"{key} — {desc}", key)
        gform.addRow("Metric", self.gate_metric_box)
        self.gate_operator = QComboBox()
        self.gate_operator.addItems(OPERATORS)
        gform.addRow("Operator", self.gate_operator)
        self.gate_threshold = QSpinBox()
        self.gate_threshold.setRange(0, 100000)
        gform.addRow("Threshold", self.gate_threshold)
        self.gate_severity = QComboBox()
        for sev in Severity:
            self.gate_severity.addItem(sev.value, sev.value)
        gform.addRow("Severity", self.gate_severity)
        self.gate_blocking = QCheckBox("blocking (failing this fails the project)")
        gform.addRow("Blocking", self.gate_blocking)
        lay.addWidget(form_box)

        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Add gate", self._add_gate))
        buttons.addWidget(make_button("Update selected", self._update_gate,
                                      secondary=True))
        buttons.addWidget(make_button("Delete selected", self._delete_gate,
                                      danger=True))
        buttons.addWidget(make_button("Install starter gates", self._seed_gates,
                                      secondary=True))
        buttons.addWidget(make_button("Evaluate now", self._evaluate_gates,
                                      secondary=True))
        buttons.addStretch(1)
        lay.addLayout(buttons)

        self.gate_table = make_table(
            ["id", "scope", "name", "metric", "condition", "severity",
             "blocking", "enabled"])
        self.gate_table.itemSelectionChanged.connect(self._select_gate)
        self.gate_table.setMinimumHeight(180)
        lay.addWidget(self.gate_table)

        lay.addWidget(QLabel("<b>Latest evaluation</b>"))
        self.gate_results = make_table(
            ["gate", "result", "metric", "observed", "expected", "detail"])
        self.gate_results.setMinimumHeight(180)
        lay.addWidget(self.gate_results)
        lay.addStretch(1)
        self._reload_gates()
        return scrollable(page)

    def _gate_project_id(self) -> int | None:
        return self.ctx.project_id

    def _reload_gates(self) -> None:
        from pyfix360.services import gate_service

        with self.ctx.session() as s:
            rows = gate_service.list_gates(s, self._gate_project_id())
            fill_table(self.gate_table, [
                [r.id, "global" if r.project_id is None else f"project {r.project_id}",
                 r.name, r.metric, f"{r.operator} {r.threshold}", r.severity,
                 "yes" if r.blocking else "no", "yes" if r.enabled else "no"]
                for r in rows
            ], color_col=5, colors=self._severity_colors())

    def _severity_colors(self) -> dict[str, str]:
        from pyfix360.ui.widgets import severity_colors

        return severity_colors()

    def _add_gate(self) -> None:
        from pyfix360.models import QualityGate, Severity
        from pyfix360.services import gate_service

        if not self.ctx.confirm_access("edit_quality_gates", "Add a quality gate", self):
            return
        gate = QualityGate(
            project_id=self._gate_project_id(),
            name=self.gate_name.text().strip() or "unnamed gate",
            metric=self.gate_metric_box.currentData(),
            operator=self.gate_operator.currentText(),
            threshold=self.gate_threshold.value(),
            severity=Severity(self.gate_severity.currentData()),
            blocking=self.gate_blocking.isChecked(),
        )
        with self.ctx.session() as s:
            gate_service.create_gate(s, gate, confirmed=True)
        self._reload_gates()

    def _update_gate(self) -> None:
        from pyfix360.services import gate_service

        gid = self._selected_gate_id()
        if gid is None:
            return
        if not self.ctx.confirm_access("edit_quality_gates", "Update a quality gate", self):
            return
        with self.ctx.session() as s:
            gate_service.update_gate(
                s, gid,
                name=self.gate_name.text().strip() or "unnamed gate",
                metric=self.gate_metric_box.currentData(),
                operator=self.gate_operator.currentText(),
                threshold=self.gate_threshold.value(),
                severity=self.gate_severity.currentData(),
                blocking=self.gate_blocking.isChecked(),
                confirmed=True,
            )
        self._reload_gates()

    def _delete_gate(self) -> None:
        from pyfix360.services import gate_service

        gid = self._selected_gate_id()
        if gid is None:
            return
        if not self.ctx.confirm_access("edit_quality_gates", "Delete a quality gate", self):
            return
        with self.ctx.session() as s:
            gate_service.delete_gate(s, gid, confirmed=True)
        self._reload_gates()

    def _seed_gates(self) -> None:
        from pyfix360.services import gate_service

        if not self.ctx.confirm_access("edit_quality_gates",
                                       "Install the starter quality gates", self):
            return
        with self.ctx.session() as s:
            created = gate_service.seed_defaults(s, self._gate_project_id(),
                                                 confirmed=True)
        QMessageBox.information(self, "Gates", f"Created {len(created)} gate(s).")
        self._reload_gates()

    def _evaluate_gates(self) -> None:
        from pyfix360.services import gate_service

        pid = self._gate_project_id()
        if pid is None:
            QMessageBox.information(self, "Gates",
                                    "Select an active project first.")
            return
        with self.ctx.session() as s:
            report = gate_service.evaluate(s, pid)
        fill_table(self.gate_results, [
            [r.name, "PASS" if r.passed else ("FAIL" if r.blocking else "warn"),
             r.metric, r.observed, r.expected, r.detail]
            for r in report.results
        ])

    def _selected_gate_id(self) -> int | None:
        items = self.gate_table.selectedItems()
        if not items:
            return None
        return int(self.gate_table.item(items[0].row(), 0).text())

    def _select_gate(self) -> None:
        gid = self._selected_gate_id()
        if gid is None:
            return
        from pyfix360.services import gate_service

        with self.ctx.session() as s:
            row = s.get(gate_service.QualityGateRow, gid)
            if row is None:
                return
            self.gate_name.setText(row.name)
            idx = self.gate_metric_box.findData(row.metric)
            if idx >= 0:
                self.gate_metric_box.setCurrentIndex(idx)
            self.gate_operator.setCurrentText(row.operator)
            self.gate_threshold.setValue(row.threshold)
            self.gate_severity.setCurrentText(row.severity)
            self.gate_blocking.setChecked(row.blocking)

    # ==================================================================
    # Tools & Commands
    # ==================================================================
    def _tab_tools(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        box = QGroupBox("Command overrides")
        form = QFormLayout(box)
        self.test_cmd = QLineEdit()
        self.test_cmd.setPlaceholderText("blank = auto-detect pytest/unittest")
        form.addRow("Test command", self.test_cmd)
        lay.addWidget(box)

        paths = QGroupBox("Custom tool paths")
        pl = QFormLayout(paths)
        self.tool_path_edits: dict[str, QLineEdit] = {}
        for tool in ("ruff", "bandit", "pip-audit", "pytest", "git", "node", "npm"):
            edit = QLineEdit()
            edit.setPlaceholderText("blank = resolve from PATH")
            browse = make_button("Browse…",
                                 lambda _=False, e=edit: self._browse_into(e),
                                 secondary=True)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(edit, 1)
            hl.addWidget(browse)
            self.tool_path_edits[tool] = edit
            pl.addRow(tool, holder)
        lay.addWidget(paths)

        note = QLabel(
            "Overrides are stored as plain text and are shown in the audit "
            "trail when a run uses them. PYFIX never downloads a tool.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        lay.addStretch(1)
        return scrollable(page)

    def _browse_into(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select executable", "")
        if path:
            edit.setText(path)

    # ==================================================================
    # Notifications
    # ==================================================================
    def _tab_notifications(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        box = QGroupBox("Notify me when…")
        form = QVBoxLayout(box)
        self.notify_scan = QCheckBox("a scan finishes")
        self.notify_verified = QCheckBox("a repair is verified by tests")
        form.addWidget(self.notify_scan)
        form.addWidget(self.notify_verified)
        lay.addWidget(box)

        logs = QGroupBox("Logs")
        lform = QFormLayout(logs)
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        lform.addRow("Log level", self.log_level)
        self.log_days = QSpinBox()
        self.log_days.setRange(1, 3650)
        self.log_days.setSuffix(" days")
        lform.addRow("Keep logs for", self.log_days)
        lay.addWidget(logs)
        lay.addStretch(1)
        return scrollable(page)

    # ==================================================================
    # Background
    # ==================================================================
    def _tab_background(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        box = QGroupBox("Window and notification area")
        form = QVBoxLayout(box)
        self.tray_enabled = QCheckBox("show the icon while the app runs")
        self.close_to_tray = QCheckBox(
            "closing the window hides it to the icon instead of quitting")
        form.addWidget(self.tray_enabled)
        form.addWidget(self.close_to_tray)
        form.addWidget(_note(
            "Hiding the window does not hide the process — it stays in Task "
            "Manager and in `tasklist`, where it belongs. On a desktop with no "
            "notification area, and whenever the icon is off, closing the "
            "window quits: an application you cannot reach is an application "
            "you cannot stop."))
        lay.addWidget(box)

        host = QGroupBox("Background host")
        hform = QFormLayout(host)
        self.service_poll_ms = QSpinBox()
        self.service_poll_ms.setRange(250, 600000)
        self.service_poll_ms.setSingleStep(250)
        self.service_poll_ms.setSuffix(" ms")
        self.service_poll_ms.setToolTip(
            "How often the host looks for queued work and writes the heartbeat "
            "that proves it is alive. Read when the host starts, so a running "
            "host keeps the interval it began with.")
        hform.addRow("Check the queue every", self.service_poll_ms)
        self.host_state = QLabel("")
        self.host_state.setWordWrap(True)
        self.host_state.setObjectName("muted")
        hform.addRow("Right now", self.host_state)
        hform.addRow("", make_button("Refresh the host state",
                                     self._refresh_host_state, secondary=True))
        hform.addRow("", _note(
            "The host is a separate, windowless process. Nothing in this panel "
            "starts or stops it, because registering it with Windows is an "
            "action with machine-wide consequences you should type yourself: "
            "`pyfix360 service probe` lists the modes available on this "
            "machine, `pyfix360 service install --mode task` registers one, "
            "and `pyfix360 service status` reports the one that is running."))
        lay.addWidget(host)
        lay.addStretch(1)
        self._refresh_host_state()
        return scrollable(page)

    def _refresh_host_state(self) -> None:
        """Show what another process installed, without touching it."""
        from pyfix360.services import host_service

        pid = host_service.live_pid()
        mode = host_service.installed_mode()
        if pid is not None:
            self.host_state.setText(
                f"a host is running as pid {pid}"
                + (f", registered as '{mode}'" if mode else
                   " (a detached process: nothing is registered)"))
        elif mode:
            self.host_state.setText(f"'{mode}' is registered, and no host is "
                                    f"running right now")
        else:
            self.host_state.setText("nothing is registered, so no queued job "
                                    "will run until you install the host")

    # ==================================================================
    # Data & Backup
    # ==================================================================
    def _tab_data(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        updates = QGroupBox("Application updates")
        ulay = QVBoxLayout(updates)
        self.update_label = QLabel("")
        self.update_label.setWordWrap(True)
        ulay.addWidget(self.update_label)
        self.update_auto = QCheckBox(
            "install a new version as soon as it is found")
        self.update_auto.setToolTip(
            "The update is downloaded and the installer replaces the installed "
            "copy. The application restarts when it is done.")
        ulay.addWidget(self.update_auto)
        urow = QHBoxLayout()
        self.check_update_button = make_button("Check for updates",
                                               self._check_for_updates)
        self.install_update_button = make_button("Install update",
                                                 self._install_update,
                                                 secondary=True)
        self.install_update_button.setEnabled(False)
        urow.addWidget(self.check_update_button)
        urow.addWidget(self.install_update_button)
        urow.addStretch(1)
        ulay.addLayout(urow)
        self.update_note = QLabel(
            "Checking talks to the GitHub repository that publishes the "
            "releases and sends nothing about this machine. The download is "
            "verified against the checksum the release published.")
        self.update_note.setWordWrap(True)
        self.update_note.setObjectName("muted")
        ulay.addWidget(self.update_note)
        lay.addWidget(updates)

        info = QGroupBox("Data directory")
        ilay = QVBoxLayout(info)
        self.data_label = QLabel("")
        self.data_label.setWordWrap(True)
        ilay.addWidget(self.data_label)
        row = QHBoxLayout()
        row.addWidget(make_button("Open folder", self._open_data_dir,
                                  secondary=True))
        row.addWidget(make_button("Copy path", self._copy_data_path,
                                  secondary=True))
        row.addStretch(1)
        ilay.addLayout(row)
        lay.addWidget(info)

        backup = QGroupBox("Settings backup")
        blay = QVBoxLayout(backup)
        self.settings_json = QTextEdit()
        self.settings_json.setReadOnly(True)
        self.settings_json.setMinimumHeight(160)
        blay.addWidget(self.settings_json)
        brow = QHBoxLayout()
        brow.addWidget(make_button("Export to file…", self._export_settings,
                                   secondary=True))
        brow.addWidget(make_button("Import from file…", self._import_settings,
                                   secondary=True))
        brow.addWidget(make_button("Refresh view", self._reload_json,
                                   secondary=True))
        brow.addStretch(1)
        blay.addLayout(brow)
        note = QLabel(
            "Exported settings never contain credentials — those live only in "
            "the OS credential store and are never written to disk by PYFIX.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        blay.addWidget(note)
        lay.addWidget(backup)
        lay.addStretch(1)
        return scrollable(page)

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------
    def _check_for_updates(self) -> None:
        from pyfix360.services import update_service

        self.check_update_button.setEnabled(False)
        self.install_update_button.setEnabled(False)
        self._update_info = None
        self.update_label.setText("Checking for a new version…")

        def work():
            with self.ctx.session() as s:
                return update_service.check(s, confirmed=True)

        run_async(work, on_done=self._update_checked, on_error=self._update_failed)

    def _update_checked(self, info) -> None:
        from pyfix360 import __version__

        self.check_update_button.setEnabled(True)
        if info is None:
            self.update_label.setText(
                f"You are running the newest version ({__version__}).")
            return
        self._update_info = info
        self.update_label.setText(
            f"Version {info.version} is available; you are running {info.current}.")
        self.install_update_button.setEnabled(True)

    def _update_failed(self, message: str) -> None:
        self.check_update_button.setEnabled(True)
        self.install_update_button.setEnabled(False)
        first = (message or "").splitlines()[0] if message else "unknown error"
        self.update_label.setText(f"The update check failed: {first}")

    def _install_update(self) -> None:
        from pyfix360.services import update_service

        info = self._update_info
        if info is None:
            return
        answer = QMessageBox.question(
            self, "Install update",
            f"Download and install PYFIX 360 {info.version}?\n\n"
            "The application closes while the installer replaces it, and the "
            "new version starts itself.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.install_update_button.setEnabled(False)
        self.update_label.setText(f"Downloading {info.version}…")

        def work():
            return update_service.download(info)

        run_async(work, on_done=self._update_downloaded, on_error=self._update_failed)

    def _update_downloaded(self, installer) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from pyfix360.services import update_service

        info = self._update_info
        if info is None:
            return
        self.update_label.setText(
            "Installing the update. The application restarts when it is done.")
        with self.ctx.session() as s:
            update_service.start_install(s, info, installer)
        # The installer finds the running application through the Windows
        # Restart Manager, closes it and starts it again afterwards. It has to
        # see the running copy, so the quit is delayed a little.
        QTimer.singleShot(2500, QApplication.instance().quit)

    def _open_data_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.config.data_dir)))

    def _copy_data_path(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(str(self.ctx.config.data_dir))
        self.ctx.status("Data directory path copied.")

    def _reload_json(self) -> None:
        from pyfix360.services import settings_service

        with self.ctx.session() as s:
            self.settings_json.setPlainText(settings_service.export_settings(s))

    def _export_settings(self) -> None:
        from pathlib import Path

        from pyfix360.services import settings_service

        path, _ = QFileDialog.getSaveFileName(
            self, "Export settings", str(Path.home() / "pyfix360-settings.json"),
            "JSON (*.json)")
        if not path:
            return
        with self.ctx.session() as s:
            payload = settings_service.export_settings(s)
        Path(path).write_text(payload, encoding="utf-8")
        QMessageBox.information(self, "Exported", f"Settings written to {path}")

    def _import_settings(self) -> None:
        from pathlib import Path

        from pyfix360.services import settings_service

        path, _ = QFileDialog.getOpenFileName(
            self, "Import settings", str(Path.home()), "JSON (*.json)")
        if not path:
            return
        payload = Path(path).read_text(encoding="utf-8-sig")
        with self.ctx.session() as s:
            settings_service.import_settings(s, payload)
        self.refresh()
        QMessageBox.information(self, "Imported", "Settings imported and applied.")

    # ==================================================================
    # load / save
    # ==================================================================
    def refresh(self) -> None:
        from pyfix360.services import ai_provider_service, settings_service

        with self.ctx.session() as s:
            st = settings_service.get_settings(s)
            providers = ai_provider_service.list_providers(s)
            provider_choices = [(p.id, f"#{p.id} {p.name}") for p in providers]

        self._settings = st

        # appearance
        idx = self.theme.findData(st.theme.value)
        if idx >= 0:
            self.theme.blockSignals(True)
            self.theme.setCurrentIndex(idx)
            self.theme.blockSignals(False)
        self.accent.blockSignals(True)
        self.accent.setText(st.accent_override)
        self.accent.blockSignals(False)
        self.font_size.setValue(st.font_size)
        self.font_family.setText(st.font_family)
        didx = self.density.findData(st.density.value)
        if didx >= 0:
            self.density.setCurrentIndex(didx)
        self.radius.setValue(st.corner_radius)
        self.motion.setChecked(st.motion_enabled)
        self.sidebar_width.setValue(st.sidebar_width)
        self.start_screen.setText(st.start_screen)

        # scanning
        self.include_globs.setPlainText("\n".join(st.scan_include_globs))
        self.exclude_globs.setPlainText("\n".join(st.scan_exclude_globs))
        self.skip_generated.setChecked(st.scan_skip_generated)
        self.max_file_kb.setValue(st.scan_max_file_kb)
        self.max_files.setValue(st.scan_max_files)
        self.workers.setValue(st.scan_parallel_workers)
        self.incremental.setChecked(st.scan_incremental)
        for name, cb in self.analyzer_checks.items():
            cb.setChecked(name in st.enabled_analyzers)

        # findings
        sidx = self.min_severity.findData(st.min_severity.value)
        if sidx >= 0:
            self.min_severity.setCurrentIndex(sidx)
        self.group_root.setChecked(st.group_by_root_cause)
        self.auto_ack_info.setChecked(st.auto_acknowledge_info)
        self.show_fp_rules.setChecked(st.show_false_positive_rules)

        # ai
        aidx = self.ai_behavior.findData(st.ai_behavior.value)
        if aidx >= 0:
            self.ai_behavior.setCurrentIndex(aidx)
        self.ai_default.clear()
        self.ai_default.addItem("— none —", None)
        for pid, label in provider_choices:
            self.ai_default.addItem(label, pid)
        didx2 = self.ai_default.findData(st.ai_default_provider_id)
        if didx2 >= 0:
            self.ai_default.setCurrentIndex(didx2)
        self.ai_auto_explain.setChecked(st.ai_auto_explain_findings)
        self.ai_max_chars.setValue(st.ai_max_prompt_chars)
        self.ai_repo_gate.setChecked(st.ai_allow_repo_content)
        self.redact_ai.setChecked(True)
        self.redact_ai.setEnabled(False)

        # privacy
        pidx = self.privacy_mode.findData(st.privacy_mode.value)
        if pidx >= 0:
            self.privacy_mode.setCurrentIndex(pidx)

        # repair
        self.isolate.setChecked(st.isolate_execution)
        self.timeout.setValue(st.execution_timeout_s)
        self.memory.setValue(st.memory_limit_mb)
        self.network.setChecked(st.allow_network_in_sandbox)
        self.retries.setValue(st.max_repair_retries)
        self.require_snapshot.setChecked(st.require_snapshot_before_write)
        self.dry_run.setChecked(st.dry_run_by_default)
        for key, cb in self.approvals.items():
            cb.setChecked(bool(getattr(st, key)))
        self.auto_commit.setChecked(st.auto_commit_verified)
        self.backup_days.setValue(st.backup_retention_days)
        self.gesture_window.setValue(st.autopilot_gesture_window_ms)

        # access
        for key, combo in self.access_combos.items():
            value = getattr(st, key).value
            idx2 = combo.findData(value)
            if idx2 >= 0:
                combo.setCurrentIndex(idx2)

        # tools
        self.test_cmd.setText(st.test_command_override)
        for tool, edit in self.tool_path_edits.items():
            edit.setText(st.custom_tool_paths.get(tool, ""))

        # notifications
        self.notify_scan.setChecked(st.notify_on_scan_complete)
        self.notify_verified.setChecked(st.notify_on_repair_verified)
        self.log_level.setCurrentText(st.log_level)
        self.log_days.setValue(st.log_retention_days)

        # updates
        self.update_auto.setChecked(st.update_auto_install)
        if self._update_info is None:
            from pyfix360 import __version__

            self.update_label.setText(f"You are running version {__version__}.")

        # background
        self.tray_enabled.setChecked(st.tray_enabled)
        self.close_to_tray.setChecked(st.close_hides_to_tray)
        self.service_poll_ms.setValue(st.service_poll_ms)

        self.data_label.setText(
            f"<b>{self.ctx.config.data_dir}</b><br>"
            "Database, workspaces, snapshots, reports and logs all live here.")
        self._reload_json()
        self._reload_gates()
        self._preview_theme()

    def _collect(self, st: AppSettings) -> AppSettings:
        st.theme = ThemeKey(self.theme.currentData())
        st.accent_override = self.accent.text().strip()
        st.font_size = self.font_size.value()
        st.font_family = self.font_family.text().strip()
        st.density = Density(self.density.currentData())
        st.corner_radius = self.radius.value()
        st.motion_enabled = self.motion.isChecked()
        st.sidebar_width = self.sidebar_width.value()
        st.start_screen = self.start_screen.text().strip() or "Overview"

        st.scan_include_globs = [ln.strip() for ln in
                                 self.include_globs.toPlainText().splitlines()
                                 if ln.strip()]
        st.scan_exclude_globs = [ln.strip() for ln in
                                 self.exclude_globs.toPlainText().splitlines()
                                 if ln.strip()]
        st.scan_skip_generated = self.skip_generated.isChecked()
        st.scan_max_file_kb = self.max_file_kb.value()
        st.scan_max_files = self.max_files.value()
        st.scan_parallel_workers = self.workers.value()
        st.scan_incremental = self.incremental.isChecked()
        st.enabled_analyzers = [name for name, cb in self.analyzer_checks.items()
                                if cb.isChecked()]
        if "builtin" not in st.enabled_analyzers:
            st.enabled_analyzers.insert(0, "builtin")

        st.min_severity = Severity(self.min_severity.currentData())
        st.group_by_root_cause = self.group_root.isChecked()
        st.auto_acknowledge_info = self.auto_ack_info.isChecked()
        st.show_false_positive_rules = self.show_fp_rules.isChecked()

        st.ai_behavior = AIBehavior(self.ai_behavior.currentData())
        st.ai_default_provider_id = self.ai_default.currentData()
        st.ai_auto_explain_findings = self.ai_auto_explain.isChecked()
        st.ai_max_prompt_chars = self.ai_max_chars.value()
        st.ai_allow_repo_content = self.ai_repo_gate.isChecked()
        st.redact_before_ai = True

        st.privacy_mode = PrivacyMode(self.privacy_mode.currentData())

        st.isolate_execution = self.isolate.isChecked()
        st.execution_timeout_s = self.timeout.value()
        st.memory_limit_mb = self.memory.value()
        st.allow_network_in_sandbox = self.network.isChecked()
        st.max_repair_retries = self.retries.value()
        st.require_snapshot_before_write = self.require_snapshot.isChecked()
        st.dry_run_by_default = self.dry_run.isChecked()
        for key, cb in self.approvals.items():
            setattr(st, key, cb.isChecked())
        st.auto_commit_verified = self.auto_commit.isChecked()
        st.backup_retention_days = self.backup_days.value()
        st.autopilot_gesture_window_ms = self.gesture_window.value()

        for key, combo in self.access_combos.items():
            setattr(st, key, AccessLevel(combo.currentData()))

        st.test_command_override = self.test_cmd.text().strip()
        st.custom_tool_paths = {
            tool: edit.text().strip()
            for tool, edit in self.tool_path_edits.items()
            if edit.text().strip()
        }

        st.notify_on_scan_complete = self.notify_scan.isChecked()
        st.notify_on_repair_verified = self.notify_verified.isChecked()
        st.log_level = self.log_level.currentText()
        st.log_retention_days = self.log_days.value()

        st.update_auto_install = self.update_auto.isChecked()

        st.tray_enabled = self.tray_enabled.isChecked()
        st.close_hides_to_tray = self.close_to_tray.isChecked()
        st.service_poll_ms = self.service_poll_ms.value()
        return st

    def _save(self) -> None:
        from pyfix360.services import settings_service

        with self.ctx.session() as s:
            st = settings_service.get_settings(s)
            st = self._collect(st)
            settings_service.save_settings(s, st)
        self.ctx.notify_changed()
        QMessageBox.information(
            self, "Settings",
            "Settings saved and applied. Appearance changes are already live.")
        self.refresh()

    def _reset(self) -> None:
        from pyfix360.services import settings_service

        if QMessageBox.question(
                self, "Reset settings",
                "Reset every setting to its default? Credentials and project "
                "data are not touched.") != QMessageBox.Yes:
            return
        with self.ctx.session() as s:
            settings_service.reset_settings(s)
        self.refresh()
        QMessageBox.information(self, "Settings", "Defaults restored.")
