"""The queue that outlives the window: one row per background job.

The GUI and the background host are *separate processes*, sometimes in separate
Windows sessions, so they cannot share an object. This table is the whole
protocol between them: the GUI writes intent (``confirmed``, ``cancel_requested``,
``resume_action``) and the host writes progress. Nothing in here runs work — that
is :mod:`pyfix360.services.background_service`.

Two decisions shape the schema:

**Intent is data, not calls.** An operator answering an approval gate from the
taskbar cannot reach into the daemon's memory, so the answer is a column the host
reads on its next poll. :func:`take_signals` consumes ``resume_action`` exactly
once, which is what makes a double-click on "Approve" harmless.

**A heartbeat, not a live flag.** A host that is killed, crashes, or loses power
leaves its job marked ``running`` forever if the only evidence of life is the
status column. Every poll re-stamps ``updated_at`` and :func:`reap_stale` turns
anything silent past the deadline into ``interrupted`` — reported as *unknown*,
never as "failed", because a half-applied repair is not a failure.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pyfix360.db.models import BackgroundJobRow
from pyfix360.models import BackgroundJob, JobStatus, utcnow
from pyfix360.services import audit

KIND_AUTOPILOT = "autopilot"

#: Statuses that mean the job will not change again.
TERMINAL = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED,
            JobStatus.INTERRUPTED)

#: Statuses in which a host may still be working.
LIVE = (JobStatus.RUNNING, JobStatus.WAITING_APPROVAL)

RESUME_ACTIONS = ("", "approve", "skip")


# --------------------------------------------------------------------------------------
# writing intent
# --------------------------------------------------------------------------------------


def enqueue(session: Session, project_id: int | None, *, kind: str = KIND_AUTOPILOT,
            confirmed: bool = False, actor: str = "user",
            note: str = "", now: datetime | None = None) -> BackgroundJob:
    """Put a job on the queue.

    ``confirmed`` carries the operator's answer to the ``autopilot`` capability
    *forward in time*, because the process that runs the job is not the process
    that was asked. Without it the host refuses to start an ASK-gated run — a
    daemon does not get to satisfy a confirmation just because nobody is there
    to hear the question.
    """
    now = now or utcnow()
    if project_id is not None:
        from pyfix360.db.models import ProjectRow

        if session.get(ProjectRow, project_id) is None:
            raise ValueError(
                f"project #{project_id} does not exist — nothing was queued")
    row = BackgroundJobRow(
        project_id=project_id, kind=kind, status=JobStatus.QUEUED.value,
        detail=note, confirmed=confirmed, created_at=now, updated_at=now,
    )
    session.add(row)
    session.flush()
    audit.record(session, "job.enqueue", project_id=project_id, target=row.kind,
                 details=f"job #{row.id} queued (confirmed={confirmed})")
    return BackgroundJob.model_validate(row)


def request_cancel(session: Session, job_id: int, *, actor: str = "user",
                   now: datetime | None = None) -> BackgroundJob | None:
    """Abandon a job.

    A queued job is cancelled outright — nothing has started. A live one can only
    be asked, because its host may be mid-step; the flag is checked between steps.
    """
    row = session.get(BackgroundJobRow, job_id)
    if row is None:
        return None
    now = now or utcnow()
    status = JobStatus(row.status)
    if status in TERMINAL:
        return BackgroundJob.model_validate(row)
    if status is JobStatus.QUEUED:
        _close(row, JobStatus.CANCELLED, detail="cancelled before it started", now=now)
        audit.record(session, "job.cancel", project_id=row.project_id,
                     target=f"job #{job_id}", details="cancelled while queued",
                     actor=actor, risk="warn")
    else:
        row.cancel_requested = True
        row.updated_at = now
        audit.record(session, "job.cancel.request", project_id=row.project_id,
                     target=f"job #{job_id}",
                     details="cancel requested; honoured between steps",
                     actor=actor, risk="warn")
    session.flush()
    return BackgroundJob.model_validate(row)


def answer(session: Session, job_id: int, action: str, *, actor: str = "user",
           now: datetime | None = None) -> BackgroundJob | None:
    """Reply to a parked approval gate: ``"approve"`` or ``"skip"``.

    Only a job that is actually waiting accepts an answer, so a stale click
    cannot silently pre-authorise a future gate.
    """
    if action not in ("approve", "skip"):
        raise ValueError(f"answer must be 'approve' or 'skip', got {action!r}")
    row = session.get(BackgroundJobRow, job_id)
    if row is None:
        return None
    if JobStatus(row.status) is not JobStatus.WAITING_APPROVAL:
        raise ValueError(f"job #{job_id} is {row.status}, not waiting for approval")
    if row.resume_action:
        # The host has not polled the first answer yet. Replacing it would let a
        # double-click quietly change what an unattended run is allowed to do.
        raise ValueError(
            f"job #{job_id} already has an unanswered '{row.resume_action}'")
    row.resume_action = action
    row.updated_at = now or utcnow()
    audit.record(session, f"job.{action}", project_id=row.project_id,
                 target=f"job #{job_id}", details=row.detail, actor=actor,
                 risk="warn" if action == "approve" else "info")
    session.flush()
    return BackgroundJob.model_validate(row)


# --------------------------------------------------------------------------------------
# the host's side
# --------------------------------------------------------------------------------------


def claim(session: Session, *, pid: int | None = None, host: str = "",
          now: datetime | None = None) -> BackgroundJob | None:
    """Take the oldest queued job, or ``None``.

    The ``WHERE status = 'queued'`` on the update is the whole point: two hosts
    that both read the same row cannot both claim it, because only one update
    matches. SQLite serialises writers, so this is a real guarantee rather than
    a likely-looking one.
    """
    now = now or utcnow()
    candidate = session.scalars(
        select(BackgroundJobRow)
        .where(BackgroundJobRow.status == JobStatus.QUEUED.value)
        .order_by(BackgroundJobRow.id)
        .limit(1)
    ).first()
    if candidate is None:
        return None
    job_id = candidate.id
    taken = session.execute(
        update(BackgroundJobRow)
        .where(BackgroundJobRow.id == job_id,
               BackgroundJobRow.status == JobStatus.QUEUED.value)
        .values(status=JobStatus.RUNNING.value, started_at=now, updated_at=now,
                pid=pid if pid is not None else os.getpid(),
                host=host or socket.gethostname(), detail="claimed by the host")
    )
    if taken.rowcount != 1:            # type: ignore[union-attr]
        return None                    # another host got there first
    row = session.get(BackgroundJobRow, job_id)
    audit.record(session, "job.claim", project_id=row.project_id,
                 target=f"job #{job_id}",
                 details=f"started by pid {row.pid} on {row.host}")
    session.flush()
    return BackgroundJob.model_validate(row)


def heartbeat(session: Session, job_id: int, *,
              status: JobStatus | None = None, detail: str | None = None,
              steps: Sequence[dict] | None = None,
              now: datetime | None = None) -> BackgroundJob | None:
    """Re-stamp ``updated_at`` and, optionally, publish a progress snapshot."""
    row = session.get(BackgroundJobRow, job_id)
    if row is None:
        return None
    row.updated_at = now or utcnow()
    if status is not None:
        row.status = status.value
    if detail is not None:
        row.detail = detail
    if steps is not None:
        row.steps = list(steps)
    session.flush()
    return BackgroundJob.model_validate(row)


def take_signals(session: Session, job_id: int) -> tuple[bool, str]:
    """Read and consume the operator's signals: ``(cancel_requested, resume)``.

    ``resume_action`` is cleared here, so an answer is applied once and only once
    however many polls see it.
    """
    row = session.get(BackgroundJobRow, job_id)
    if row is None:
        return False, ""
    resume, row.resume_action = row.resume_action, ""
    cancel = bool(row.cancel_requested)
    session.flush()
    return cancel, resume


def finish(session: Session, job_id: int, status: JobStatus, *, detail: str = "",
           error: str = "", steps: Sequence[dict] | None = None,
           actor: str = "host",
           now: datetime | None = None) -> BackgroundJob | None:
    """Close a job with its final state."""
    if status not in TERMINAL:
        raise ValueError(f"{status.value} is not a closing state")
    row = session.get(BackgroundJobRow, job_id)
    if row is None:
        return None
    _close(row, status, detail=detail, error=error, steps=steps, now=now or utcnow())
    audit.record(session, f"job.{status.value}", project_id=row.project_id,
                 target=f"job #{job_id}",
                 details=(detail or error)[:400], actor=actor,
                 risk="info" if status is JobStatus.DONE else "warn")
    session.flush()
    return BackgroundJob.model_validate(row)


def reap_stale(session: Session, *, limit_s: float,
               keep_pid: int | None = None, now: datetime | None = None,
               probe: Callable[[int | None], bool] | None = None) -> list[int]:
    """Mark silent jobs interrupted and return their ids.

    A quiet heartbeat is only half the evidence: a host grinding through a
    forty-second test suite is quiet too. So a job is reaped only when its stamp
    is old *and* the recorded pid is gone, which is also what makes a second host
    on this machine safe to run this.
    """
    now = now or utcnow()
    keep_pid = os.getpid() if keep_pid is None else keep_pid
    alive = probe or pid_alive
    stale: list[int] = []
    rows = session.scalars(
        select(BackgroundJobRow)
        .where(BackgroundJobRow.status.in_([s.value for s in LIVE]))
    ).all()
    for row in rows:
        if row.pid == keep_pid:
            continue
        stamp = row.updated_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if (now - stamp).total_seconds() <= limit_s:
            continue
        if alive(row.pid):
            continue                       # slow, not dead — leave it alone
        _close(row, JobStatus.INTERRUPTED,
               detail=f"the host stopped reporting after {_age(stamp, now)}",
               error="the background process went quiet; work may be half-applied "
                     "and has not been rolled back",
               now=now)
        audit.record(session, "job.interrupted", project_id=row.project_id,
                     target=f"job #{row.id}",
                     details=f"no heartbeat for {limit_s:.0f}s and pid {row.pid} "
                             f"is gone",
                     risk="warn")
        stale.append(row.id)
    if stale:
        session.flush()
    return stale


# --------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------


def get(session: Session, job_id: int) -> BackgroundJob | None:
    row = session.get(BackgroundJobRow, job_id)
    return BackgroundJob.model_validate(row) if row else None


def list_jobs(session: Session, *, open_only: bool = False, limit: int = 50,
              project_id: int | None = None) -> list[BackgroundJob]:
    stmt = select(BackgroundJobRow).order_by(BackgroundJobRow.id.desc())
    if open_only:
        stmt = stmt.where(BackgroundJobRow.status.notin_(
            [s.value for s in TERMINAL]))
    if project_id is not None:
        stmt = stmt.where(BackgroundJobRow.project_id == project_id)
    return [BackgroundJob.model_validate(r) for r in session.scalars(stmt.limit(limit))]


def counters(session: Session) -> dict[str, int]:
    """Job counts by status — the substance behind ``service status``."""
    rows = session.scalars(select(BackgroundJobRow.status)).all()
    out = {s.value: 0 for s in JobStatus}
    for value in rows:
        out[value] = out.get(value, 0) + 1
    return out


def step_rows(steps: Iterable) -> list[dict]:
    """Render autopilot steps for readers, without importing Qt or the machine."""
    from pyfix360.services.autopilot_service import Step

    out = []
    for step in steps:
        if isinstance(step, Step):
            out.append({"label": step.label, "status": step.status,
                        "capability": step.capability, "detail": step.detail})
        else:
            out.append(dict(step))
    return out


# --------------------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------------------


def _close(row: BackgroundJobRow, status: JobStatus, *, detail: str,
           error: str = "", steps: Sequence[dict] | None = None,
           now: datetime) -> None:
    row.status = status.value
    row.detail = detail
    row.error = error
    if steps is not None:
        row.steps = list(steps)
    row.finished_at = now
    row.updated_at = now
    row.cancel_requested = False
    row.resume_action = ""


def _age(then: datetime, now: datetime) -> str:
    seconds = int((now - then).total_seconds())
    delta = timedelta(seconds=seconds)
    return f"{delta}"


def pid_alive(pid: int | None) -> bool:
    """Whether ``pid`` still names a running process.

    Deliberately not ``os.kill(pid, 0)``: on Windows that path reaches
    ``TerminateProcess``, so the liveness probe would kill what it probed.
    """
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                  # exists, owned by somebody else
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    still_active, query_limited, access_denied = 259, 0x1000, 5
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(query_limited, False, pid)
    if not handle:
        # An existing process we may not query still counts as alive.
        return kernel32.GetLastError() == access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True                  # could not read it; assume alive
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
