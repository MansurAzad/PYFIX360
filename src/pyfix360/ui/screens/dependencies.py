"""Dependency Manager."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import DependencyRow, FindingRow, ProjectRow, ScanRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async


class DependenciesScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Dependency Manager",
            "Declared dependencies and known vulnerabilities. Upgrading a major "
            "dependency version is a destructive change and always requires approval.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(make_button("Run vulnerability audit", self._audit))
        controls.addStretch(1)
        self.summary = QLabel("")
        controls.addWidget(self.summary)
        layout.addLayout(controls)

        self.deps = make_table(["name", "declared version", "ecosystem", "source"])
        self.deps.setMaximumHeight(240)
        layout.addWidget(self.deps)

        layout.addWidget(QLabel("<b>Dependency findings</b>"))
        self.findings = make_table(["id", "severity", "rule", "title", "status"])
        self.findings.setMinimumHeight(160)
        layout.addWidget(self.findings, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.deps.setRowCount(0)
            self.findings.setRowCount(0)
            return
        with self.ctx.session() as s:
            rows = s.query(DependencyRow).filter_by(project_id=pid).order_by(
                DependencyRow.ecosystem, DependencyRow.name).all()
            fill_table(self.deps, [
                [d.name, d.declared_version or "any", d.ecosystem, d.source_file or "—"]
                for d in rows
            ])
            project = s.get(ProjectRow, pid)
            dep_findings = []
            if project and project.last_scan_id:
                dep_findings = s.query(FindingRow).filter_by(
                    scan_id=project.last_scan_id, category="dependency").all()
            fill_table(self.findings, [
                [f.id, f.severity.upper(), f.rule_id, (f.title or "")[:80], f.status]
                for f in dep_findings
            ], color_col=1, colors={
                "critical": "#ff4d4d", "high": "#ff8c42",
                "medium": "#ffd166", "low": "#7fb3ff",
            })
            scan_skipped = []
            if project and project.last_scan_id:
                scan = s.get(ScanRow, project.last_scan_id)
                if scan:
                    scan_skipped = [t for t in (scan.tools_skipped or [])
                                    if "audit" in t.lower()]
            self.summary.setText(
                f"{len(rows)} declared · {len(dep_findings)} dependency findings"
            )
            if scan_skipped:
                self.log.appendPlainText(
                    "Vulnerability scanning unavailable: " + "; ".join(scan_skipped))

    def _audit(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            QMessageBox.warning(self, "Dependencies", "Select a project first.")
            return
        self.log.appendPlainText("Running pip-audit (requires network access)…")

        def work():
            from pathlib import Path

            from pyfix360.analysis.tool_adapters import PipAuditAdapter

            with self.ctx.session() as s:
                project = s.get(ProjectRow, pid)
                adapter = PipAuditAdapter()
                ok, reason = adapter.is_available()
                if not ok:
                    return False, reason, 0
                result = adapter.run(Path(project.root_path), timeout_s=300)
                if not result.ran:
                    return False, result.skip_reason, 0
                return True, "", len(result.findings)

        def done(res):
            ok, message, count = res
            if ok:
                self.log.appendPlainText(
                    f"Audit complete: {count} vulnerability finding(s). "
                    "Re-run the scan in the Inspection Center to persist them.")
            else:
                self.log.appendPlainText(f"Audit unavailable: {message}")

        run_async(work, on_done=done,
                  on_error=lambda m: self.log.appendPlainText("ERROR " + m))
