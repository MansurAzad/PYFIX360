"""Testing and Verification Center."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import PatchRow, ProjectRow, TestRunRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async

_OUTCOME_COLORS = {
    "passed": "#6fcf97", "failed": "#ff4d4d", "error": "#ff8c42",
    "skipped": "#ffd166", "not_run": "#8b909b",
}


class TestingScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Testing & Verification Center",
            "Run the project's tests in an isolated copy and compare against the "
            "pre-repair baseline. An untested patch is never labelled verified.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Workspace:"))
        self.workspace = QComboBox()
        self.workspace.setMinimumWidth(420)
        controls.addWidget(self.workspace, 1)
        controls.addWidget(make_button("Detect harness", self._detect))
        controls.addWidget(make_button("Run tests", self._run))
        controls.addStretch(1)
        layout.addLayout(controls)

        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._build_runs())
        split.addWidget(self._build_output())
        split.setSizes([300, 340])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def _build_runs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Test runs</b>"))
        self.runs = make_table(["id", "phase", "outcome", "passed", "failed",
                                "errors", "skipped", "duration", "command"])
        self.runs.itemSelectionChanged.connect(self._show_run)
        lay.addWidget(self.runs, 1)
        return w

    def _build_output(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Output</b>"))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.output, 1)
        self.limits = QLabel("")
        self.limits.setObjectName("muted")
        self.limits.setWordWrap(True)
        lay.addWidget(self.limits)
        return w

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        pid = self.ctx.project_id
        self.workspace.clear()
        if not pid:
            self.runs.setRowCount(0)
            self.hint.setText("No project selected.")
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None:
                return
            self.workspace.addItem(f"project root (read-only): {project.root_path}",
                                   str(project.root_path))
            for ws in sorted(self.ctx.config.workspace_dir.glob(f"proj{pid}_plan*")):
                if ws.is_dir():
                    self.workspace.addItem(f"isolated workspace: {ws.name}", str(ws))
            runs = s.query(TestRunRow).filter_by(project_id=pid).order_by(
                TestRunRow.id.desc()).all()
            fill_table(self.runs, [
                [r.id, r.phase, r.outcome, r.passed, r.failed, r.errors, r.skipped,
                 f"{r.duration_s:.2f}s", r.command or "(no harness)"]
                for r in runs
            ], color_col=2, colors=_OUTCOME_COLORS)
            self.limits.setText(
                "Sandbox: timeouts enforced on every run; network isolation is NOT "
                "enforced at OS level. Run untrusted projects in a container or VM."
            )

    def _detect(self) -> None:
        from pyfix360.services import test_runner

        path = self.workspace.currentData()
        if not path:
            return
        cmd = test_runner.detect_test_command(Path(path))
        if cmd:
            self.hint.setText(f"Detected harness: <b>{' '.join(cmd)}</b>")
        else:
            self.hint.setText(
                "<span style='color:#ff8c42'>No test harness detected in this "
                "workspace. Tests cannot run, so no patch can be verified here.</span>"
            )

    def _run(self) -> None:
        pid = self.ctx.project_id
        path = self.workspace.currentData()
        if not pid or not path:
            QMessageBox.warning(self, "Tests", "Select a project and workspace first.")
            return
        if not self.ctx.confirm_access("run_tests", "Run the project's test suite",
                                        self):
            return
        self.ctx.status("Running tests…")

        def work():
            from pyfix360.services import test_runner

            with self.ctx.session() as s:
                run = test_runner.run_tests(
                    s, pid, Path(path), phase="adhoc",
                    timeout_s=self.ctx.config.execution_timeout_s,
                    memory_limit_mb=self.ctx.config.memory_limit_mb,
                    confirmed=True,
                )
                return run.id, run.outcome, run.passed, run.failed

        def done(res):
            rid, outcome, passed, failed = res
            self.ctx.status(f"Test run #{rid}: {outcome} (passed={passed} failed={failed})")
            self.ctx.notify_changed()

        run_async(work, on_done=done,
                  on_error=lambda m: QMessageBox.critical(self, "Test run failed", m))

    def _show_run(self) -> None:
        items = self.runs.selectedItems()
        if not items:
            return
        rid = int(self.runs.item(items[0].row(), 0).text())
        with self.ctx.session() as s:
            run = s.get(TestRunRow, rid)
            if run is None:
                return
            lines = [
                f"RUN #{run.id}  phase={run.phase}  outcome={run.outcome}",
                f"command : {run.command or '(no harness detected)'}",
                f"counts  : passed={run.passed} failed={run.failed} "
                f"errors={run.errors} skipped={run.skipped}",
                f"duration: {run.duration_s:.2f}s",
                "=" * 72,
                run.log_excerpt or "(no output)",
            ]
            if run.results:
                lines += ["", "CASES"]
                lines += [f"  [{r.outcome}] {r.node_id} {r.message or ''}"
                          for r in run.results[:200]]
        self.output.setPlainText("\n".join(lines))


class RollbackScreen(QWidget):
    """Repair History and Rollback Center."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Repair History & Rollback Center",
            "Complete repair history with snapshots. Every applied change can be "
            "reverted to the exact pre-repair state.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(make_button("Roll back selected patch", self._rollback, danger=True))
        controls.addWidget(make_button("Refresh", self.refresh))
        controls.addStretch(1)
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._build_patches())
        split.addWidget(self._build_snapshots())
        split.setSizes([260, 260])
        layout.addWidget(split, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def _build_patches(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Repairs</b>"))
        self.patches = make_table(["id", "status", "file", "risk", "origin", "explanation"])
        lay.addWidget(self.patches, 1)
        return w

    def _build_snapshots(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Snapshots</b>"))
        self.snapshots = make_table(["id", "label", "kind", "created", "restored", "path"])
        lay.addWidget(self.snapshots, 1)
        return w

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.patches.setRowCount(0)
            self.snapshots.setRowCount(0)
            return
        from pyfix360.services import snapshot_service

        with self.ctx.session() as s:
            rows = s.query(PatchRow).filter_by(project_id=pid).order_by(
                PatchRow.id.desc()).all()
            fill_table(self.patches, [
                [p.id, p.status, p.file_path, p.risk, p.origin,
                 (p.explanation or "")[:70]] for p in rows
            ], color_col=1, colors={
                "verified": "#6fcf97", "applied": "#ffd166",
                "rolled_back": "#ff8c42", "failed": "#ff4d4d",
            })
            snaps = snapshot_service.list_snapshots(s, pid)
            fill_table(self.snapshots, [
                [sn.id, sn.label, sn.kind, str(sn.created_at)[:19],
                 str(sn.restored_at)[:19] if sn.restored_at else "—", sn.path]
                for sn in snaps
            ])

    def _rollback(self) -> None:
        items = self.patches.selectedItems()
        if not items:
            QMessageBox.information(self, "Rollback", "Select a patch to roll back.")
            return
        patch_id = int(self.patches.item(items[0].row(), 0).text())
        if self.ctx.access_denied("rollback", "Rolling back a patch", self):
            return
        if QMessageBox.question(
            self, "Roll back",
            f"Restore the snapshot taken before patch #{patch_id}?\n"
            "All later changes to the project will be reverted."
        ) != QMessageBox.Yes:
            return
        from pyfix360.services import repair_service

        try:
            with self.ctx.session() as s:
                snap = repair_service.rollback_patch(s, patch_id, confirmed=True)
            self.log.appendPlainText(
                f"Rolled back patch #{patch_id} using snapshot #{snap.id}.")
        except Exception as exc:
            QMessageBox.critical(self, "Rollback failed", str(exc))
            self.log.appendPlainText(f"Rollback FAILED for patch #{patch_id}: {exc}")
        self.ctx.notify_changed()
        self.refresh()
