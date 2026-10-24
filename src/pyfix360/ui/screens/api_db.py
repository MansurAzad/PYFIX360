"""API and Database Inspector — routes, models and integration surfaces."""

from __future__ import annotations

import ast
from pathlib import Path

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

from pyfix360.db.models import ProjectFileRow, ProjectRow
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import fill_table, make_table, page_header

_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "route", "websocket"}
_ORM_BASES = {"Model", "Base", "DeclarativeBase", "Document", "Table"}
_SQL_KEYWORDS = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE",
                 "ALTER TABLE", "DROP ")


class ApiDbScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "API & Database Inspector",
            "Detected HTTP routes, ORM/DB models and raw SQL usage — parsed from "
            "source. Static inspection only; nothing is executed.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("View:"))
        self.view = QComboBox()
        self.view.addItems(["HTTP routes", "Database models", "Raw SQL usage"])
        self.view.currentTextChanged.connect(lambda _t: self.refresh())
        controls.addWidget(self.view)
        controls.addStretch(1)
        controls.addWidget(QLabel("Rescan happens through the Inspection Center."))
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["file", "line", "kind", "name", "detail"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setSizes([380, 240])
        layout.addWidget(split, 1)

        self._cache: dict[int, str] = {}
        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        pid = self.ctx.project_id
        self._cache = {}
        if not pid:
            self.table.setRowCount(0)
            return
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None:
                return
            root = Path(project.root_path)
            py_files = s.query(ProjectFileRow).filter_by(
                project_id=pid, language="python").all()

            rows: list[list] = []
            view = self.view.currentText()
            for pf in py_files:
                path = root / pf.rel_path
                try:
                    source = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                self._cache[pf.id] = source
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                if view == "HTTP routes":
                    rows += self._routes(tree, pf.rel_path)
                elif view == "Database models":
                    rows += self._models(tree, pf.rel_path)
                else:
                    rows += self._sql(source, pf.rel_path)
            fill_table(self.table, rows)
            if not rows:
                self.detail.setPlainText(
                    "Nothing detected for this view. This can mean the project does "
                    "not use the pattern, or the pattern is not Python (only Python "
                    "files are parsed by this inspector)."
                )

    def _routes(self, tree: ast.AST, rel: str) -> list[list]:
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name not in _ROUTE_METHODS:
                    continue
                args = [ast.unparse(a) for a in dec.args]
                kwargs = [f"{k.arg}={ast.unparse(k.value)}" for k in dec.keywords]
                out.append([rel, node.lineno, name.upper(),
                            node.name, ", ".join(args + kwargs) or "(no path args)"])
        return out

    def _models(self, tree: ast.AST, rel: str) -> list[list]:
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(b).split(".")[-1] for b in node.bases}
            if bases & _ORM_BASES:
                fields = [n.target.id for n in node.body
                          if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
                out.append([rel, node.lineno, "model", node.name,
                            f"fields: {', '.join(fields[:12])}"])
            elif any(ast.unparse(b).endswith("Schema") for b in node.bases):
                out.append([rel, node.lineno, "schema", node.name, "pydantic/validation model"])
        return out

    def _sql(self, source: str, rel: str) -> list[list]:
        out = []
        for i, line in enumerate(source.splitlines(), start=1):
            upper = line.upper()
            for kw in _SQL_KEYWORDS:
                if kw in upper and ("=" in line or "(" in line):
                    out.append([rel, i, "sql", kw.strip(), line.strip()[:90]])
                    break
        return out

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rel = self.table.item(row, 0).text()
        line = int(self.table.item(row, 1).text())
        source = None
        with self.ctx.session() as s:
            pid = self.ctx.project_id
            project = s.get(ProjectRow, pid) if pid else None
            if project:
                try:
                    source = (Path(project.root_path) / rel).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    source = None
        if source is None:
            return
        lines = source.splitlines()
        start = max(0, line - 6)
        end = min(len(lines), line + 6)
        self.detail.setPlainText(
            f"{rel} (around line {line})\n" + "-" * 60 + "\n" +
            "\n".join(f"{i + 1:5} | {lines[i]}" for i in range(start, end))
        )
