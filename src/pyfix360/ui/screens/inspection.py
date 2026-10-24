"""Inspection Center — run scans and browse evidence-backed findings."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import FindingRow, ProjectRow, ScanRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    fill_table,
    make_button,
    make_table,
    page_header,
    severity_colors,
)
from pyfix360.ui.workers import run_async


class InspectionScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Inspection Center",
            "Static analysis with the built-in Python analyzer plus any external "
            "tools available on this machine. Unavailable tools are reported, "
            "never silently skipped.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Tools:"))
        self.tools = QComboBox()
        self.tools.addItems(["builtin + available", "builtin only"])
        controls.addWidget(self.tools)
        self.incremental = QComboBox()
        self.incremental.addItems(["incremental", "full rescan"])
        controls.addWidget(self.incremental)
        self.run_btn = make_button("Run scan", self._run_scan)
        controls.addWidget(self.run_btn)
        controls.addStretch(1)
        controls.addWidget(QLabel("Severity filter:"))
        self.sev = QComboBox()
        self.sev.addItems(["all", "critical", "high", "medium", "low", "info"])
        self.sev.currentTextChanged.connect(lambda _t: self._load_findings())
        controls.addWidget(self.sev)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["all", "detected", "planned", "fix_proposed",
                                     "fix_applied", "verified", "failed", "rolled_back"])
        self.status_filter.currentTextChanged.connect(lambda _t: self._load_findings())
        controls.addWidget(self.status_filter)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.scan_hint = QLabel("")
        self.scan_hint.setObjectName("muted")
        self.scan_hint.setWordWrap(True)
        layout.addWidget(self.scan_hint)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["id", "sev", "rule", "category", "confidence",
                                 "status", "location", "title"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)

        detail = QWidget()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.addWidget(QLabel("<b>Finding detail & evidence</b>"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        detail_lay.addWidget(self.detail, 1)
        split.addWidget(detail)
        split.setSizes([420, 300])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self._reload_all())
        self.ctx.data_changed.connect(self._reload_all)
        self._reload_all()

    # ------------------------------------------------------------------
    def _reload_all(self) -> None:
        self._load_scan_info()
        self._load_findings()

    def _load_scan_info(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.scan_hint.setText("No project selected.")
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None or not project.last_scan_id:
                self.scan_hint.setText("No scan yet — click 'Run scan'.")
                return
            scan = s.get(ScanRow, project.last_scan_id)
            if scan is None:
                return
            parts = [
                f"Scan #{scan.id} — status: <b>{scan.status}</b>, "
                f"{scan.findings_count} findings, {scan.files_scanned} files scanned, "
                f"{scan.files_skipped} skipped.",
                f"Tools ran: {', '.join(scan.tools_ran) or 'builtin only'}.",
            ]
            if scan.tools_skipped:
                parts.append(
                    "<span style='color:#ff8c42'>NOT available (findings are incomplete): "
                    + "; ".join(scan.tools_skipped) + "</span>"
                )
            if scan.message:
                parts.append(f"<span style='color:#8b909b'>{scan.message}</span>")
            self.scan_hint.setText("<br>".join(parts))

    def _load_findings(self) -> None:
        pid = self.ctx.project_id
        self.table.setRowCount(0)
        self.detail.clear()
        if not pid:
            return
        from pyfix360.services import findings_service

        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None or not project.last_scan_id:
                return
            sev = self.sev.currentText()
            status = self.status_filter.currentText()
            rows = findings_service.visible_findings(
                s, pid,
                severity=None if sev == "all" else sev,
                status=None if status == "all" else status,
            )
            grouping = findings_service.group_by_root_cause(rows) \
                if self._grouping_on(s) else []
        if grouping:
            fill_table(self.table, [
                [g.head.id, g.severity.upper(), g.head.rule_id, g.head.category,
                 g.head.confidence, g.head.status,
                 f"{g.head.file_path or '—'}:{g.head.line_start or '—'}",
                 f"{g.head.title} (+{g.count - 1} more in this root cause)"]
                for g in grouping
            ], color_col=1, colors=severity_colors())
        else:
            fill_table(self.table, [
                [f.id, f.severity.upper(), f.rule_id, f.category, f.confidence,
                 f.status, f"{f.file_path or '—'}:{f.line_start or '—'}",
                 (f.title or "")[:70]]
                for f in rows
            ], color_col=1, colors=severity_colors())

    def _grouping_on(self, session) -> bool:
        from pyfix360.services import settings_service

        try:
            return settings_service.get_settings(session).group_by_root_cause
        except Exception:  # noqa: BLE001 - first run, no settings yet
            return True

    # ------------------------------------------------------------------
    def _run_scan(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            QMessageBox.warning(self, "Scan", "Select or import a project first.")
            return
        tools = None if self.tools.currentIndex() == 0 else ["builtin"]
        incremental = self.incremental.currentIndex() == 0
        self.run_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.ctx.status("Scanning…")

        def work():
            from pyfix360.services import scan_service

            with self.ctx.session() as s:
                scan = scan_service.create_scan(s, pid, tools)
                scan_service.run_scan(s, scan.id, incremental=incremental,
                                      timeout_s=self.ctx.config.execution_timeout_s)
                return scan.id

        def done(scan_id):
            self.run_btn.setEnabled(True)
            self.progress.setValue(100)
            self.progress.setVisible(False)
            count = self._finding_count(scan_id)
            self.ctx.status(f"Scan #{scan_id} complete — {count} finding(s)")
            self.ctx.notify("scan", "Scan finished",
                            f"Scan #{scan_id}: {count} finding(s).")
            self.ctx.notify_changed()

        def fail(msg):
            self.run_btn.setEnabled(True)
            self.progress.setVisible(False)
            QMessageBox.critical(self, "Scan failed", msg)

        run_async(work, on_done=done, on_error=fail)

    def _finding_count(self, scan_id: int) -> int:
        with self.ctx.session() as s:
            scan = s.get(ScanRow, scan_id)
            return scan.findings_count if scan is not None else 0

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        fid = int(self.table.item(items[0].row(), 0).text())
        with self.ctx.session() as s:
            f = s.get(FindingRow, fid)
            if f is None:
                return
            lines = [
                f"#{f.id}  [{f.severity.upper()}]  {f.title}",
                "=" * 72,
                f"rule        : {f.rule_id}",
                f"tool        : {f.tool}",
                f"category    : {f.category}",
                f"confidence  : {f.confidence}",
                f"status      : {f.status}",
                f"occurrences : {f.dedup_count}",
                f"root cause  : {f.root_cause_group}",
                f"location    : {f.file_path}:{f.line_start}-{f.line_end}",
                f"symbol      : {f.symbol or '<module>'}",
                "",
                "DESCRIPTION",
                f.description or "(none)",
                "",
            ]
            for ev in f.evidence:
                lines.append(f"EVIDENCE ({ev.kind}) {ev.file_path}:{ev.line_start}-{ev.line_end}")
                if ev.excerpt:
                    lines.append(f"  why: {ev.excerpt}")
                if ev.snippet:
                    lines.append("  code:")
                    lines += [f"    | {ln}" for ln in ev.snippet.splitlines()[:20]]
                lines.append("")
            self.detail.setPlainText("\n".join(lines))
        if self._auto_explain_on():
            self._auto_explain(fid)

    def _auto_explain_on(self) -> bool:
        from pyfix360.services import settings_service

        try:
            with self.ctx.session() as s:
                return settings_service.get_settings(s).ai_auto_explain_findings
        except Exception:  # noqa: BLE001 - first run, no settings yet
            return False

    def _auto_explain(self, finding_id: int) -> None:
        """Ask the provider to explain the selected finding, automatically.

        The provider still has to be usable and the privacy policy still has
        to allow it: a refusal is reported in the detail pane instead of
        being hidden.
        """
        from pyfix360.services import ai_chat_service

        self.detail.appendPlainText(
            "\n--- AI explanation (requested automatically) ---")

        def work():
            with self.ctx.session() as s:
                return ai_chat_service.explain_finding(s, finding_id)

        def done(text):
            self.detail.appendPlainText(text)

        def fail(msg):
            self.detail.appendPlainText(f"[no explanation: {msg}]")

        run_async(work, on_done=done, on_error=fail)
