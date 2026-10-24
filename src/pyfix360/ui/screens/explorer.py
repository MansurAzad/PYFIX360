"""Project Explorer — file tree, facts and module inventory."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import ModuleRow, ProjectFileRow, ProjectRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import page_header


class ExplorerScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(page_header(
            "Project Explorer",
            "Registered files, detected languages and the parsed symbol inventory.",
        ))

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["file / symbol", "language", "size", "flags"])
        self.tree.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tree)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        self.facts = QLabel("Select a project.")
        self.facts.setWordWrap(True)
        self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_lay.addWidget(self.facts)
        right_lay.addWidget(QLabel("<b>Source preview</b>"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        right_lay.addWidget(self.preview, 1)
        split.addWidget(right)
        split.setSizes([560, 520])
        layout.addWidget(split, 1)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        pid = self.ctx.project_id
        if not pid:
            self.facts.setText("No project selected. Import or select one in Project Manager.")
            self.preview.clear()
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None:
                return
            facts = project.facts or {}
            self.facts.setText(
                f"<b>{project.name}</b><br>"
                f"<span style='color:#8b909b'>root:</span> {project.root_path}<br>"
                f"<span style='color:#8b909b'>languages:</span> "
                f"{', '.join(facts.get('languages', [])) or '—'}<br>"
                f"<span style='color:#8b909b'>frameworks:</span> "
                f"{', '.join(facts.get('frameworks', [])) or '—'}<br>"
                f"<span style='color:#8b909b'>build systems:</span> "
                f"{', '.join(facts.get('build_systems', [])) or '—'}<br>"
                f"<span style='color:#8b909b'>entry points:</span> "
                f"{', '.join(facts.get('entry_points', [])) or '—'}<br>"
                f"<span style='color:#8b909b'>test harnesses:</span> "
                f"{', '.join(facts.get('test_harnesses', [])) or '—'}"
            )
            files = s.query(ProjectFileRow).filter_by(project_id=pid).order_by(
                ProjectFileRow.rel_path).all()
            modules = s.query(ModuleRow).filter_by(project_id=pid).all()
            mods_by_file: dict[str, list[ModuleRow]] = {}
            for m in modules:
                mods_by_file.setdefault(m.rel_path, []).append(m)

            for pf in files:
                flags = []
                if pf.is_test:
                    flags.append("test")
                if pf.is_generated:
                    flags.append("generated")
                item = QTreeWidgetItem([
                    pf.rel_path, pf.language or "—",
                    f"{pf.size_bytes:,}", ",".join(flags),
                ])
                item.setData(0, Qt.UserRole, pf.rel_path)
                for m in sorted(mods_by_file.get(pf.rel_path, []),
                                key=lambda x: x.line_start or 0):
                    child = QTreeWidgetItem([
                        f"  {m.kind} {m.name}",
                        f"L{m.line_start or '?'}", "", "",
                    ])
                    item.addChild(child)
                self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        while item.parent() is not None:
            item = item.parent()
        rel = item.data(0, Qt.UserRole)
        pid = self.ctx.project_id
        if not rel or not pid:
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None:
                return
            path = project.root_path
        try:
            from pathlib import Path

            text = (Path(path) / rel).read_text(encoding="utf-8-sig", errors="replace")
            if len(text) > 60_000:
                text = text[:60_000] + "\n… (truncated)"
            self.preview.setPlainText(text)
        except OSError as exc:
            self.preview.setPlainText(f"Could not read file: {exc}")
