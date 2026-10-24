"""AI Provider Manager — the Custom AI Settings Center.

Three panels:

1. **Catalogue** — a curated, offline list of providers and their models. One
   click creates a provider row. Local runtimes need no key at all; remote
   providers start disabled and locked to local-only privacy until you
   deliberately change both.
2. **Configuration** — the full editor for the selected provider, including
   credential handling (never stored in the database) and privacy mode.
3. **Verification** — explicit buttons that actually contact the endpoint. A
   model is only described as live-verified after a real reply.

The catalogue is a *starting point*, not a guarantee: model ids go stale, so
each entry carries a review date and the Refresh button asks the provider
itself what it currently serves.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pyfix360.models import AIProviderConfig, PrivacyMode, ProviderType
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    fill_table,
    make_button,
    make_table,
    page_header,
    scrollable,
)
from pyfix360.ui.workers import run_async


class AIScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._current_id: int | None = None
        self._catalog_key = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(10)
        outer.addWidget(page_header(
            "AI Provider Manager",
            "All analysis works fully offline. AI is optional, runs only when "
            "you enable it, and is never the sole authority for verification.",
        ))

        warn = QLabel(
            "Source code is never transmitted unless you (1) enable the "
            "provider, (2) set its privacy mode to allow remote processing, and "
            "(3) tick 'allow repository content' in Settings → AI. Credentials "
            "are redacted before any prompt is built and are never written to "
            "the database."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("padding:8px; border-radius:6px;")
        outer.addWidget(warn)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._tab_catalog(), "Catalogue")
        self.tabs.addTab(self._tab_configure(), "Configure")
        self.tabs.addTab(self._tab_verify(), "Verify & Discover")
        outer.addWidget(self.tabs, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # Catalogue tab
    # ------------------------------------------------------------------
    def _tab_catalog(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        row = QHBoxLayout()
        row.addWidget(QLabel("Provider"))
        self.catalog_preset = QComboBox()
        self.catalog_preset.setMinimumWidth(320)
        self.catalog_preset.currentIndexChanged.connect(self._catalog_changed)
        row.addWidget(self.catalog_preset, 1)
        row.addWidget(make_button("Create this provider", self._create_from_catalog))
        lay.addLayout(row)

        self.catalog_note = QLabel("")
        self.catalog_note.setWordWrap(True)
        self.catalog_note.setObjectName("muted")
        lay.addWidget(self.catalog_note)

        self.catalog_models = make_table(
            ["model id", "cost", "context", "good for", "notes"])
        self.catalog_models.setMinimumHeight(220)
        lay.addWidget(self.catalog_models)

        # model picker for the chosen preset
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Model to use"))
        self.catalog_model_pick = QComboBox()
        self.catalog_model_pick.setMinimumWidth(320)
        self.catalog_model_pick.currentIndexChanged.connect(self._model_notes)
        pick.addWidget(self.catalog_model_pick, 1)
        lay.addLayout(pick)
        self.model_note = QLabel("")
        self.model_note.setWordWrap(True)
        self.model_note.setObjectName("muted")
        lay.addWidget(self.model_note)

        self.alias_box = QLabel("")
        self.alias_box.setWordWrap(True)
        lay.addWidget(self.alias_box)

        reviewed = QLabel("")
        reviewed.setObjectName("muted")
        self.reviewed_label = reviewed
        lay.addWidget(reviewed)
        lay.addStretch(1)
        return scrollable(page)

    def _catalog_changed(self) -> None:
        from pyfix360.services import ai_provider_service

        preset = ai_provider_service.preset_for_label(self.catalog_preset.currentText())
        if preset is None:
            return
        self._catalog_key = preset.key
        bits = [f"<b>{preset.label}</b>"]
        if preset.notes:
            bits.append(preset.notes)
        if preset.signup_url:
            bits.append(f"Get a key: {preset.signup_url}")
        bits.append("Requires a key: " + ("yes" if preset.requires_key else "no"))
        self.catalog_note.setText(" — ".join(bits))

        self.catalog_model_pick.clear()
        for model in preset.models:
            self.catalog_model_pick.addItem(f"{model.label} ({model.id})", model.id)
        fill_table(self.catalog_models, [
            [m.id, m.cost, f"~{m.context_k}k" if m.context_k else "-",
             ", ".join(m.good_for) or "-", m.notes]
            for m in preset.models
        ], color_col=1,
            colors={"free": "#3fb950", "cheap": "#d29922",
                    "local": "#58a6ff", "paid": "#f85149"})
        self._model_notes()
        self.reviewed_label.setText(
            f"Catalogue last reviewed {ai_provider_service.catalog_reviewed()}. "
            "Model ids change often — use 'Refresh model list' on the Verify "
            "tab to ask the provider directly.")
        aliases = ai_provider_service.aliases()
        lines = ["<b>Names you may have heard, and where they map:</b>"]
        for name, targets in aliases.items():
            lines.append(f"• {name} → {', '.join(targets)}")
        lines.append(f"<i>{ai_provider_service.aggregator_note()}</i>")
        self.alias_box.setText("<br>".join(lines))

    def _model_notes(self) -> None:
        from pyfix360.services import ai_provider_service

        model_id = self.catalog_model_pick.currentData()
        if not model_id:
            self.model_note.setText("")
            return
        self.model_note.setText(
            ai_provider_service.model_notes(self._catalog_key, model_id))

    def _create_from_catalog(self) -> None:
        from pyfix360.services import ai_provider_service

        if not self._catalog_key:
            return
        model = self.catalog_model_pick.currentData()
        with self.ctx.session() as s:
            preset = ai_provider_service.preset_for_label(
                self.catalog_preset.currentText())
            existing = [p for p in ai_provider_service.list_providers(s)
                        if p.catalog_key == self._catalog_key]
            if existing:
                answer = QMessageBox.question(
                    self, "Already configured",
                    f"'{preset.label}' already exists as provider "
                    f"#{existing[0].id}. Update it instead?")
                if answer != QMessageBox.Yes:
                    return
                row = existing[0]
                self._current_id = row.id
            else:
                row = ai_provider_service.create_from_preset(
                    s, self._catalog_key, model=model)
                self._current_id = row.id
            name = row.name
        self._reload_providers()
        self._load_into_form(self._current_id)
        self.tabs.setCurrentIndex(1)
        self.ctx.status(f"Provider '{name}' created — configure and enable it here.")
        if not self._requires_key(self._current_id):
            self.ctx.status(f"'{name}' is a local runtime; no API key is needed.")

    def _requires_key(self, provider_id: int) -> bool:
        from pyfix360.services import ai_provider_service

        with self.ctx.session() as s:
            row = s.get(ai_provider_service.AIProviderRow, provider_id)
            return bool(row and row.provider_type != ProviderType.LOCAL_RUNTIME.value)

    # ------------------------------------------------------------------
    # Configure tab
    # ------------------------------------------------------------------
    def _tab_configure(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        pick = QHBoxLayout()
        pick.addWidget(QLabel("Editing provider"))
        self.provider_pick = QComboBox()
        self.provider_pick.setMinimumWidth(300)
        self.provider_pick.currentIndexChanged.connect(self._pick_changed)
        pick.addWidget(self.provider_pick, 1)
        pick.addWidget(make_button("New blank provider", self._new_blank,
                                   secondary=True))
        lay.addLayout(pick)

        form_box = QGroupBox("Configuration")
        form = QFormLayout(form_box)
        self.name = QLineEdit()
        form.addRow("Name", self.name)
        self.provider_type = QComboBox()
        self.provider_type.addItems([t.value for t in ProviderType])
        form.addRow("Provider type", self.provider_type)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://… or http://localhost:11434/v1")
        form.addRow("Endpoint", self.base_url)
        self.model = QLineEdit()
        self.model.setPlaceholderText("model id, e.g. qwen2.5-coder:7b")
        form.addRow("Model", self.model)

        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText(
            "stored in the OS credential store, never in the database")
        self.clear_key = QCheckBox("clear stored key")
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.clear_key)
        key_w = QWidget()
        key_w.setLayout(key_row)
        form.addRow("API key", key_w)
        self.key_state = QLabel("")
        self.key_state.setObjectName("muted")
        form.addRow("", self.key_state)

        self.privacy = QComboBox()
        for mode in PrivacyMode:
            self.privacy.addItem(mode.value, mode.value)
        form.addRow("Privacy mode", self.privacy)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 3600)
        self.timeout.setSuffix(" s")
        form.addRow("Timeout", self.timeout)
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        form.addRow("Retry limit", self.retries)
        self.enabled = QCheckBox("enabled — allow PYFIX to call this provider")
        form.addRow("State", self.enabled)
        lay.addWidget(form_box)

        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Save provider", self._save))
        buttons.addWidget(make_button("Test connection", self._test))
        buttons.addWidget(make_button("Delete provider", self._delete, danger=True))
        buttons.addWidget(make_button("Clear form", self._clear, secondary=True))
        buttons.addStretch(1)
        lay.addLayout(buttons)

        self.backend = QLabel("")
        self.backend.setObjectName("muted")
        self.backend.setWordWrap(True)
        lay.addWidget(self.backend)

        self.table = make_table(
            ["id", "name", "type", "privacy", "model", "state", "key", "verified"])
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.setMinimumHeight(160)
        lay.addWidget(self.table)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        lay.addWidget(self.log)
        lay.addStretch(1)
        return scrollable(page)

    def _pick_changed(self) -> None:
        pid = self.provider_pick.currentData()
        if pid is None:
            return
        self._load_into_form(pid)

    def _new_blank(self) -> None:
        self._current_id = None
        self._catalog_key = ""
        self.name.clear()
        self.api_key.clear()
        self.model.clear()
        self.base_url.clear()
        self.clear_key.setChecked(False)
        self.enabled.setChecked(False)
        self.provider_type.setCurrentText(ProviderType.CUSTOM_OPENAI_COMPAT.value)
        self.privacy.setCurrentText(PrivacyMode.LOCAL_ONLY.value)
        self.key_state.setText("no key stored")

    def _load_into_form(self, provider_id: int) -> None:
        from pyfix360.services import ai_provider_service

        with self.ctx.session() as s:
            row = s.get(ai_provider_service.AIProviderRow, provider_id)
            if row is None:
                return
            model = ai_provider_service.to_model(row)
        self._current_id = provider_id
        self._catalog_key = model.catalog_key
        self.name.setText(model.name)
        self.provider_type.setCurrentText(model.provider_type.value)
        self.base_url.setText(model.base_url)
        self.model.setText(model.model)
        self.privacy.setCurrentText(model.privacy_mode.value)
        self.timeout.setValue(model.timeout_s)
        self.retries.setValue(model.retry_limit)
        self.enabled.setChecked(model.enabled)
        self.api_key.clear()
        self.api_key.setPlaceholderText(
            "a key is stored — type to replace" if model.has_api_key
            else "no key stored")
        self.key_state.setText(
            "A key is stored in the credential store." if model.has_api_key
            else "No key stored.")
        self._sync_pick(provider_id)

    def _sync_pick(self, provider_id: int) -> None:
        idx = self.provider_pick.findData(provider_id)
        if idx >= 0:
            self.provider_pick.blockSignals(True)
            self.provider_pick.setCurrentIndex(idx)
            self.provider_pick.blockSignals(False)

    def _save(self) -> None:
        from pyfix360.services import ai_provider_service

        cfg = AIProviderConfig(
            id=self._current_id,
            name=self.name.text().strip() or "unnamed",
            provider_type=ProviderType(self.provider_type.currentText()),
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            timeout_s=self.timeout.value(),
            retry_limit=self.retries.value(),
            privacy_mode=PrivacyMode(self.privacy.currentData()),
            enabled=self.enabled.isChecked(),
            catalog_key=self._catalog_key,
        )
        key = self.api_key.text() or None
        if self.clear_key.isChecked():
            key = ""
        with self.ctx.session() as s:
            row = ai_provider_service.upsert_provider(s, cfg, api_key=key)
            self._current_id = row.id
        self.api_key.clear()
        self.clear_key.setChecked(False)
        self.log.appendPlainText(
            f"Saved '{cfg.name}' (type={cfg.provider_type.value}, "
            f"privacy={cfg.privacy_mode.value}, enabled={cfg.enabled}).")
        self.refresh()

    def _test(self) -> None:
        if self._current_id is None:
            QMessageBox.information(self, "Test",
                                    "Save the provider before testing it.")
            return
        pid = self._current_id
        self.log.appendPlainText(f"Testing provider #{pid} …")
        self.ctx.status("Sending a one-word test prompt …")

        def work():
            from pyfix360.services import ai_provider_service

            with self.ctx.session() as s:
                return ai_provider_service.test_provider(s, pid)

        def done(result):
            ok, msg = result
            self.log.appendPlainText(("OK   " if ok else "FAIL ") + msg)
            self.ctx.status(msg)
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda m: self.log.appendPlainText("ERROR " + m))

    def _delete(self) -> None:
        from pyfix360.services import ai_provider_service

        if self._current_id is None:
            return
        if QMessageBox.question(
                self, "Delete provider",
                "Delete this provider and its stored credential?") != QMessageBox.Yes:
            return
        with self.ctx.session() as s:
            ai_provider_service.remove_provider(s, self._current_id)
        self.log.appendPlainText(f"Deleted provider #{self._current_id}.")
        self._current_id = None
        self._clear()
        self.refresh()

    def _clear(self) -> None:
        self._current_id = None
        self._catalog_key = ""
        self.name.clear()
        self.api_key.clear()
        self.model.clear()
        self.base_url.clear()
        self.clear_key.setChecked(False)
        self.enabled.setChecked(False)

    def _table_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        self._load_into_form(int(self.table.item(items[0].row(), 0).text()))
        self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------
    # Verify tab
    # ------------------------------------------------------------------
    def _tab_verify(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        intro = QLabel(
            "Verification is the only thing that lets PYFIX describe a model as "
            "live. 'Test connection' sends a one-word prompt; 'Refresh model "
            "list' asks the provider for its current catalogue. Both use your "
            "key and your endpoint only.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Provider"))
        self.verify_pick = QComboBox()
        self.verify_pick.setMinimumWidth(300)
        self.verify_pick.currentIndexChanged.connect(self._reload_discovered)
        row.addWidget(self.verify_pick, 1)
        row.addWidget(make_button("Test connection", self._verify_test))
        row.addWidget(make_button("Refresh model list", self._verify_discover,
                                  secondary=True))
        lay.addLayout(row)

        self.verify_status = QLabel("")
        self.verify_status.setWordWrap(True)
        lay.addWidget(self.verify_status)

        lay.addWidget(QLabel("<b>Models this provider reported</b>"))
        self.discovered = make_table(["model id", "annotation"])
        self.discovered.setMinimumHeight(200)
        lay.addWidget(self.discovered)

        note = QLabel(
            "Free/cheap annotations come from PYFIX's own catalogue. Anything "
            "marked 'unknown price' was reported by the provider but is not in "
            "our list, so we do not guess its cost.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)

        self.verify_log = QPlainTextEdit()
        self.verify_log.setReadOnly(True)
        self.verify_log.setMaximumHeight(130)
        lay.addWidget(self.verify_log)
        lay.addStretch(1)
        return scrollable(page)

    def _verify_provider_id(self) -> int | None:
        return self.verify_pick.currentData()

    def _provider_is_remote(self, provider_id: int | None) -> bool:
        """True when the provider would be contacted over the network."""
        if provider_id is None:
            return False
        from pyfix360.ai import gateway
        from pyfix360.db.models import AIProviderRow

        with self.ctx.session() as s:
            provider = s.get(AIProviderRow, provider_id)
            if provider is None:
                return False
            return gateway.provider_destination(provider)[1]

    def _verify_test(self) -> None:
        pid = self._verify_provider_id()
        if pid is None:
            QMessageBox.information(self, "Verify", "Add a provider first.")
            return
        self.verify_log.appendPlainText(f"Testing provider #{pid} …")

        def work():
            from pyfix360.services import ai_provider_service

            with self.ctx.session() as s:
                return ai_provider_service.test_provider(s, pid)

        def done(result):
            ok, msg = result
            self.verify_log.appendPlainText(("OK   " if ok else "FAIL ") + msg)
            self.verify_status.setText(msg)
            self.refresh()

        run_async(work, on_done=done,
                  on_error=lambda m: self.verify_log.appendPlainText("ERROR " + m))

    def _verify_discover(self) -> None:
        pid = self._verify_provider_id()
        if pid is None:
            return
        if self._provider_is_remote(pid):
            if self.ctx.access_denied("remote_ai",
                                      "Asking a remote provider for its model list",
                                      self):
                return
            if not self.ctx.confirm_access(
                    "remote_ai", "Ask this remote provider for its live model list",
                    self):
                return
        self.verify_log.appendPlainText(f"Requesting model list from #{pid} …")

        def work():
            from pyfix360.services import ai_provider_service

            with self.ctx.session() as s:
                return ai_provider_service.refresh_models(s, pid, confirmed=True)

        def done(result):
            ok, detail, ids = result
            self.verify_log.appendPlainText(
                ("OK   " if ok else "FAIL ") + detail)
            self._reload_discovered()

        run_async(work, on_done=done,
                  on_error=lambda m: self.verify_log.appendPlainText("ERROR " + m))

    def _reload_discovered(self) -> None:
        from pyfix360.services import ai_provider_service

        pid = self._verify_provider_id()
        if pid is None:
            fill_table(self.discovered, [])
            return
        with self.ctx.session() as s:
            rows = ai_provider_service.discovered_annotated(s, pid)
        fill_table(self.discovered, [[r.id, r.annotation] for r in rows])
        if not rows:
            self.verify_status.setText(
                "No models reported yet. Press 'Refresh model list' to ask the "
                "provider (this makes a metadata-only request).")

    # ------------------------------------------------------------------
    # shared refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        from pyfix360.credentials.store import store_info
        from pyfix360.services import ai_provider_service

        info = store_info()
        self.backend.setText(
            f"Credential store backend: <b>{info.backend}</b>. {info.warning}")

        if self.catalog_preset.count() == 0:
            for label, _preset in ai_provider_service.presets_by_label():
                self.catalog_preset.addItem(label)
            self._catalog_changed()

        self._reload_providers()

    def _reload_providers(self) -> None:
        from pyfix360.services import ai_provider_service

        with self.ctx.session() as s:
            rows = ai_provider_service.list_providers(s)
            models = [ai_provider_service.to_model(r) for r in rows]

        for combo in (self.provider_pick, self.verify_pick):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for m in models:
                combo.addItem(f"#{m.id} {m.name}", m.id)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        fill_table(self.table, [
            [m.id, m.name, m.provider_type.value, m.privacy_mode.value, m.model,
             "enabled" if m.enabled else "disabled",
             "yes" if m.has_api_key else "no",
             {True: "verified", False: "failed", None: "-"}[m.last_verify_ok]]
            for m in models
        ], color_col=7,
            colors={"verified": "#3fb950", "failed": "#f85149", "-": "#8b909b"})
        self._reload_discovered()
