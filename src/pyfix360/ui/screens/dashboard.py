"""Overview Dashboard."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pyfix360.db.models import (
    AuditEventRow,
    FindingRow,
    PatchRow,
    ProjectRow,
    ScanRow,
)
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import StatCard, fill_table, make_table, page_header


class DashboardScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(page_header(
            "Overview",
            "Offline-first inspection, audit and repair. No source code leaves "
            "this machine unless you explicitly enable and approve a remote AI provider.",
        ))

        cards = QGridLayout()
        cards.setSpacing(12)
        self.card_projects = StatCard("Projects", "0")
        self.card_files = StatCard("Tracked files", "0")
        self.card_findings = StatCard("Findings (latest scans)", "0")
        self.card_critical = StatCard("Critical findings", "0")
        self.card_verified = StatCard("Verified repairs", "0")
        self.card_skipped = StatCard("Skipped tools (incomplete scans)", "0")
        for i, card in enumerate((self.card_projects, self.card_files, self.card_findings,
                                  self.card_critical, self.card_verified, self.card_skipped)):
            cards.addWidget(card, i // 3, i % 3)
        layout.addLayout(cards)

        layout.addWidget(QLabel("<b>Recent audit events</b>"))
        self.events = make_table(["time", "actor", "risk", "action", "target", "details"])
        self.events.setMinimumHeight(260)
        layout.addWidget(self.events, 1)

        self.ctx.data_changed.connect(self.refresh)
        self.ctx.project_changed.connect(lambda _pid: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        with self.ctx.session() as s:
            projects = s.query(ProjectRow).all()
            self.card_projects.set_value(len(projects))
            self.card_files.set_value(sum(p.file_count or 0 for p in projects))

            latest_scan_ids = [p.last_scan_id for p in projects if p.last_scan_id]
            findings = []
            for sid in latest_scan_ids:
                findings.extend(s.query(FindingRow).filter_by(scan_id=sid).all())
            self.card_findings.set_value(len(findings))
            self.card_critical.set_value(
                sum(1 for f in findings if f.severity == "critical"))
            self.card_verified.set_value(
                s.query(PatchRow).filter_by(status="verified").count())

            skipped = 0
            for sid in latest_scan_ids:
                scan = s.get(ScanRow, sid)
                if scan:
                    skipped += len(scan.tools_skipped or [])
            self.card_skipped.set_value(skipped)

            events = s.query(AuditEventRow).order_by(AuditEventRow.id.desc()).limit(60).all()
            fill_table(self.events, [
                [str(e.timestamp)[:19], e.actor, e.risk, e.action,
                 (e.target or "")[:40], (e.details or "")[:90]]
                for e in events
            ])
