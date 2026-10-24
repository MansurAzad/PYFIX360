"""Security Audit Center — security findings, secrets and dependency risk."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import DependencyRow, FindingRow, ProjectRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_table, page_header, severity_colors

_SECURITY_CATEGORIES = ("security", "dependency")


class SecurityScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Security Audit Center",
            "Security-sensitive patterns (secrets, injection surfaces, unsafe "
            "deserialization, weak crypto) and known dependency vulnerabilities.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        self.filter.addItems(["all security", "secret exposure only",
                              "dependency vulnerabilities", "high & critical only"])
        self.filter.currentTextChanged.connect(lambda _t: self.refresh())
        controls.addWidget(self.filter)
        controls.addStretch(1)
        self.summary = QLabel("")
        controls.addWidget(self.summary)
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["id", "sev", "rule", "location", "title", "status"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)

        bottom = QWidget()
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.addWidget(QLabel("<b>Detail</b>"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        bottom_lay.addWidget(self.detail, 1)
        split.addWidget(bottom)
        split.setSizes([360, 260])
        layout.addWidget(split, 1)

        layout.addWidget(QLabel("<b>Declared dependencies</b>"))
        self.deps = make_table(["name", "version", "ecosystem", "source"])
        self.deps.setMaximumHeight(180)
        layout.addWidget(self.deps)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.table.setRowCount(0)
            self.deps.setRowCount(0)
            self.summary.setText("No project selected.")
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None or not project.last_scan_id:
                self.summary.setText("No scan yet — run one in the Inspection Center.")
                return
            rows = s.query(FindingRow).filter_by(scan_id=project.last_scan_id).all()
            security = [f for f in rows if f.category in _SECURITY_CATEGORIES]
            mode = self.filter.currentText()
            if mode == "secret exposure only":
                security = [f for f in security if "secret" in f.rule_id or "key" in f.rule_id
                            or "credential" in f.rule_id or "token" in f.rule_id]
            elif mode == "dependency vulnerabilities":
                security = [f for f in security if f.category == "dependency"]
            elif mode == "high & critical only":
                security = [f for f in security if f.severity in ("high", "critical")]

            order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
            security.sort(key=lambda f: (order.get(f.severity, 9), f.file_path or ""))
            fill_table(self.table, [
                [f.id, f.severity.upper(), f.rule_id,
                 f"{f.file_path or '—'}:{f.line_start or '—'}",
                 (f.title or "")[:70], f.status] for f in security
            ], color_col=1, colors=severity_colors())

            crit = sum(1 for f in security if f.severity == "critical")
            high = sum(1 for f in security if f.severity == "high")
            self.summary.setText(
                f"<span style='color:#ff4d4d'>{crit} critical</span> · "
                f"<span style='color:#ff8c42'>{high} high</span> · "
                f"{len(security)} shown"
            )

            deps = s.query(DependencyRow).filter_by(project_id=pid).all()
            fill_table(self.deps, [
                [d.name, d.declared_version or "any", d.ecosystem, d.source_file or "—"]
                for d in deps
            ])

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
                f"#{f.id} [{f.severity.upper()}] {f.title}",
                "=" * 72,
                f"rule     : {f.rule_id}",
                f"tool     : {f.tool}",
                f"location : {f.file_path}:{f.line_start}",
                f"status   : {f.status}",
                "",
                f.description or "",
                "",
            ]
            for ev in f.evidence:
                lines.append(f"EVIDENCE {ev.file_path}:{ev.line_start}")
                if ev.snippet:
                    lines += [f"  | {ln}" for ln in ev.snippet.splitlines()[:15]]
                lines.append("")
            lines.append("NOTE: secrets are never displayed verbatim — matches are redacted.")
            self.detail.setPlainText("\n".join(lines))
