"""Website and UI Inspector — HTML/JSX/CSS audit surface."""

from __future__ import annotations

import re
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

FRONTEND_LANGS = {"html", "javascript", "typescript", "vue", "svelte", "css", "scss"}

_BUTTON_RE = re.compile(r"<(button|input)\b[^>]*>", re.IGNORECASE)
_FORM_RE = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_LINK_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']*)[\"']", re.IGNORECASE)
_IMG_NO_ALT_RE = re.compile(r"<img\b(?![^>]*\balt=)[^>]*>", re.IGNORECASE)
_ONCLICK_RE = re.compile(r"\bon(click|change|submit|input)\s*=", re.IGNORECASE)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_LABEL_FOR_RE = re.compile(r"<label\b[^>]*\bfor=", re.IGNORECASE)
_VIEWPORT_RE = re.compile(r"<meta\b[^>]*name=[\"']viewport[\"']", re.IGNORECASE)
_MEDIA_RE = re.compile(r"@media\b", re.IGNORECASE)
_JSX_BUTTON_RE = re.compile(r"<button\b|<Button\b|<Pressable\b")
_JSX_INPUT_RE = re.compile(r"<input\b|<Input\b|<input\s")
_FETCH_RE = re.compile(r"\b(fetch|axios\.\w+)\s*\(", re.IGNORECASE)


class UiInspectorScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Website & UI Inspector",
            "Audits markup, forms, buttons, links, resilient layout and client-side "
            "API calls. Static inspection of HTML/JSX/Vue/Svelte/CSS sources — the "
            "project is not rendered or executed here.",
        ))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Audit:"))
        self.mode = QComboBox()
        self.mode.addItems([
            "Interaction inventory (buttons/forms/links)",
            "Accessibility risks (alt text, labels)",
            "Responsive layout signals",
            "Client-side API calls",
        ])
        self.mode.currentTextChanged.connect(lambda _t: self.refresh())
        controls.addWidget(self.mode)
        controls.addStretch(1)
        layout.addLayout(controls)

        split = QSplitter(Qt.Vertical)
        self.table = make_table(["file", "line", "kind", "detail"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setSizes([360, 240])
        layout.addWidget(split, 1)

        self._notes = QLabel("")
        self._notes.setObjectName("muted")
        self._notes.setWordWrap(True)
        layout.addWidget(self._notes)

        self.ctx.project_changed.connect(lambda _p: self.refresh())
        self.ctx.data_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.table.setRowCount(0)
            return
        rows: list[list] = []
        limitations: list[str] = []
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid)
            if project is None:
                return
            root = Path(project.root_path)
            files = [f for f in s.query(ProjectFileRow).filter_by(project_id=pid).all()
                     if f.language in FRONTEND_LANGS]
            if not files:
                self.table.setRowCount(0)
                self._notes.setText(
                    "No frontend source files detected in this project. "
                    "Website auditing requires HTML/JSX/Vue/Svelte/CSS sources."
                )
                return
            mode = self.mode.currentText()
            for pf in files:
                try:
                    text = (root / pf.rel_path).read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                rows += self._audit(text, pf.rel_path, mode)

            # frameworks that require a real browser for deeper checks
            facts = project.facts or {}
            if any(f in facts.get("frameworks", []) for f in ("react", "vue", "nextjs", "angular")):
                limitations.append(
                    "React/Vue/Next/Angular components are inspected statically; "
                    "runtime interaction flows require Playwright (not configured)."
                )
        fill_table(self.table, rows)
        if not rows:
            self.detail.setPlainText("Nothing found for this audit view.")
        self._notes.setText(" ".join(limitations))

    def _audit(self, text: str, rel: str, mode: str) -> list[list]:
        out: list[list] = []
        lines = text.splitlines()

        def scan(pattern: re.Pattern[str], kind: str, detail_fn) -> None:
            for m in pattern.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                out.append([rel, line_no, kind, detail_fn(m)])

        if mode.startswith("Interaction"):
            scan(_BUTTON_RE, "button", lambda m: m.group(0)[:80])
            scan(_FORM_RE, "form", lambda m: m.group(0)[:80])
            scan(_LINK_RE, "link", lambda m: m.group(1) or "(empty href)")
        elif mode.startswith("Accessibility"):
            scan(_IMG_NO_ALT_RE, "img-missing-alt", lambda m: m.group(0)[:80])
            scan(_ONCLICK_RE, "inline-handler", lambda m: m.group(0))
            scan(_INPUT_RE, "input", lambda m: m.group(0)[:80])
            label_hit = _LABEL_FOR_RE.search(text)
            if label_hit:
                out.append([rel, text[: label_hit.start()].count("\n") + 1,
                            "label", "has explicit label association"])
        elif mode.startswith("Responsive"):
            has_viewport = bool(_VIEWPORT_RE.search(text))
            has_media = bool(_MEDIA_RE.search(text))
            if rel.endswith((".html", ".htm")) and not has_viewport:
                out.append([rel, 1, "missing-viewport",
                            "No <meta name=viewport> — layout will not scale on mobile"])
            if rel.endswith((".css", ".scss")) and not has_media:
                out.append([rel, 1, "no-media-queries",
                            "No @media rules — layout does not adapt to screen size"])
        else:
            for m in _FETCH_RE.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                snippet = lines[line_no - 1].strip() if line_no - 1 < len(lines) else ""
                out.append([rel, line_no, "api-call", snippet[:90]])
        return out

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rel = self.table.item(row, 0).text()
        line = int(self.table.item(row, 1).text())
        pid = self.ctx.project_id
        with self.ctx.session() as s:
            project = s.get(ProjectRow, pid) if pid else None
            if project is None:
                return
            try:
                text = (Path(project.root_path) / rel).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                return
        lines = text.splitlines()
        start, end = max(0, line - 5), min(len(lines), line + 5)
        self.detail.setPlainText(
            f"{rel} (around line {line})\n" + "-" * 60 + "\n" +
            "\n".join(f"{i + 1:5} | {lines[i]}" for i in range(start, end))
        )
