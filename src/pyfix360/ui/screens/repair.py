"""Repair Planning Center and Patch/Diff Viewer."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import FindingRow, PatchRow, ProjectRow, RepairPlanRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async


class RepairScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Repair Planning Center",
            "Plan a repair, review its steps and risk, then execute it in an isolated "
            "workspace. A snapshot is always taken first. Unverified patches are never "
            "reported as fixed.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Finding:"))
        self.finding = QComboBox()
        self.finding.setMinimumWidth(520)
        controls.addWidget(self.finding, 1)
        controls.addWidget(make_button("Create plan", self._plan))
        controls.addWidget(make_button("Execute plan", self._execute))
        self.apply_check = QCheckBox("apply verified changes to the original project")
        controls.addWidget(self.apply_check)
        layout.addLayout(controls)
        self._apply_dry_run_default()

        split = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)

        plans_w = QWidget()
        plans_lay = QVBoxLayout(plans_w)
        plans_lay.setContentsMargins(0, 0, 0, 0)
        plans_lay.addWidget(QLabel("<b>Repair plans</b>"))
        self.plans = make_table(["id", "title", "risk", "findings"])
        self.plans.itemSelectionChanged.connect(self._show_plan)
        plans_lay.addWidget(self.plans, 1)
        top.addWidget(plans_w)

        patches_w = QWidget()
        patches_lay = QVBoxLayout(patches_w)
        patches_lay.setContentsMargins(0, 0, 0, 0)
        patches_lay.addWidget(QLabel("<b>Patches</b>"))
        self.patches = make_table(["id", "status", "file", "delta", "risk", "origin"])
        self.patches.itemSelectionChanged.connect(self._show_patch)
        patches_lay.addWidget(self.patches, 1)
        top.addWidget(patches_w)
        top.setSizes([520, 520])
        split.addWidget(top)

        detail_w = QWidget()
        detail_lay = QVBoxLayout(detail_w)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.addWidget(QLabel("<b>Plan steps / proposed diff</b>"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.NoWrap)
        detail_lay.addWidget(self.detail, 1)
        split.addWidget(detail_w)
        split.setSizes([300, 340])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def _apply_dry_run_default(self) -> None:
        """Start from the user's dry-run preference.

        "Preview repairs without applying by default" means the copy back to
        the original project starts switched off; without that preference it
        starts switched on. The user can still change the box either way.
        """
        from pyfix360.services import settings_service

        try:
            with self.ctx.session() as s:
                dry_run = settings_service.get_settings(s).dry_run_by_default
        except Exception:  # noqa: BLE001 - first run, no settings yet
            dry_run = True
        self.apply_check.setChecked(not dry_run)

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.finding.clear()
            self.plans.setRowCount(0)
            self.patches.setRowCount(0)
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            self.finding.clear()
            if project and project.last_scan_id:
                order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
                rows = s.query(FindingRow).filter_by(scan_id=project.last_scan_id).all()
                rows.sort(key=lambda f: (order.get(f.severity, 9), f.rule_id))
                for f in rows:
                    self.finding.addItem(
                        f"#{f.id} [{f.severity.upper()}] {f.rule_id} — "
                        f"{f.file_path}:{f.line_start} ({f.status})", f.id)

            plans = s.query(RepairPlanRow).filter_by(project_id=pid).order_by(
                RepairPlanRow.id.desc()).all()
            fill_table(self.plans, [
                [p.id, (p.title or "")[:60], p.estimated_risk, len(p.finding_ids or [])]
                for p in plans
            ])
            patches = s.query(PatchRow).filter_by(project_id=pid).order_by(
                PatchRow.id.desc()).all()
            fill_table(self.patches, [
                [p.id, p.status, p.file_path, f"+{p.added_lines}/-{p.removed_lines}",
                 p.risk, p.origin] for p in patches
            ], color_col=1, colors={
                "verified": "#6fcf97", "applied": "#ffd166",
                "rolled_back": "#ff8c42", "failed": "#ff4d4d",
            })

    def _selected_plan_id(self) -> int | None:
        items = self.plans.selectedItems()
        return int(self.plans.item(items[0].row(), 0).text()) if items else None

    def _plan(self) -> None:
        fid = self.finding.currentData()
        if fid is None:
            QMessageBox.warning(self, "Repair", "No finding selected.")
            return
        from pyfix360.services import repair_service

        with self.ctx.session() as s:
            plan = repair_service.plan_repair(s, fid)
            plan_id = plan.id
        self.ctx.status(f"Repair plan #{plan_id} created")
        self.ctx.notify_changed()
        self._select_plan(plan_id)

    def _select_plan(self, plan_id: int) -> None:
        for row in range(self.plans.rowCount()):
            if int(self.plans.item(row, 0).text()) == plan_id:
                self.plans.selectRow(row)
                return

    def _execute(self) -> None:
        plan_id = self._selected_plan_id()
        if plan_id is None:
            QMessageBox.warning(self, "Repair", "Select a repair plan first.")
            return
        from pyfix360.services import repair_service

        apply_original = self.apply_check.isChecked()
        if apply_original and self.ctx.access_denied(
                "apply_to_original",
                "Copying verified changes back to the ORIGINAL project", self):
            return
        # The extra approvals this repair needs, asked for once, up front.
        try:
            with self.ctx.session() as s:
                approvals = set(repair_service.required_approvals(s, plan_id))
        except Exception as exc:  # noqa: BLE001 - a bad plan must not crash the UI
            QMessageBox.warning(self, "Repair", str(exc))
            return
        if approvals and QMessageBox.question(
                self, "Approval required",
                "This change needs explicit approval for:\n\n• "
                + "\n• ".join(sorted(approvals))
                + "\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        confirm = (
            "Execute this repair?\n\n"
            "• A recoverable snapshot will be taken first.\n"
            "• Patches are applied only inside an isolated workspace copy.\n"
            "• The project's test suite will run before and after.\n"
            + ("• VERIFIED changes will be copied back to the ORIGINAL project.\n"
               if apply_original else
               "• The original project will NOT be modified.\n")
        )
        if QMessageBox.question(self, "Confirm repair", confirm) != QMessageBox.Yes:
            return
        self._run_repair(plan_id, apply_original, approvals)

    def _run_repair(self, plan_id: int, apply_original: bool,
                    approvals: set[str]) -> None:
        """Run the repair, carrying the approvals the user has given."""
        from pyfix360.services import repair_service

        self.ctx.status(f"Executing repair plan #{plan_id} …")

        def work():
            with self.ctx.session() as s:
                result = repair_service.execute_repair(
                    s, plan_id,
                    timeout_s=self.ctx.config.execution_timeout_s,
                    memory_limit_mb=self.ctx.config.memory_limit_mb,
                    max_retries=self.ctx.config.max_repair_retries,
                    apply_to_original=apply_original,
                    confirmed=True,
                    approvals=approvals,
                )
                return {
                    "status": result.status.value,
                    "verification": result.verification,
                    "message": result.message,
                }

        def done(info):
            self.ctx.status(f"Repair finished: {info['status']} "
                            f"(verification: {info['verification']})")
            if info["verification"] == "passed":
                self.ctx.notify("repair", "Repair verified",
                                f"Plan #{plan_id}: {info['message']}")
            QMessageBox.information(
                self, "Repair result",
                f"Status: {info['status']}\nVerification: {info['verification']}\n\n"
                f"{info['message']}"
            )
            self.ctx.notify_changed()

        def failed(exc):
            """A change that needs an approval nobody gave yet is not a crash."""
            if isinstance(exc, repair_service.ApprovalRequired):
                missing = ", ".join(exc.categories)
                if QMessageBox.question(
                        self, "Approval required",
                        f"This change needs explicit approval for: {missing}\n\n"
                        "Continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                ) == QMessageBox.Yes:
                    approvals.update(exc.categories)
                    self._run_repair(plan_id, apply_original, approvals)
                    return
                self.ctx.status("Repair cancelled: approval not given")
                return
            QMessageBox.critical(self, "Repair failed", str(exc))

        run_async(work, on_done=done, on_error=failed)

    def _show_plan(self) -> None:
        plan_id = self._selected_plan_id()
        if plan_id is None:
            return
        with self.ctx.session() as s:
            plan = s.get(RepairPlanRow, plan_id)
            if plan is None:
                return
            lines = [
                f"PLAN #{plan.id}: {plan.title}",
                "=" * 72,
                f"estimated risk : {plan.estimated_risk}",
                f"findings       : {', '.join(str(i) for i in (plan.finding_ids or []))}",
                "",
                "RATIONALE",
                plan.rationale or "(none)",
                "",
                "STEPS",
            ]
            for step in plan.steps or []:
                flag = "  [REQUIRES APPROVAL]" if step.get("requires_approval") else ""
                lines.append(f"  {step.get('order', '?')}. {step.get('action', '')} "
                             f"(risk: {step.get('risk', '?')}){flag}")
                lines.append(f"     {step.get('detail', '')}")
        self.detail.setPlainText("\n".join(lines))

    def _show_patch(self) -> None:
        items = self.patches.selectedItems()
        if not items:
            return
        patch_id = int(self.patches.item(items[0].row(), 0).text())
        with self.ctx.session() as s:
            p = s.get(PatchRow, patch_id)
            if p is None:
                return
            header = [
                f"PATCH #{p.id}  [{p.status}]  {p.file_path}",
                "=" * 72,
                f"risk      : {p.risk}",
                f"origin    : {p.origin}",
                f"delta     : +{p.added_lines} / -{p.removed_lines}",
                f"explanation: {p.explanation}",
                "",
                "DIFF",
                "-" * 72,
            ]
        self.detail.setPlainText("\n".join(header) + "\n" + (p.diff or "(no diff)"))


class PatchViewerScreen(QWidget):
    """Standalone patch & diff viewer with rollback."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Patch & Diff Viewer",
            "Review every proposed change line by line. A clear diff exists for every "
            "modification, and applied patches can be rolled back via their snapshot.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Filter:"))
        self.filter = QComboBox()
        self.filter.addItems(["all", "proposed", "applied", "verified", "rolled_back",
                              "failed", "rejected"])
        self.filter.currentTextChanged.connect(lambda _t: self.refresh())
        controls.addWidget(self.filter)
        controls.addWidget(make_button("Roll back selected patch", self._rollback, danger=True))
        controls.addStretch(1)
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["id", "status", "file", "delta", "risk", "origin",
                                 "explanation"])
        self.table.itemSelectionChanged.connect(self._show)
        split.addWidget(self.table)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setLineWrapMode(QPlainTextEdit.NoWrap)
        split.addWidget(self.diff)
        split.setSizes([280, 380])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.table.setRowCount(0)
            return
        with self.ctx.session() as s:
            query = s.query(PatchRow).filter_by(project_id=pid)
            mode = self.filter.currentText()
            if mode != "all":
                query = query.filter_by(status=mode)
            rows = query.order_by(PatchRow.id.desc()).all()
            fill_table(self.table, [
                [p.id, p.status, p.file_path, f"+{p.added_lines}/-{p.removed_lines}",
                 p.risk, p.origin, (p.explanation or "")[:60]] for p in rows
            ], color_col=1, colors={
                "verified": "#6fcf97", "applied": "#ffd166",
                "rolled_back": "#ff8c42", "failed": "#ff4d4d",
            })

    def _show(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        pid = int(self.table.item(items[0].row(), 0).text())
        with self.ctx.session() as s:
            p = s.get(PatchRow, pid)
            if p is None:
                return
            text = (
                f"PATCH #{p.id} [{p.status}]\n{p.file_path}\n"
                f"risk: {p.risk}  origin: {p.origin}  "
                f"delta: +{p.added_lines}/-{p.removed_lines}\n"
                f"explanation: {p.explanation}\n"
                + "=" * 72 + "\n" + (p.diff or "(no diff)")
            )
        self.diff.setPlainText(text)

    def _rollback(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        patch_id = int(self.table.item(items[0].row(), 0).text())
        if self.ctx.access_denied("rollback", "Rolling back a patch", self):
            return
        if QMessageBox.question(
            self, "Roll back patch",
            "Restore the project from the snapshot taken before this patch?\n"
            "This reverts ALL changes made since that snapshot."
        ) != QMessageBox.Yes:
            return
        from pyfix360.services import repair_service

        try:
            with self.ctx.session() as s:
                snap = repair_service.rollback_patch(s, patch_id, confirmed=True)
            QMessageBox.information(self, "Rollback", f"Restored snapshot #{snap.id}.")
        except Exception as exc:
            QMessageBox.critical(self, "Rollback failed", str(exc))
        self.ctx.notify_changed()
        self.refresh()
