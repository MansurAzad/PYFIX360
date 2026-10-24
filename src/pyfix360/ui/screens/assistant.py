"""AI Assistant — chat, explanations and redaction preview.

Everything here is explicitly user-initiated. The screen shows:

* the exact redacted prompt that would be sent, before sending it
* whether the destination is local or remote, and which provider will answer
* the full redacted history of what PYFIX actually transmitted

It never auto-applies anything. A proposal from the assistant is text; turning
it into a patch is a separate, verified repair flow.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyfix360.services import ai_chat_service
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    fill_table,
    make_button,
    make_table,
    page_header,
    scrollable,
)
from pyfix360.ui.workers import run_async


class AssistantScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(10)
        outer.addWidget(page_header(
            "AI Assistant",
            "Optional. Explains findings, drafts repairs and answers questions. "
            "Nothing is sent until you press Send, and PYFIX shows you the "
            "redacted prompt first.",
        ))

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._tab_chat(), "Chat")
        self.tabs.addTab(self._tab_explain(), "Explain")
        self.tabs.addTab(self._tab_history(), "Sent history")
        outer.addWidget(self.tabs, 1)

        self.ctx.project_changed.connect(lambda _pid: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------
    def _provider_row(self) -> tuple[QComboBox, QCheckBox]:
        combo = QComboBox()
        combo.setMinimumWidth(240)
        preview = QCheckBox("show me the redacted prompt before sending")
        preview.setChecked(True)
        return combo, preview

    def _tab_chat(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("Provider"))
        self.chat_provider = QComboBox()
        self.chat_provider.setMinimumWidth(240)
        head.addWidget(self.chat_provider, 1)
        self.chat_preview = QCheckBox("preview the redacted prompt before sending")
        self.chat_preview.setChecked(True)
        head.addWidget(self.chat_preview)
        lay.addLayout(head)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMinimumHeight(260)
        lay.addWidget(self.transcript, 1)

        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText(
            "Ask about the project, a finding, or paste a snippet to review…")
        self.chat_input.setMaximumHeight(110)
        lay.addWidget(self.chat_input)

        row = QHBoxLayout()
        row.addWidget(make_button("Send", self._send))
        row.addWidget(make_button("Preview redacted prompt",
                                  self._preview_chat, secondary=True))
        row.addWidget(make_button("Clear transcript", self._clear_transcript,
                                  secondary=True))
        row.addStretch(1)
        lay.addLayout(row)

        self.preview_box = QPlainTextEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setPlaceholderText(
            "the redacted prompt appears here so you can check it first")
        self.preview_box.setMaximumHeight(150)
        lay.addWidget(QLabel("<b>Redacted prompt preview</b>"))
        lay.addWidget(self.preview_box)
        return scrollable(page)

    def _tab_explain(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("Provider"))
        self.explain_provider = QComboBox()
        self.explain_provider.setMinimumWidth(240)
        head.addWidget(self.explain_provider, 1)
        lay.addLayout(head)

        fid_row = QHBoxLayout()
        fid_row.addWidget(QLabel("Finding id"))
        self.finding_id = QComboBox()
        self.finding_id.setMinimumWidth(360)
        fid_row.addWidget(self.finding_id, 1)
        fid_row.addWidget(make_button("Explain finding", self._explain_finding))
        fid_row.addWidget(make_button("Propose a repair diff",
                                      self._propose_repair, secondary=True))
        lay.addLayout(fid_row)

        self.explain_output = QTextEdit()
        self.explain_output.setReadOnly(True)
        self.explain_output.setMinimumHeight(300)
        lay.addWidget(self.explain_output, 1)

        warn = QLabel(
            "A proposed diff is a suggestion only. To turn one into a change, "
            "run it through Repair Planning so it is applied in an isolated "
            "workspace, tested, and only labelled verified if the tests pass.")
        warn.setWordWrap(True)
        warn.setObjectName("muted")
        lay.addWidget(warn)
        return scrollable(page)

    def _tab_history(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        intro = QLabel(
            "Only the redacted text is stored — the raw prompt never reaches "
            "the disk. The SHA-256 in the audit trail is taken over that "
            "redacted text, so nothing here can be reversed into a secret.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(make_button("Refresh", self._reload_history, secondary=True))
        row.addWidget(make_button("Clear history", self._clear_history,
                                  danger=True))
        row.addStretch(1)
        lay.addLayout(row)

        self.history_table = make_table(
            ["time", "role", "purpose", "provider", "remote", "redactions",
             "content"])
        self.history_table.setMinimumHeight(320)
        lay.addWidget(self.history_table)

        self.history_text = QPlainTextEdit()
        self.history_text.setReadOnly(True)
        self.history_text.setPlaceholderText(
            "select a row to read the full redacted turn")
        self.history_text.setMinimumHeight(160)
        lay.addWidget(self.history_text)
        return scrollable(page)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        from pyfix360.services import ai_provider_service, settings_service

        with self.ctx.session() as s:
            providers = ai_provider_service.list_providers(s)
            settings = settings_service.get_settings(s)
            rows = ai_chat_service.history(s, project_id=self.ctx.project_id,
                                           limit=300)

        for combo in (self.chat_provider, self.explain_provider):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for p in providers:
                combo.addItem(f"#{p.id} {p.name} (model {p.model or '-'})", p.id)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        # findings picker
        from pyfix360.db.models import FindingRow, ScanRow

        with self.ctx.session() as s:
            items = []
            if self.ctx.project_id:
                scan = s.query(ScanRow).filter_by(
                    project_id=self.ctx.project_id).order_by(
                    ScanRow.id.desc()).first()
                if scan:
                    for f in s.query(FindingRow).filter_by(
                            scan_id=scan.id).order_by(FindingRow.id).all():
                        items.append((f.id, f"[{f.severity}] {f.rule_id} — "
                                           f"{f.title} ({f.file_path or '-'})"))
        self.finding_id.clear()
        for fid, label in items:
            self.finding_id.addItem(f"#{fid} {label}", fid)

        if settings.ai_behavior.value == "off":
            self.status.setText(
                "<b>AI is off.</b> Enable it in Settings → AI to use this screen. "
                "All other features work without it.")
        elif not providers:
            self.status.setText(
                "<b>No providers configured.</b> Add one on the AI Providers "
                "screen — a local Ollama runtime needs no key and works offline.")
        else:
            enabled = [p for p in providers if p.enabled]
            self.status.setText(
                f"{len(providers)} provider(s) configured, "
                f"{len(enabled)} enabled. "
                "Free and open models are listed in the provider catalogue.")

        self._reload_history(rows)

    def _reload_history(self, rows=None) -> None:
        if rows is None:
            with self.ctx.session() as s:
                rows = ai_chat_service.history(s, project_id=self.ctx.project_id,
                                               limit=300)
        fill_table(self.history_table, [
            [str(t.created_at)[:19], t.role, t.purpose,
             f"#{t.provider_id}" if t.provider_id else "-",
             "yes" if t.remote else "no", t.redactions_applied,
             (t.content or "").replace("\n", " ")[:120]]
            for t in rows
        ])
        self._history_rows = rows
        self.history_table.itemSelectionChanged.connect(self._show_turn)

    def _show_turn(self) -> None:
        items = self.history_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rows = getattr(self, "_history_rows", [])
        if 0 <= row < len(rows):
            self.history_text.setPlainText(rows[row].content)

    # ------------------------------------------------------------------
    def _is_remote(self, provider_id) -> bool:
        """True when the selected provider would send the prompt off this machine."""
        if provider_id is None:
            return False
        from pyfix360.ai import gateway
        from pyfix360.db.models import AIProviderRow

        with self.ctx.session() as s:
            provider = s.get(AIProviderRow, provider_id)
            if provider is None:
                return False
            return gateway.provider_destination(provider)[1]

    def _preview_chat(self) -> None:

        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        with self.ctx.session() as s:
            self.preview_box.setPlainText(
                ai_chat_service.preview_prompt(s, text))

    def _send(self) -> None:

        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        provider_id = self.chat_provider.currentData()
        confirmed = False
        if self.chat_preview.isChecked():
            with self.ctx.session() as s:
                preview = ai_chat_service.preview_prompt(s, text)
            answer = QMessageBox.question(
                self, "Send this?",
                "This will be sent (redacted) to the selected provider:\n\n"
                + preview[:1200]
                + ("\n\n…" if len(preview) > 1200 else ""))
            if answer != QMessageBox.Yes:
                return
            confirmed = True
        if self._is_remote(provider_id):
            if self.ctx.access_denied("remote_ai",
                                      "Sending this prompt to a remote provider", self):
                return
            if not confirmed and not self.ctx.confirm_access(
                    "remote_ai", "Send this prompt to a remote AI provider", self):
                return
            confirmed = True

        self.transcript.append(f"<b>You:</b> {text}")
        self.chat_input.clear()
        self.ctx.status("Waiting for the assistant …")
        pid = self.ctx.project_id

        def work():
            with self.ctx.session() as s:
                return ai_chat_service.chat(
                    s, text, project_id=pid, provider_id=provider_id,
                    confirmed=confirmed)

        def done(reply):
            self.transcript.append(f"<b>Assistant:</b> {reply}")
            self.transcript.append("")
            self.ctx.status("Reply received.")
            self.refresh()

        def failed(message):
            self.transcript.append(
                f"<span style='color:#f85149'><b>Refused / failed:</b> {message}"
                "</span>")
            self.ctx.status(message.splitlines()[0][:160])

        run_async(work, on_done=done, on_error=failed)

    def _clear_transcript(self) -> None:
        self.transcript.clear()

    def _explain_finding(self) -> None:
        fid = self.finding_id.currentData()
        if fid is None:
            QMessageBox.information(self, "Explain", "Select a finding first.")
            return
        provider_id = self.explain_provider.currentData()
        if self._is_remote(provider_id):
            if self.ctx.access_denied("remote_ai",
                                      "Sending the finding to a remote provider", self):
                return
            if not self.ctx.confirm_access(
                    "remote_ai", "Send this finding to a remote AI provider", self):
                return
        self.explain_output.setPlainText("Asking the provider …")

        def work():
            with self.ctx.session() as s:
                return ai_chat_service.explain_finding(
                    s, fid, provider_id=provider_id, confirmed=True)

        def done(text):
            self.explain_output.setPlainText(text)
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda m: self.explain_output.setPlainText(
                      "Refused or failed:\n\n" + m))

    def _propose_repair(self) -> None:
        fid = self.finding_id.currentData()
        if fid is None:
            return
        provider_id = self.explain_provider.currentData()
        if self._is_remote(provider_id):
            if self.ctx.access_denied("remote_ai",
                                      "Sending the finding to a remote provider", self):
                return
            if not self.ctx.confirm_access(
                    "remote_ai", "Send this finding to a remote AI provider", self):
                return
        self.explain_output.setPlainText("Asking for a diff proposal …")

        def work():
            with self.ctx.session() as s:
                return ai_chat_service.propose_repair(
                    s, fid, provider_id=provider_id, confirmed=True)

        def done(text):
            self.explain_output.setPlainText(
                text + "\n\n---\nThis is a suggestion. Use Repair Planning to "
                       "apply and verify it.")
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda m: self.explain_output.setPlainText(
                      "Refused or failed:\n\n" + m))

    def _clear_history(self) -> None:
        if QMessageBox.question(
                self, "Clear history",
                "Delete the stored redacted AI history?") != QMessageBox.Yes:
            return
        with self.ctx.session() as s:
            count = ai_chat_service.clear_history(s, self.ctx.project_id)
        self.refresh()
        self.ctx.status(f"Removed {count} stored turn(s).")
