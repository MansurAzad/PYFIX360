"""The background host — the same autopilot pipeline, in a process with no window.

:mod:`pyfix360.services.autopilot_service` is the state machine and
:mod:`pyfix360.services.job_service` is the queue. This module is the third piece:
the loop that joins them when nobody is watching.

Three things differ from the desktop driver in :mod:`pyfix360.ui.autopilot`, and
each is a deliberate choice rather than an omission:

**Steps run inline.** There is no GUI thread to protect, so a dispatched step is
executed on the host's own thread and reported straight back. The Qt worker pool
would buy nothing here. Because the host paces itself by sleeping between polls,
the machine's own step interval is switched off.

**Cancellation lands between steps.** That is the only place it *can* land: a
running test suite is inside a subprocess of its own. The operator is told this
rather than being shown a Stop button that pretends otherwise.

**An ASK gate parks instead of guessing.** The desktop answers a gate with Enter.
A host has no keyboard, so it stops, writes the prompt into the job row and the
log, and waits for ``service approve`` / ``service skip`` (or the Autopilot
screen) to answer out of band. A daemon that quietly confirmed its own gated step
would turn the access matrix into decoration — which is why starting an
``ASK``-gated *run* is refused outright unless the operator confirmed it when the
job was queued.

This module imports no Qt and no Windows API, so the loop is testable with an
injected clock and a temporary database.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pyfix360.models import BackgroundJob, JobStatus
from pyfix360.services import autopilot_service, job_service
from pyfix360.services.autopilot_service import Autopilot, Step
from pyfix360.services.settings_service import AccessDenied

logger = logging.getLogger(__name__)

#: How often the host wakes to look for work and re-stamp its heartbeat.
DEFAULT_POLL_S = 2.0

#: A job is only presumed abandoned after this much silence *and* a dead pid.
MIN_STALE_S = 60.0

#: Capability that guards starting an unattended run, named once for messages.
CAPABILITY = autopilot_service.CAPABILITY_AUTOPILOT


@dataclass
class Poll:
    """What one cycle actually did, so callers can report it truthfully."""

    claimed: int | None = None
    finished: int | None = None
    final_status: str = ""
    steps_run: int = 0
    parked: int | None = None
    idle: bool = True
    interrupted: tuple[int, ...] = ()
    note: str = ""


@dataclass
class Background:
    """A single-process consumer of :mod:`pyfix360.services.job_service`.

    ``clock`` and ``sleeper`` are injected because the alternative is a test suite
    that sleeps for real, and ``steps_for`` is injected so a test can hand the host
    a queue it controls instead of the live pipeline.
    """

    session_factory: Callable | None = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    poll_s: float = DEFAULT_POLL_S
    window_s: float = autopilot_service.DEFAULT_WINDOW_S
    stale_s: float | None = None
    pid: int | None = None
    host: str = ""
    steps_for: Callable[[int | None], Iterable[Step]] | None = None

    def __post_init__(self) -> None:
        self._job: BackgroundJob | None = None
        self._pilot: Autopilot | None = None
        self._parked: int | None = None
        self._stale_s = self.stale_s or max(MIN_STALE_S, self.poll_s * 30)

    @property
    def job(self) -> BackgroundJob | None:
        """The job this host is holding, if any."""
        return self._job

    @property
    def waiting(self) -> bool:
        """True when the held job is stopped at an approval gate.

        Nothing is in flight in that state, which matters when the host has to
        explain why it is giving the job up.
        """
        return self._job is not None and self._pilot is not None \
            and self._pilot.pending is not None

    # ------------------------------------------------------------------ one cycle
    def poll(self) -> Poll:
        """Reap, listen, advance one step. A failing job is reported, not raised."""
        interrupted = tuple(self._reap())
        if self._job is None:
            if not self._claim():
                return Poll(idle=True, interrupted=interrupted)
            return Poll(idle=False, claimed=self._job.id,
                        note=unattended_status(self._pilot, self._job.id),
                        interrupted=interrupted)

        job, pilot = self._job, self._pilot
        cancel, resume = self._signals(job.id)
        if cancel:
            return self._close(JobStatus.CANCELLED,
                               note="cancelled on the operator's request",
                               interrupted=interrupted)
        if resume:
            self._parked = None            # a later gate must be reported again
            change = (pilot.approve_pending() if resume == "approve"
                      else pilot.deny_pending())
            logger.info("job #%s answered '%s': %s", job.id, resume, change.note)
            return self._publish(Poll(idle=False, note=change.note,
                                      interrupted=interrupted))

        change = pilot.tick()
        if change.dispatch is not None:
            ok, detail = self._run(change.dispatch)
            change = pilot.finish_step(ok=ok, detail=detail)
            return self._publish(Poll(idle=False, steps_run=1,
                                      note=change.note or detail,
                                      interrupted=interrupted))
        return self._publish(Poll(idle=False, note=change.note,
                                  interrupted=interrupted))

    # ------------------------------------------------------------------ the loop
    def run(self, *, stop: Callable[[], bool] | None = None,
            max_polls: int | None = None) -> dict:
        """Poll until told to stop, sleeping only when there is nothing to do.

        Steps run back-to-back once a job is claimed: the machine's own interval
        is switched off because this loop *is* the pacing, and sleeping between
        steps would only delay the heartbeat that proves the host is alive.
        """
        counts = {"polls": 0, "steps": 0, "done": 0, "failed": 0,
                  "cancelled": 0, "interrupted": 0, "parked": 0}
        while True:
            result = self.poll()
            counts["polls"] += 1
            counts["steps"] += result.steps_run
            counts["interrupted"] += len(result.interrupted)
            if result.parked is not None:
                counts["parked"] += 1
            if result.final_status:
                counts[result.final_status] = counts.get(result.final_status, 0) + 1
            if stop is not None and stop():
                break
            if max_polls is not None and counts["polls"] >= max_polls:
                break
            if result.idle or result.parked is not None:
                self.sleeper(self.poll_s)
        return counts

    # ------------------------------------------------------------------ claiming
    def _claim(self) -> bool:
        """Take the next queued job and build its machine. False if there is none."""
        with self._sessions() as s:
            job = job_service.claim(s, pid=self._pid(), host=self.host)
        if job is None:
            return False
        if job.kind != job_service.KIND_AUTOPILOT:
            self._fail(job, f"the background host has no runner for '{job.kind}'")
            return False
        steps = list(self._steps_for(job.project_id))
        if not steps:
            self._fail(job, "the pipeline produced no steps — this job has no "
                            "project, or the project no longer exists")
            return False

        pilot = Autopilot(clock=self.clock, window_s=self.window_s,
                          session_factory=self._sessions,
                          project_id=job.project_id)
        pilot.step_interval_s = 0.0
        try:
            change = pilot.start(steps, confirmed=job.confirmed)
        except AccessDenied as exc:
            self._fail(
                job,
                f"{exc} An unattended run cannot answer for you: set "
                f"'{CAPABILITY}' to FULL in Settings → Access Control, or queue "
                f"the job with confirmation.")
            return False
        self._job, self._pilot = job, pilot
        logger.info("job #%s claimed (%d steps, confirmed=%s): %s",
                    job.id, len(steps), job.confirmed, change.note)
        self._write(_snapshot(pilot), JobStatus.RUNNING,
                    unattended_status(pilot, job.id))
        return True

    # ------------------------------------------------------------------ advancing
    def _publish(self, result: Poll) -> Poll:
        """Write the machine's shape back to the row readers in other processes see."""
        pilot = self._pilot
        if pilot.pending is not None:
            result.parked = self._job.id
            prompt = parked_prompt(pilot.pending, self._job.id)
            self._write(_snapshot(pilot), JobStatus.WAITING_APPROVAL, prompt)
            if self._parked != self._job.id:
                # Later polls only heartbeat. Logging this every poll buried the
                # rest of the file in a job that is waiting for a human.
                self._parked = self._job.id
                logger.info("job #%s parked: %s", self._job.id, prompt)
            return result
        if (pilot.state is autopilot_service.State.PAUSED
                and pilot.reason is autopilot_service.Reason.FAILED):
            return self._close(JobStatus.FAILED,
                               note=unattended_status(pilot, self._job.id),
                               interrupted=result.interrupted,
                               steps=result.steps_run)
        if pilot.state is autopilot_service.State.IDLE:
            return self._close(_outcome(pilot),
                               note=unattended_status(pilot, self._job.id),
                               interrupted=result.interrupted,
                               steps=result.steps_run)
        self._write(_snapshot(pilot), JobStatus.RUNNING,
                    unattended_status(pilot, self._job.id))
        return result

    def _write(self, rows: list[dict], status: JobStatus, detail: str) -> None:
        with self._sessions() as s:
            job_service.heartbeat(s, self._job.id, status=status,
                                  detail=detail, steps=rows)

    def _close(self, status: JobStatus, *, note: str,
               interrupted: tuple[int, ...] = (), steps: int = 0) -> Poll:
        job = self._job
        rows = _snapshot(self._pilot)
        with self._sessions() as s:
            job_service.finish(s, job.id, status, detail=note,
                               error=note if status is not JobStatus.DONE else "",
                               steps=rows)
        logger.info("job #%s → %s: %s", job.id, status.value, note)
        self._job, self._pilot, self._parked = None, None, None
        return Poll(finished=job.id, final_status=status.value, steps_run=steps,
                    idle=False, interrupted=interrupted, note=note)

    def _fail(self, job: BackgroundJob, error: str) -> None:
        with self._sessions() as s:
            job_service.finish(s, job.id, JobStatus.FAILED, detail=error,
                               error=error)
        logger.warning("job #%s failed before it started: %s", job.id, error)

    def _signals(self, job_id: int) -> tuple[bool, str]:
        with self._sessions() as s:
            return job_service.take_signals(s, job_id)

    def _reap(self) -> list[int]:
        with self._sessions() as s:
            return job_service.reap_stale(s, limit_s=self._stale_s,
                                          keep_pid=self._pid())

    # ------------------------------------------------------------------ running
    def _run(self, step: Step) -> tuple[bool, str]:
        """Execute a dispatched step here. An exception is a result, not a crash."""
        logger.info("job #%s: running '%s'", self._job.id, step.label)
        try:
            return True, (step.run() or "") if step.run is not None else ""
        except Exception as exc:                        # noqa: BLE001 - reported
            logger.exception("job #%s: step '%s' raised",
                             self._job.id, step.label)
            return False, f"{type(exc).__name__}: {exc}"

    def _steps_for(self, project_id: int | None) -> list[Step]:
        if self.steps_for is not None:
            return list(self.steps_for(project_id))
        if project_id is None:
            return []
        return autopilot_service.pipeline_steps(project_id,
                                                session_factory=self._sessions)

    # ------------------------------------------------------------------ plumbing
    def _sessions(self):
        factory = self.session_factory
        if factory is None:
            from pyfix360.db import session_scope

            factory = session_scope
        return factory()

    def _pid(self) -> int:
        return self.pid or os.getpid()


def parked_prompt(pending: autopilot_service.PendingApproval,
                  job_id: int | None) -> str:
    """How a gate is worded when there is no keyboard in front of it.

    The desktop prompt says "press Enter", which is true at a desk and a lie in a
    log file, so the host writes the command that actually answers it.
    """
    return (f"{pending.message} — answer with "
            f"`pyfix360 service approve {job_id}` or "
            f"`pyfix360 service skip {job_id}`")


def unattended_status(pilot: Autopilot, job_id: int | None) -> str:
    """:meth:`Autopilot.status_line` rewritten for a run with no keyboard.

    The desktop line tells the operator which key to press. In a job row and a
    log file that reads as an instruction nobody can carry out, so the same state
    is stated with the command that actually applies.
    """
    if pilot.pending is not None:
        return parked_prompt(pilot.pending, job_id)
    done, total = pilot.progress
    base = f"job #{job_id} {pilot.state.value} ({done}/{total} steps)"
    if (pilot.state is autopilot_service.State.PAUSED
            and pilot.reason is autopilot_service.Reason.FAILED):
        last = pilot.completed[-1] if pilot.completed else None
        which = f"'{last.label}'" if last else "a step"
        return f"{base} — {which} raised; the rest of the queue was left untouched"
    if pilot.state is autopilot_service.State.IDLE:
        outstanding = [step.label for step in pilot.completed
                       if step.status != "done"]
        if outstanding:
            return f"{base} — not completed: {', '.join(outstanding)}"
    return base


def _outcome(pilot: Autopilot) -> JobStatus:
    """Whether a finished run counts as done or failed.

    A *skipped* step is not a failure — it is the operator's own answer at a gate,
    and calling it one would make declining a gated step look like the run
    breaking. Refused (blocked in Settings) is the same kind of deliberate outcome.
    Only an exception counts against the job; the audit trail already lists which
    steps never ran.
    """
    if any(step.status == "failed" for step in pilot.completed):
        return JobStatus.FAILED
    return JobStatus.DONE


def _snapshot(pilot: Autopilot | None) -> list[dict]:
    """The machine's queue, flattened for a reader in another process."""
    if pilot is None:
        return []
    steps: list[Step] = list(pilot.completed)
    if pilot.inflight is not None:
        steps.append(pilot.inflight)
    steps += list(pilot.queue)
    return job_service.step_rows(steps)


def from_settings(session_factory: Callable | None = None, **kwargs) -> Background:
    """Build a host on the persisted timings rather than the module defaults."""
    from pyfix360.services import settings_service

    scope = session_factory or _session_scope()
    poll_s, window_s = DEFAULT_POLL_S, autopilot_service.DEFAULT_WINDOW_S
    try:
        with scope() as s:
            settings = settings_service.get_settings(s)
        poll_s = max(0.25, settings.service_poll_ms / 1000.0)
        window_s = max(0.15, settings.autopilot_gesture_window_ms / 1000.0)
    except Exception:                          # pragma: no cover - first run
        logger.debug("background host using default timings", exc_info=True)
    return Background(session_factory=session_factory, poll_s=poll_s,
                      window_s=window_s, **kwargs)


def _session_scope():
    from pyfix360.db import session_scope

    return session_scope
