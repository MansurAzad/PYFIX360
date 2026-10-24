"""Project Import and Project Manager."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import ProjectRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_button, make_table, page_header
from pyfix360.ui.workers import run_async


class ProjectsScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(page_header(
            "Project Import & Manager",
            "Import a folder (analyzed in place), a ZIP archive, or clone a git URL. "
            "Imported trees are never modified by analysis.",
        ))

        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("Folder path, .zip archive, or git URL…")
        self.origin = QComboBox()
        self.origin.addItems(["auto", "folder", "zip", "git"])
        browse = make_button("Browse…", self._browse)
        imp = make_button("Import", self._import)
        row.addWidget(self.source, 1)
        row.addWidget(self.origin)
        row.addWidget(browse)
        row.addWidget(imp)
        layout.addLayout(row)

        self.hint = QLabel("No project selected.")
        self.hint.setObjectName("muted")
        layout.addWidget(self.hint)

        self.table = make_table(["id", "name", "origin", "files", "last scan", "root"])
        self.table.itemSelectionChanged.connect(self._select)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addWidget(make_button("Open selected", self._open_selected))
        actions.addWidget(make_button("Remove from manager", self._remove, danger=True))
        actions.addStretch(1)
        layout.addLayout(actions)

        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def _browse(self) -> None:
        mode = self.origin.currentText()
        if mode == "zip":
            path, _ = QFileDialog.getOpenFileName(self, "Choose ZIP archive", "",
                                                  "ZIP archives (*.zip)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Choose project folder")
        if path:
            self.source.setText(path)
            if mode == "auto":
                self.origin.setCurrentText("zip" if path.lower().endswith(".zip") else "folder")

    def _import(self) -> None:
        source = self.source.text().strip()
        if not source:
            QMessageBox.warning(self, "Import", "Enter a folder, ZIP path or git URL.")
            return
        mode = self.origin.currentText()
        self.ctx.status(f"Importing {source} …")

        def work():
            from pyfix360.models import ProjectOrigin
            from pyfix360.services import project_service

            origin = None if mode == "auto" else ProjectOrigin(mode)
            with self.ctx.session() as s:
                p = project_service.import_project(s, source, origin=origin)
                return p.id, p.name, p.file_count

        def done(result):
            pid, name, count = result
            self.ctx.status(f"Imported {name} ({count} files)")
            self.ctx.set_project(pid)
            self.ctx.notify_changed()
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda msg: QMessageBox.critical(self, "Import failed", msg))

    def _select(self) -> None:
        pid = self._selected_id()
        if pid:
            self.ctx.set_project(pid)

    def _selected_id(self) -> int | None:
        items = self.table.selectedItems()
        if not items:
            return None
        return int(self.table.item(items[0].row(), 0).text())

    def _open_selected(self) -> None:
        pid = self._selected_id()
        if pid:
            self.ctx.set_project(pid)
            self.ctx.status(f"Project #{pid} selected")

    def _remove(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        if self.ctx.access_denied("delete_project", "Removing a project", self):
            return
        answer = QMessageBox.question(
            self, "Remove project",
            "Remove this project and its scans from PYFIX 360? "
            "The project files on disk are NOT touched.",
        )
        if answer != QMessageBox.Yes:
            return
        from pyfix360.services import project_service

        with self.ctx.session() as s:
            project_service.delete_project(s, pid, confirmed=True)
        if self.ctx.project_id == pid:
            self.ctx.set_project(None)
        self.ctx.notify_changed()
        self.refresh()

    def refresh(self) -> None:
        with self.ctx.session() as s:
            rows = s.query(ProjectRow).order_by(ProjectRow.id.desc()).all()
            fill_table(self.table, [
                [p.id, p.name, p.origin, p.file_count, p.last_scan_id or "—", p.root_path]
                for p in rows
            ])
            if self.ctx.project_id:
                p = s.get(ProjectRow, self.ctx.project_id)
                if p:
                    self.hint.setText(f"Selected: #{p.id} {p.name} — {p.root_path}")
