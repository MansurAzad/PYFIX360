"""Reports and Export Center."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async


class ReportsScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Reports & Export Center",
            "Audit reports include findings with evidence, repair history, test "
            "results, the audit trail, and an explicit statement of what could "
            "NOT be checked.",
        ))

        controls = QHBoxLayout()
        self.format = QComboBox()
        self.format.addItems(["markdown", "json", "html"])
        controls.addWidget(QLabel("Format:"))
        controls.addWidget(self.format)
        controls.addWidget(make_button("Generate report", self._generate))
        controls.addWidget(make_button("Export to file…", self._export))
        controls.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        controls.addWidget(self.status)
        layout.addLayout(controls)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.preview, 2)

        layout.addWidget(QLabel("<b>Report history</b>"))
        self.history = make_table(["file", "size", "modified"])
        self.history.setMaximumHeight(160)
        layout.addWidget(self.history)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        reports_dir = self.ctx.config.reports_dir
        rows = []
        for path in sorted(reports_dir.glob("*"), key=lambda p: p.stat().st_mtime
                           if p.exists() else 0, reverse=True)[:50]:
            try:
                stat = path.stat()
                rows.append([path.name, f"{stat.st_size:,}", str(path)[:100]])
            except OSError:
                continue
        fill_table(self.history, [[r[0], r[1], r[2]] for r in rows])

    def _generate(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            QMessageBox.warning(self, "Report", "Select a project first.")
            return
        if not self.ctx.confirm_access("export_reports", "Generate a report", self):
            return
        fmt = self.format.currentText()
        self.ctx.status("Generating report…")

        def work():
            from pyfix360.services import report_service

            with self.ctx.session() as s:
                path = report_service.generate_report(s, pid, fmt=fmt, confirmed=True)
                return str(path), path.read_text(encoding="utf-8")

        def done(res):
            path, text = res
            self.status.setText(f"Written: {path}")
            self._full_text = text
            self.preview.setPlainText(text[:200_000])
            self.ctx.status(f"Report generated: {path}")
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda m: QMessageBox.critical(self, "Report failed", m))

    def _export(self) -> None:
        text = getattr(self, "_full_text", "") or self.preview.toPlainText()
        if not text:
            QMessageBox.information(self, "Export", "Generate a report first.")
            return
        if not self.ctx.confirm_access("export_reports", "Export the report to a file",
                                       self):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report", "",
                                              "Markdown (*.md);;JSON (*.json);;HTML (*.html)")
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.status.setText(f"Exported: {path}")
