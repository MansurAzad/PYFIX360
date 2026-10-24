"""Integrations & CLIs screen — knowledge base, project detection, playbooks.

Three things live here:

* **Catalog** — the tool reference: what each CLI is, how it is installed,
  which commands it takes and what usually goes wrong.
* **Project detection** — which integrations the current project already
  uses. Static and read-only: file markers only, no command is ever run.
* **Platform Playbooks** — the operating knowledge for the AI app builders
  (Lovable, Trickle, Replit, Emergent, v0, Builder.io, Google AI Studio):
  the prompt patterns that work, the command dialect each one understands,
  the questions it asks back, and the situations that come up mid-build.
  Every playbook says when it was last reviewed and that it has not been
  driven live from inside this build.

The **Operate a platform** tab is where a prompt is actually sent, through
the platform's REST API or through a signed-in browser session. Both paths
go through the privacy and access-control gates in the service layer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pyfix360.services import integration_service
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    fill_table,
    make_button,
    make_table,
    page_header,
)
from pyfix360.ui.workers import run_async


class IntegrationsScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(page_header(
            "Integrations & CLIs",
            "Deep reference for external tools, CLIs and services (GitHub, "
            "Supabase, Vercel, Docker, AWS, ...), plus operating playbooks for "
            "the AI app builders. Knowledge only — nothing is executed or "
            "installed automatically.",
        ))

        tabs = QTabWidget()
        tabs.addTab(self._build_catalog_tab(), "Catalog")
        tabs.addTab(self._build_detection_tab(), "Project detection")
        tabs.addTab(self._build_playbooks_tab(), "Platform Playbooks")
        tabs.addTab(self._build_operate_tab(), "Operate a platform")
        layout.addWidget(tabs, 1)

    # ---------------------------------------------------------------- catalog
    def _build_catalog_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Category:"))
        self.cat = QComboBox()
        cats = ["all"] + list(integration_service.list_integrations() and
                              sorted({i.category for i in integration_service.list_integrations()}))
        self.cat.addItems(cats)
        self.cat.currentTextChanged.connect(lambda _t: self._refresh_catalog())
        controls.addWidget(self.cat)
        controls.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("github, supabase, deploy, webhook, ...")
        self.search.textChanged.connect(lambda _t: self._refresh_catalog())
        controls.addWidget(self.search, 1)
        v.addLayout(controls)

        split = QSplitter(Qt.Horizontal)
        self.table = make_table(["key", "label", "category", "cli"])
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setSizes([460, 520])
        v.addWidget(split, 1)
        self._refresh_catalog()
        return w

    def _refresh_catalog(self) -> None:
        cat = self.cat.currentText() if hasattr(self, "cat") else "all"
        query = self.search.text().strip() if hasattr(self, "search") else ""
        if query:
            items = integration_service.search(query)
        elif cat and cat != "all":
            items = integration_service.list_integrations(cat)
        else:
            items = integration_service.list_integrations()
        rows = [[it.key, it.label, it.category, it.cli or "-"] for it in items]
        fill_table(self.table, rows)
        if not rows:
            self.detail.setPlainText("No integrations match.")

    def _show_detail(self) -> None:
        sel = self.table.selectedItems()
        if not sel:
            return
        key = sel[0].text()
        it = integration_service.get_integration(key)
        if it is None:
            return
        lines = []
        lines.append(f"# {it.label}  ({it.key})")
        lines.append(f"Category : {it.category}")
        lines.append(f"Homepage : {it.homepage}")
        lines.append(f"CLI      : {it.cli or '(none)'}")
        lines.append(f"Install  : {it.install}")
        lines.append(f"Auth     : {it.auth}")
        lines.append("")
        lines.append(it.summary)
        lines.append("")
        lines.append("Detection markers:")
        for m in it.detection:
            lines.append(f"  - {m}")
        lines.append("")
        lines.append("Commands:")
        for c in it.commands:
            lines.append(f"  $ {c.cmd}")
            lines.append(f"      {c.purpose}" + (f"  [{c.notes}]" if c.notes else ""))
        lines.append("")
        lines.append("Pitfalls:")
        for p in it.pitfalls:
            lines.append(f"  - {p}")
        if it.docs_url:
            lines.append("")
            lines.append(f"Docs: {it.docs_url}")
        self.detail.setPlainText("\n".join(lines))

    # ----------------------------------------------------------- detection
    def _build_detection_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addWidget(QLabel(
            "Static detection of which integrations the current project uses. "
            "Detection is file/marker based only — no commands are run."
        ))
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Project root:"))
        self.root_label = QLabel("(none selected)")
        controls.addWidget(self.root_label, 1)
        btn = self._make_button("Detect for current project")
        btn.clicked.connect(self._detect)
        controls.addWidget(btn)
        v.addLayout(controls)

        self.det_table = make_table(["detected", "key", "label", "category", "markers"])
        v.addWidget(self.det_table, 1)
        return w

    def _make_button(self, text):
        from PySide6.QtWidgets import QPushButton
        b = QPushButton(text)
        return b

    def _detect(self) -> None:
        pid = self.ctx.project_id
        if not pid:
            self.root_label.setText("(no project selected)")
            return
        from pyfix360.db.models import ProjectRow

        with self.ctx.session() as s:
            proj = s.get(ProjectRow, pid)
            root = proj.root_path if proj is not None else ""
        if not root:
            self.root_label.setText("(project not found)")
            return
        self.root_label.setText(root)
        results = integration_service.detect_for_project(root)
        rows = []
        for r in results:
            if r.detected:
                rows.append(["YES", r.key, r.label, r.category,
                             ", ".join(r.markers_found)])
        # also show not-detected count in detail? keep table to detected only
        if not rows:
            rows.append(["-", "-", "No integrations detected", "-", "-"])
        fill_table(self.det_table, rows)

    # ------------------------------------------------------------ playbooks
    def _build_playbooks_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Category:"))
        self.pb_cat = QComboBox()
        self.pb_cat.addItems(["all"] + integration_service.platform_categories())
        self.pb_cat.currentTextChanged.connect(lambda _t: self._refresh_playbooks())
        controls.addWidget(self.pb_cat)
        controls.addWidget(QLabel("Search:"))
        self.pb_search = QLineEdit()
        self.pb_search.setPlaceholderText("prompt, question, situation, API, ...")
        self.pb_search.textChanged.connect(lambda _t: self._refresh_playbooks())
        controls.addWidget(self.pb_search, 1)
        v.addLayout(controls)

        split = QSplitter(Qt.Horizontal)
        self.pb_table = make_table(["platform", "label", "mode", "api", "reviewed"])
        self.pb_table.itemSelectionChanged.connect(self._show_playbook)
        split.addWidget(self.pb_table)
        self.pb_detail = QPlainTextEdit()
        self.pb_detail.setReadOnly(True)
        split.addWidget(self.pb_detail)
        split.setSizes([360, 620])
        v.addWidget(split, 1)

        self._refresh_playbooks()
        return w

    def _refresh_playbooks(self) -> None:
        cat = self.pb_cat.currentText() if hasattr(self, "pb_cat") else "all"
        query = self.pb_search.text().strip() if hasattr(self, "pb_search") else ""
        items = integration_service.list_platforms()
        if query:
            items = integration_service.search(query)
            items = [it for it in items if it.kind == "platform"]
        elif cat and cat != "all":
            items = [it for it in items if it.category == cat]
        rows = [[it.key, it.label, it.operation_mode or "-",
                 it.api or "—", it.reviewed_on or "-"]
                for it in items]
        fill_table(self.pb_table, rows)
        if not rows:
            self.pb_detail.setPlainText("No platform matches.")

    def _show_playbook(self) -> None:
        sel = self.pb_table.selectedItems()
        if not sel:
            return
        first = self.pb_table.item(sel[0].row(), 0)
        if first is None:
            return
        key = first.text()
        pb = integration_service.platform_playbook(key)
        if pb is None:
            return
        self.pb_detail.setPlainText(format_playbook(pb))

    # -------------------------------------------------------------- operate
    def _build_operate_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        v.addWidget(QLabel(
            "Send a prompt to a platform. The API path needs an API key; the "
            "browser path drives a signed-in session and needs the optional "
            "[automation] extra. Both paths are gated by Settings → Access "
            "Control and Settings → Privacy, and every call is audited."
        ))

        row = QHBoxLayout()
        row.addWidget(QLabel("Platform:"))
        self.op_platform = QComboBox()
        self.op_platform.setMinimumWidth(240)
        for it in integration_service.list_platforms():
            self.op_platform.addItem(it.label, it.key)
        self.op_platform.currentIndexChanged.connect(lambda _i: self._refresh_operate())
        row.addWidget(self.op_platform)
        self.op_status = QLabel("")
        self.op_status.setObjectName("muted")
        self.op_status.setWordWrap(True)
        row.addWidget(self.op_status, 1)
        v.addLayout(row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key:"))
        self.op_key = QLineEdit()
        self.op_key.setEchoMode(QLineEdit.Password)
        self.op_key.setPlaceholderText(
            "stored in the OS keyring, never in the database")
        key_row.addWidget(self.op_key, 1)
        key_row.addWidget(make_button("Save key", self._save_key))
        key_row.addWidget(make_button("Remove key", self._remove_key, secondary=True))
        v.addLayout(key_row)

        handle_row = QHBoxLayout()
        handle_row.addWidget(QLabel("Job handle:"))
        self.op_handle = QLineEdit()
        self.op_handle.setPlaceholderText(
            "the chat / job id returned by a previous send")
        handle_row.addWidget(self.op_handle, 1)
        v.addLayout(handle_row)

        self.op_prompt = QPlainTextEdit()
        self.op_prompt.setPlaceholderText(
            "the prompt to send — it is redacted before it leaves this machine")
        self.op_prompt.setMaximumHeight(110)
        v.addWidget(self.op_prompt)

        btn_row = QHBoxLayout()
        btn_row.addWidget(make_button("Preview what will be sent", self._preview))
        btn_row.addWidget(make_button("Send via API", self._send_api))
        btn_row.addWidget(make_button("Send via browser", self._send_browser,
                                      secondary=True))
        btn_row.addWidget(make_button("Send follow-up", self._send_followup,
                                      secondary=True))
        btn_row.addWidget(make_button("Read status", self._read_status,
                                      secondary=True))
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addWidget(QLabel("<b>Result</b>"))
        self.op_output = QPlainTextEdit()
        self.op_output.setReadOnly(True)
        self.op_output.setMinimumHeight(180)
        v.addWidget(self.op_output, 1)

        self._refresh_operate()
        return w

    def _refresh_operate(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        if not key:
            return
        ready = {r.key: r for r in platform_service.readiness()}
        info = ready.get(key)
        if info is None:
            self.op_status.setText("")
            return
        bits = []
        bits.append(f"mode: {info.operation_mode or 'unknown'}")
        bits.append(f"API: {'yes' if info.has_api else 'no'}")
        bits.append(f"key: {'stored' if info.has_credential else 'not stored'}")
        bits.append(f"browser: {'available' if info.browser_available else 'not installed'}")
        if info.note:
            bits.append(f"— {info.note}")
        self.op_status.setText(" | ".join(bits))

    def _save_key(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        secret = self.op_key.text().strip()
        if not key or not secret:
            self.op_output.setPlainText("Choose a platform and type a key.")
            return
        backend = platform_service.store_credential(key, secret)
        self.op_key.clear()
        self.op_output.setPlainText(
            f"Key stored in the {backend} backend. It is never written to the "
            "database.")
        self._refresh_operate()

    def _remove_key(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        if not key:
            return
        platform_service.delete_credential(key)
        self.op_output.setPlainText(f"Key for '{key}' removed.")
        self._refresh_operate()

    def _preview(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        text = self.op_prompt.toPlainText().strip()
        if not key or not text:
            self.op_output.setPlainText("Type a prompt first.")
            return
        with self.ctx.session() as s:
            self.op_output.setPlainText(
                platform_service.preview_prompt(s, key, text))

    def _send_api(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        text = self.op_prompt.toPlainText().strip()
        if not key or not text:
            self.op_output.setPlainText("Type a prompt first.")
            return

        answer = QMessageBox.question(
            self, "Send this?",
            f"This sends a prompt to {key} over the network.\n\n"
            "Only continue if you trust the platform and the privacy mode "
            "allows it.")
        if answer != QMessageBox.Yes:
            return

        self.op_output.setPlainText("Sending …")
        pid = self.ctx.project_id

        def work():
            with self.ctx.session() as s:
                return platform_service.submit_prompt(s, key, text, confirmed=True,
                                                      project_id=pid)

        def done(result):
            self.op_output.setPlainText(
                f"handle: {result.handle or '-'}\n"
                f"status: {result.status or '-'}\n\n{result.output}")
            self.ctx.status(f"Prompt sent to {key}.")

        def failed(message):
            self.op_output.setPlainText(f"Refused or failed:\n\n{message}")
            self.ctx.status(message.splitlines()[0][:160])

        run_async(work, on_done=done, on_error=failed)

    def _send_browser(self) -> None:
        from pyfix360.integrations import browser
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        text = self.op_prompt.toPlainText().strip()
        if not key or not text:
            self.op_output.setPlainText("Type a prompt first.")
            return

        available, reason = browser.playwright_available()
        if not available:
            self.op_output.setPlainText(
                f"{reason}.\n\n{browser.INSTALL_HINT}")
            return

        pid = self.ctx.project_id
        try:
            with self.ctx.session() as s:
                payload = platform_service.browser_payload(
                    s, key, text, confirmed=True, project_id=pid)
        except platform_service.PlatformRefused as exc:
            self.op_output.setPlainText(f"Refused:\n\n{exc}")
            return

        self.op_output.setPlainText("Opening the browser …")

        def work():
            with browser.BrowserSession(headless=False) as session:
                result = session.submit_prompt(key, payload)
                answer = session.read_last_response(key)
            return result, answer

        def done(payload):
            result, answer = payload
            self.op_output.setPlainText(
                f"url: {result.handle or '-'}\n\n{answer}")
            self.ctx.status(f"Prompt submitted to {key} in the browser.")

        def failed(message):
            self.op_output.setPlainText(f"Refused or failed:\n\n{message}")
            self.ctx.status(message.splitlines()[0][:160])

        run_async(work, on_done=done, on_error=failed)

    def _send_followup(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        text = self.op_prompt.toPlainText().strip()
        handle = self.op_handle.text().strip()
        if not key:
            return
        if not handle:
            self.op_output.setPlainText(
                "Paste the job handle returned by the previous send first.")
            return
        if not text:
            self.op_output.setPlainText(
                "Type the answer to the question the platform asked.")
            return

        self.op_output.setPlainText("Sending the follow-up …")
        pid = self.ctx.project_id

        def work():
            with self.ctx.session() as s:
                return platform_service.send_followup(s, key, handle, text,
                                                     confirmed=True,
                                                     project_id=pid)

        def done(result):
            self.op_output.setPlainText(
                f"status: {result.status or '-'}\n\n{result.output}")
            self.ctx.status(f"Follow-up sent to {key}.")

        def failed(message):
            self.op_output.setPlainText(f"Refused or failed:\n\n{message}")
            self.ctx.status(message.splitlines()[0][:160])

        run_async(work, on_done=done, on_error=failed)

    def _read_status(self) -> None:
        from pyfix360.services import platform_service

        key = self.op_platform.currentData()
        if not key:
            return
        handle = self.op_handle.text().strip()
        if not handle:
            self.op_output.setPlainText(
                "Paste the job handle returned by the previous send first.")
            return
        self.op_output.setPlainText("Reading status …")
        pid = self.ctx.project_id

        def work():
            with self.ctx.session() as s:
                return platform_service.status(s, key, handle, confirmed=True,
                                               project_id=pid)

        def done(result):
            self.op_output.setPlainText(
                f"status: {result.status or '-'}\n\n{result.output}")

        def failed(message):
            self.op_output.setPlainText(f"Refused or failed:\n\n{message}")

        run_async(work, on_done=done, on_error=failed)


def format_playbook(pb) -> str:
    """Render a playbook as the plain-text document shown in the detail pane."""
    lines: list[str] = []
    lines.append(f"# {pb.label}   ({pb.key})")
    lines.append("")
    lines.append(pb.summary)
    lines.append("")
    lines.append(f"Homepage : {pb.homepage}")
    if pb.docs_url:
        lines.append(f"Docs     : {pb.docs_url}")
    lines.append(f"Operation: {pb.operation_mode or 'not declared'}")
    lines.append(f"Reviewed : {pb.reviewed_on or 'not recorded'}")

    if pb.warnings:
        lines.append("")
        lines.append("READ THIS BEFORE YOU RELY ON IT")
        for w in pb.warnings:
            lines.append(f"  ! {w}")

    if pb.auth:
        lines.append("")
        lines.append("AUTHENTICATION")
        lines.append(f"  {pb.auth}")

    if pb.api or pb.api_capabilities:
        lines.append("")
        lines.append("API")
        lines.append(f"  base : {pb.api or '(none published)'}")
        lines.append(f"  key  : sent as {pb.api_key_header or '(unknown header)'}"
                     + (f" — keys: {pb.api_key_url}" if pb.api_key_url else ""))
        if pb.api_capabilities:
            lines.append("  can  :")
            for cap in pb.api_capabilities:
                lines.append(f"    - {cap}")
        else:
            lines.append("  can  : nothing is claimed in this build")

    if pb.cli:
        lines.append("")
        lines.append("CLI")
        lines.append(f"  {pb.cli}  ({pb.install})")

    if pb.deep_link:
        lines.append("")
        lines.append("DEEP LINK (prefills a prompt)")
        lines.append(f"  {pb.deep_link}")

    if pb.command_dialect:
        lines.append("")
        lines.append("HOW TO GIVE COMMANDS")
        for d in pb.command_dialect:
            lines.append(f"  - {d}")

    if pb.prompt_patterns:
        lines.append("")
        lines.append("PROMPT PATTERNS")
        for p in pb.prompt_patterns:
            lines.append(f"  * {p}")

    if pb.qa_flow:
        lines.append("")
        lines.append("QUESTIONS IT ASKS, AND HOW TO ANSWER")
        for q in pb.qa_flow:
            lines.append(f"  ? {q}")

    if pb.situations:
        lines.append("")
        lines.append("SITUATIONS AND THE RIGHT RESPONSE")
        for s in pb.situations:
            lines.append(f"  ! {s}")

    if pb.pitfalls:
        lines.append("")
        lines.append("PITFALLS")
        for p in pb.pitfalls:
            lines.append(f"  - {p}")

    return "\n".join(lines)
