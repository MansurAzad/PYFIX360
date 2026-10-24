"""Quality Gates screen — project-scoped definition of 'done'.

A gate is a declarative rule ("no critical findings", "ruff must have run").
The screen shows the current gates, evaluates them against the latest scan,
and explains every failure with the findings that caused it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.models import QualityGate, Severity
from pyfix360.services.gate_service import METRICS, OPERATORS
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    Card,
    fill_table,
    make_button,
    make_table,
    page_header,
    severity_colors,
)


class GatesScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(10)
        outer.addWidget(page_header(
            "Quality Gates",
            "Declarative pass/fail rules for the active project. Gates make "
            "'done' explicit and machine-checkable, and every failure names the "
            "findings responsible.",
        ))

        self.summary = Card("Latest evaluation")
        self.summary_value = QLabel("—")
        self.summary_value.setWordWrap(True)
        self.summary.add(self.summary_value)
        outer.addWidget(self.summary)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._gates_panel())
        split.addWidget(self._editor_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _pid: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def _gates_panel(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(make_button("Evaluate now", self._evaluate))
        row.addWidget(make_button("Install starter gates", self._seed,
                                  secondary=True))
        row.addWidget(make_button("Delete selected", self._delete, danger=True))
        row.addStretch(1)
        lay.addLayout(row)

        self.results = make_table(
            ["gate", "result", "metric", "observed", "expected", "detail"])
        self.results.setMinimumHeight(200)
        lay.addWidget(self.results, 1)

        lay.addWidget(QLabel("<b>Configured gates</b>"))
        self.gates = make_table(
            ["id", "scope", "name", "metric", "condition", "severity",
             "blocking", "enabled"])
        self.gates.setMinimumHeight(180)
        self.gates.itemSelectionChanged.connect(self._select)
        lay.addWidget(self.gates, 1)
        return page

    def _editor_panel(self) -> QWidget:
        box = QGroupBox("Gate editor")
        form = QFormLayout(box)
        self.name = QLineEdit()
        form.addRow("Name", self.name)
        self.metric_box = QComboBox()
        for key, desc in METRICS.items():
            self.metric_box.addItem(f"{key} — {desc}", key)
        form.addRow("Metric", self.metric_box)
        self.operator = QComboBox()
        self.operator.addItems(OPERATORS)
        form.addRow("Operator", self.operator)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 100000)
        form.addRow("Threshold", self.threshold)
        self.severity = QComboBox()
        for sev in Severity:
            self.severity.addItem(sev.value, sev.value)
        form.addRow("Severity", self.severity)
        self.scope = QComboBox()
        self.scope.addItem("this project", "project")
        self.scope.addItem("all projects (global)", "global")
        form.addRow("Scope", self.scope)
        self.blocking = QCheckBox("blocking — failing this fails the project")
        self.blocking.setChecked(True)
        form.addRow("Blocking", self.blocking)

        row = QHBoxLayout()
        row.addWidget(make_button("Add gate", self._add))
        row.addWidget(make_button("Update selected", self._update, secondary=True))
        row.addStretch(1)
        form.addRow("", self._wrap(row))
        return box

    def _wrap(self, layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------------
    def _project_id(self) -> int | None:
        return self.ctx.project_id

    def refresh(self) -> None:
        from pyfix360.services import gate_service

        with self.ctx.session() as s:
            rows = gate_service.list_gates(s, self._project_id())
        fill_table(self.gates, [
            [r.id, "global" if r.project_id is None else f"project {r.project_id}",
             r.name, r.metric, f"{r.operator} {r.threshold}", r.severity,
             "yes" if r.blocking else "no", "yes" if r.enabled else "no"]
            for r in rows
        ], color_col=5, colors=severity_colors())
        self._evaluate()

    def _evaluate(self) -> None:
        from pyfix360.services import gate_service

        pid = self._project_id()
        if pid is None:
            self.summary_value.setText(
                "Select an active project in the toolbar to evaluate gates.")
            fill_table(self.results, [])
            return
        with self.ctx.session() as s:
            report = gate_service.evaluate(s, pid)
        line = gate_service.summary_line(report)
        self.summary_value.setText(line)
        fill_table(self.results, [
            [r.name, "PASS" if r.passed else ("FAIL" if r.blocking else "warn"),
             r.metric, r.observed, r.expected, r.detail]
            for r in report.results
        ], color_col=1,
            colors={"PASS": "#3fb950", "FAIL": "#f85149", "warn": "#d29922"})

    def _selected_id(self) -> int | None:
        items = self.gates.selectedItems()
        if not items:
            return None
        return int(self.gates.item(items[0].row(), 0).text())

    def _select(self) -> None:
        from pyfix360.services import gate_service

        gid = self._selected_id()
        if gid is None:
            return
        with self.ctx.session() as s:
            row = s.get(gate_service.QualityGateRow, gid)
            if row is None:
                return
            self.name.setText(row.name)
            idx = self.metric_box.findData(row.metric)
            if idx >= 0:
                self.metric_box.setCurrentIndex(idx)
            self.operator.setCurrentText(row.operator)
            self.threshold.setValue(row.threshold)
            self.severity.setCurrentText(row.severity)
            self.blocking.setChecked(row.blocking)
            self.scope.setCurrentIndex(1 if row.project_id is None else 0)

    def _add(self) -> None:
        from pyfix360.services import gate_service

        if not self.ctx.confirm_access("edit_quality_gates", "Add a quality gate", self):
            return
        scope = self.scope.currentData()
        gate = QualityGate(
            project_id=None if scope == "global" else self._project_id(),
            name=self.name.text().strip() or "unnamed gate",
            metric=self.metric_box.currentData(),
            operator=self.operator.currentText(),
            threshold=self.threshold.value(),
            severity=Severity(self.severity.currentData()),
            blocking=self.blocking.isChecked(),
        )
        with self.ctx.session() as s:
            gate_service.create_gate(s, gate, confirmed=True)
        self.refresh()

    def _update(self) -> None:
        from pyfix360.services import gate_service

        gid = self._selected_id()
        if gid is None:
            return
        if not self.ctx.confirm_access("edit_quality_gates", "Update a quality gate", self):
            return
        scope = self.scope.currentData()
        with self.ctx.session() as s:
            gate_service.update_gate(
                s, gid,
                project_id=None if scope == "global" else self._project_id(),
                name=self.name.text().strip() or "unnamed gate",
                metric=self.metric_box.currentData(),
                operator=self.operator.currentText(),
                threshold=self.threshold.value(),
                severity=self.severity.currentData(),
                blocking=self.blocking.isChecked(),
                confirmed=True,
            )
        self.refresh()

    def _delete(self) -> None:
        from pyfix360.services import gate_service

        gid = self._selected_id()
        if gid is None:
            return
        if not self.ctx.confirm_access("edit_quality_gates", "Delete a quality gate", self):
            return
        with self.ctx.session() as s:
            gate_service.delete_gate(s, gid, confirmed=True)
        self.refresh()

    def _seed(self) -> None:
        from pyfix360.services import gate_service

        if not self.ctx.confirm_access("edit_quality_gates",
                                       "Install the starter quality gates", self):
            return
        with self.ctx.session() as s:
            created = gate_service.seed_defaults(s, self._project_id(), confirmed=True)
        QMessageBox.information(self, "Quality Gates",
                                f"Created {len(created)} starter gate(s).")
        self.refresh()
