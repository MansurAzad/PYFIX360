"""Audit trail service.

Every meaningful action (import, scan, patch, apply, rollback, AI request)
records an immutable audit event.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pyfix360.db.models import AuditEventRow


def record(
    session: Session,
    action: str,
    *,
    project_id: int | None = None,
    target: str | None = None,
    details: str = "",
    actor: str = "system",
    risk: str = "info",
) -> AuditEventRow:
    row = AuditEventRow(
        project_id=project_id,
        action=action,
        target=target,
        details=details,
        actor=actor,
        risk=risk,
    )
    session.add(row)
    session.flush()
    return row


def purge_history(session: Session, *, confirmed: bool = False) -> int:
    """Delete every audit event, and say how many went.

    Erasing the trail is the one action the trail cannot record for itself, so
    it is gated by the ``purge_history`` capability — *blocked* by default —
    and the purge is written down afterwards, which is the only trace of it
    that survives.
    """
    from pyfix360.services import settings_service

    settings_service.check_access(session, "purge_history", confirmed=confirmed)
    count = session.query(AuditEventRow).delete()
    session.flush()
    record(session, "danger.purge_history", actor="user", risk="high",
           details=f"{count} events removed")
    return count
