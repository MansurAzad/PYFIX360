"""Findings retrieval with the user's display filters applied.

The Settings Center's *Findings* tab promises four things: a severity
floor, root-cause grouping, auto-acknowledgement of informational findings
and a switch for rules the user marked as false positives. They are
implemented here, in one place, so the inspection table, the repair picker,
the security view and the CLI all show the same thing.

Nothing here changes what is *stored*. A filter hides a row from a list; it
never deletes it and never rewrites its severity. Lowering the floor can
therefore never lose data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.db.models import FindingRow, ProjectRow
from pyfix360.models import FindingStatus, Severity

#: Most severe first. Anything not listed sorts last.
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get((severity or "").lower(), 9)


def _settings(session: Session):
    from pyfix360.services import settings_service

    return settings_service.get_settings(session)


def severity_floor(session: Session) -> Severity:
    """The lowest severity the user still wants to see."""
    return _settings(session).min_severity


def visible_findings(
    session: Session,
    project_id: int | None = None,
    *,
    scan_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    category: str | None = None,
    apply_settings: bool = True,
) -> list[FindingRow]:
    """The findings the user should see, most severe first.

    ``severity``/``status``/``category`` are the explicit on-screen filters;
    the Settings Center floor and the false-positive switch are applied on
    top of them unless ``apply_settings`` is turned off.
    """
    if scan_id is None and project_id is not None:
        project = session.get(ProjectRow, project_id)
        if project is None or not project.last_scan_id:
            return []
        scan_id = project.last_scan_id
    if scan_id is None:
        return []

    stmt = select(FindingRow).where(FindingRow.scan_id == scan_id)
    if severity:
        stmt = stmt.where(FindingRow.severity == severity)
    if status:
        stmt = stmt.where(FindingRow.status == status)
    if category:
        stmt = stmt.where(FindingRow.category == category)

    if apply_settings:
        settings = _settings(session)
        floor = settings.min_severity.value
        allowed = {name for name, rank in SEVERITY_ORDER.items() if rank <= severity_rank(floor)}
        stmt = stmt.where(FindingRow.severity.in_(allowed))
        if not settings.show_false_positive_rules:
            stmt = stmt.where(FindingRow.status != FindingStatus.FALSE_POSITIVE.value)

    rows = list(session.scalars(stmt))
    rows.sort(key=lambda f: (severity_rank(f.severity), f.file_path or "",
                             f.line_start or 0, f.id))
    return rows


@dataclass
class FindingGroup:
    """One root cause, with the findings that share it."""

    key: str
    rows: list[FindingRow] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def severity(self) -> str:
        return min((r.severity for r in self.rows),
                   key=severity_rank, default="info")

    @property
    def head(self) -> FindingRow:
        return self.rows[0]


def group_by_root_cause(rows: list[FindingRow]) -> list[FindingGroup]:
    """Collapse findings that share a root cause into one group each."""
    groups: dict[str, FindingGroup] = {}
    for row in rows:
        key = row.root_cause_group or f"finding:{row.id}"
        group = groups.get(key)
        if group is None:
            group = groups[key] = FindingGroup(key=key)
        group.rows.append(row)
    ordered = list(groups.values())
    for group in ordered:
        group.rows.sort(key=lambda f: (severity_rank(f.severity),
                                       f.file_path or "", f.line_start or 0, f.id))
    ordered.sort(key=lambda g: (severity_rank(g.severity),
                                g.head.file_path or "", g.head.line_start or 0))
    return ordered


def acknowledge_info(session: Session, scan_id: int) -> int:
    """Mark informational findings as acknowledged. Returns how many changed.

    Only ``info`` findings are touched, and only ones that are still in the
    ``detected`` state — a finding the user is already working on keeps its
    status.
    """
    rows = list(session.scalars(
        select(FindingRow).where(
            FindingRow.scan_id == scan_id,
            FindingRow.severity == Severity.INFO.value,
            FindingRow.status == FindingStatus.DETECTED.value,
        )
    ))
    for row in rows:
        row.status = FindingStatus.ACKNOWLEDGED.value
    if rows:
        session.flush()
    return len(rows)
