"""Feature Completeness Center."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import FeatureCheckRow, FeatureRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async

_STATUS_COLORS = {
    "complete": "#6fcf97",
    "partial": "#ffd166",
    "missing": "#ff4d4d",
    "unknown": "#8b909b",
}


class FeatureScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Feature Completeness Center",
            "A feature matrix built from code evidence, tests, documentation and your "
            "acceptance criteria. Statuses are evidence-backed; 'complete' means a "
            "test references the feature, not that it is proven correct.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(make_button("Analyze features", self._analyze))
        controls.addStretch(1)
        self.summary = QLabel("")
        controls.addWidget(self.summary)
        layout.addLayout(controls)

        crit_label = QLabel("Acceptance criteria (one per line) — used to check "
                            "requirements against evidence:")
        crit_label.setObjectName("muted")
        layout.addWidget(crit_label)
        self.criteria = QPlainTextEdit()
        self.criteria.setPlaceholderText("e.g. users must be able to reset their password")
        self.criteria.setMaximumHeight(90)
        layout.addWidget(self.criteria)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["feature", "status", "confidence", "evidence"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setSizes([360, 240])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def _analyze(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            QMessageBox.warning(self, "Features", "Select a project first.")
            return
        criteria = [ln.strip() for ln in self.criteria.toPlainText().splitlines()
                    if ln.strip()]
        self.ctx.status("Analyzing features…")

        def work():
            from pyfix360.services import feature_service

            with self.ctx.session() as s:
                feature_service.analyze_completeness(s, pid, criteria)
                return len(feature_service.completeness_matrix(s, pid))

        def done(count):
            self.ctx.status(f"Feature analysis complete ({count} features)")
            self.ctx.notify_changed()

        run_async(work, on_done=done,
                  on_error=lambda m: QMessageBox.critical(self, "Feature analysis", m))

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.table.setRowCount(0)
            self.summary.setText("No project selected.")
            return
        with self.ctx.session() as s:
            rows = s.query(FeatureRow).filter_by(project_id=pid).order_by(
                FeatureRow.name).all()
            fill_table(self.table, [
                [f.name, f.status, f.confidence,
                 f.description + (f"  [{', '.join(f.evidence_paths or [])}]"
                                  if f.evidence_paths else "")]
                for f in rows
            ], color_col=1, colors=_STATUS_COLORS)
            complete = sum(1 for r in rows if r.status == "complete")
            partial = sum(1 for r in rows if r.status == "partial")
            missing = sum(1 for r in rows if r.status == "missing")
            self.summary.setText(
                f"<span style='color:#6fcf97'>{complete} complete</span> · "
                f"<span style='color:#ffd166'>{partial} partial</span> · "
                f"<span style='color:#ff4d4d'>{missing} missing</span>"
            )

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        name = self.table.item(items[0].row(), 0).text()
        pid = self.ctx.project_id
        with self.ctx.session() as s:
            feat = s.query(FeatureRow).filter_by(project_id=pid, name=name).first()
            if feat is None:
                return
            checks = s.query(FeatureCheckRow).filter_by(feature_id=feat.id).all()
            lines = [
                f"FEATURE: {feat.name}",
                "=" * 72,
                f"status     : {feat.status}",
                f"confidence : {feat.confidence}",
                f"description: {feat.description}",
                f"evidence   : {', '.join(feat.evidence_paths or []) or '—'}",
                "",
                "CHECKS",
            ]
            for c in checks:
                mark = "PASS" if c.passed else "FAIL"
                lines.append(f"  [{mark}] ({c.check_kind}) {c.description}")
                if c.detail:
                    lines.append(f"         {c.detail}")
            lines += [
                "",
                "LIMITATION: feature detection is heuristic. 'complete' requires only a "
                "test reference, and does not prove the behaviour is correct.",
            ]
            self.detail.setPlainText("\n".join(lines))
