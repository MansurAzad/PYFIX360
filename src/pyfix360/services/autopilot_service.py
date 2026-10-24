"""Autopilot — run the pipeline unattended, and stop the instant the user says so.

The state machine is ``IDLE → RUNNING → PAUSED → RUNNING``. Three rules shape it,
and each one exists because the obvious implementation gets it wrong:

**The grace window.** A double Enter pauses and a triple Enter resumes, but a
triple *contains* a double. Acting on the second Enter would mean every resume
first pauses, which looks like the app stuttering. So the second Enter is never
acted on immediately: it opens a window (``window_s``, measured from the **first**
Enter of the gesture, not rolling between presses). A third Enter inside that
window resolves the gesture as a triple and cancels the pending pause; if the
window closes with only two Enters in it, the gesture was a double and the pause
happens then. A caller that never presses a third key still gets the pause,
because :meth:`Autopilot.tick` resolves the expiry.

**Approval is not a gesture.** When a step hits a capability set to ``ASK``, the
run pauses with reason ``approval`` and a *single* Enter approves it. Gesture
counting is suspended while an approval is outstanding, because the operator is
answering a prompt rather than issuing a command.

**Nothing is silently skipped.** A blocked capability, a failed step and a denied
approval each end up in the audit trail and in :attr:`Autopilot.events`, and the
state after them is stated rather than implied.

This module deliberately imports no Qt. The timers and the key filter that make
it live inside the desktop app are in :mod:`pyfix360.ui.autopilot`; keeping the
machine pure is what makes the gesture arithmetic testable at all.
"""

from __future__ import annotations

import enum
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pyfix360.models import AccessLevel
from pyfix360.services import audit

logger = logging.getLogger(__name__)

#: Capability that guards *starting* an unattended run. Like every other
#: capability it is enforced in the settings service, not re-implemented here.
CAPABILITY_AUTOPILOT = "autopilot"

#: How long to wait for a third Enter before a double becomes a pause.
DEFAULT_WINDOW_S = 0.500

#: Minimum spacing between queue steps, so a tick-driven loop cannot spin.
DEFAULT_STEP_INTERVAL_S = 0.20


# --------------------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------------------


class State(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class Reason(str, enum.Enum):
    """Why the run is paused — the operator needs to know which answer is wanted."""

    NONE = ""
    GESTURE = "gesture"        # double Enter; a triple resumes
    APPROVAL = "approval"      # a step hit an ASK capability; Enter approves
    FAILED = "failed"          # a step raised; the queue is left intact
    FINISHED = "finished"      # the queue drained


class Gesture(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"        # inside the grace window, nothing decided yet
    LONE = "lone"              # a single Enter that never became a gesture
    DOUBLE = "double"          # → pause
    TRIPLE = "triple"          # → resume


# --------------------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------------------


@dataclass
class Step:
    """One unit of queued work, plus the capability it is allowed to consume.

    ``run`` is a zero-argument callable that opens whatever session it needs, so
    a background step can be dispatched to a worker thread without the state
    machine having to own a session on its behalf.
    """

    label: str
    capability: str = ""
    run: Callable[[], str] | None = None
    #: Long-running steps are handed to the caller to dispatch off the GUI thread.
    background: bool = True
    status: str = "queued"        # queued | running | done | failed | skipped | refused
    detail: str = ""
    confirmed: bool = False

    def describe(self) -> str:
        gate = f" [{self.capability}]" if self.capability else ""
        return f"{self.status}{gate}: {self.label}"


@dataclass
class PendingApproval:
    """A step stopped at an ASK gate, waiting for the operator."""

    step: Step
    capability: str
    message: str

    @property
    def prompt(self) -> str:
        return f"{self.message}  Press Enter to approve, Esc to skip."


@dataclass
class Event:
    at: float
    kind: str
    note: str


@dataclass
class Change:
    """What one input actually did, so the caller can report it truthfully."""

    state: State = State.IDLE
    reason: Reason = Reason.NONE
    note: str = ""
    gesture: Gesture = Gesture.NONE
    #: Set when the state machine started a background step and expects the
    #: caller to run it and report back via :meth:`Autopilot.finish_step`.
    dispatch: Step | None = None

    @property
    def changed(self) -> bool:
        return bool(self.note)


# --------------------------------------------------------------------------------------
# the machine
# --------------------------------------------------------------------------------------


class Autopilot:
    """A Qt-free state machine driven by ``press_enter`` and ``tick``.

    Every method must be called on the GUI thread. ``run_async`` callbacks are
    delivered there, so a background step finishing is a GUI-thread event too.
    """

    def __init__(self, *,
                 clock: Callable[[], float] = time.monotonic,
                 window_s: float = DEFAULT_WINDOW_S,
                 step_interval_s: float = DEFAULT_STEP_INTERVAL_S,
                 session_factory: Callable | None = None,
                 project_id: int | None = None) -> None:
        self._clock = clock
        self.window_s = window_s
        self.step_interval_s = step_interval_s
        self._session_factory = session_factory
        self.project_id = project_id

        self._state = State.IDLE
        self._reason = Reason.NONE
        self._queue: list[Step] = []
        self._completed: list[Step] = []
        self._inflight: Step | None = None
        self._pending: PendingApproval | None = None
        self._enters: list[float] = []
        self._last_step_at: float = 0.0
        self.events: list[Event] = []

    # ------------------------------------------------------------------ reads
    @property
    def state(self) -> State:
        return self._state

    @property
    def reason(self) -> Reason:
        return self._reason

    @property
    def pending(self) -> PendingApproval | None:
        return self._pending

    @property
    def inflight(self) -> Step | None:
        """The background step dispatched and not yet reported back.

        The UI needs it: it is out of the queue and not yet in ``completed``,
        so a run screen that ignored it would show a step vanishing mid-flight.
        """
        return self._inflight

    @property
    def queue(self) -> Sequence[Step]:
        return tuple(self._queue)

    @property
    def completed(self) -> Sequence[Step]:
        return tuple(self._completed)

    @property
    def gesture_depth(self) -> int:
        """How many Enters are currently sitting inside the grace window."""
        return len(self._enters)

    @property
    def progress(self) -> tuple[int, int]:
        """Completed steps and total — the in-flight one still counts."""
        done = len(self._completed)
        outstanding = len(self._queue) + (1 if self._inflight is not None else 0)
        return done, done + outstanding

    def status_line(self) -> str:
        """One line that says exactly where the run stands."""
        done, total = self.progress
        if self._state is State.IDLE:
            base = "idle"
        elif self._state is State.RUNNING:
            base = f"running ({done}/{total} steps)"
        else:
            base = f"paused ({done}/{total} steps)"
        if self._pending is not None:
            return f"AUTOPILOT {base} — {self._pending.prompt}"
        if self._state is State.PAUSED:
            hint = ("triple Enter to resume" if self._reason is Reason.GESTURE
                    else "triple Enter to resume, or Stop to abandon")
            return f"AUTOPILOT {base} — {self._reason.value} — {hint}"
        if self._gesture_open:
            return f"AUTOPILOT {base} — awaiting a second Enter"
        return f"AUTOPILOT {base}"

    @property
    def _gesture_open(self) -> bool:
        return len(self._enters) == 1

    # ------------------------------------------------------------------ setup
    def load(self, steps: Iterable[Step]) -> int:
        """Replace the queue. Only legal while idle, so a run cannot be re-planned
        out from under itself."""
        if self._state is not State.IDLE:
            raise AutopilotBusy("stop the run before loading a new queue")
        self._queue = list(steps)
        self._completed = []
        self.events = []
        return len(self._queue)

    def start(self, steps: Iterable[Step] | None = None, *,
              confirmed: bool = False) -> Change:
        """Open the run. Raises ``AccessDenied`` when policy refuses it."""
        from pyfix360.services import settings_service

        if self._state is not State.IDLE:
            return Change(self._state, self._reason,
                          f"already {self._state.value}; nothing was started.")
        with self._sessions() as s:
            settings_service.check_access(s, CAPABILITY_AUTOPILOT,
                                          confirmed=confirmed)
        if steps is not None:
            self._queue = list(steps)
            self._completed = []
        if not self._queue:
            return Change(State.IDLE, Reason.NONE,
                          "the queue is empty; autopilot was not started.")
        self._enters = []
        self._events = []
        self._last_step_at = 0.0
        change = self._set(State.RUNNING, Reason.NONE, action="autopilot.start",
                           note=f"{len(self._queue)} steps queued")
        self._record("start", change.note)
        return change

    def stop(self, *, reason: str = "stopped by the operator") -> Change:
        """Abandon the run. The queue is discarded, the audit trail is not."""
        if self._state is State.IDLE:
            return Change(State.IDLE, Reason.NONE, "already idle.")
        dropped = len(self._queue)
        self._queue = []
        self._inflight = None
        self._pending = None
        self._enters = []
        change = self._set(State.IDLE, Reason.NONE, action="autopilot.stop",
                           note=f"{reason}; {dropped} queued steps discarded",
                           risk="warn")
        self._record("stop", change.note)
        return change

    # ------------------------------------------------------------------ input
    def press_enter(self, now: float | None = None) -> Change:
        """Feed one Enter press into the gesture decoder.

        This is the whole point of the module, so the ordering is deliberate:
        an outstanding approval is answered first, then the grace window is
        settled, then the press is counted.
        """
        now = self._clock() if now is None else now

        if self._pending is not None:
            self._enters = []
            return self.approve_pending()

        settled = self._settle_window(now)
        if settled is Gesture.DOUBLE:
            # The window closed on this press: honour the pause it earned, and
            # let this press begin a fresh gesture.
            self._enters = [now]
            return self._apply_double()
        if settled is Gesture.LONE:
            self._enters = [now]
            return Change(self._state, self._reason, gesture=Gesture.LONE)

        if not self._enters:
            self._enters = [now]
            return Change(self._state, self._reason, gesture=Gesture.PENDING,
                          note="first Enter recorded — a second decides")
        self._enters.append(now)

        if len(self._enters) == 2:
            # The grace window: acting here is the bug this whole file exists to
            # avoid, because a triple would pause before it resumes.
            return Change(self._state, self._reason, gesture=Gesture.PENDING,
                          note="second Enter — holding the pause open for a third")

        self._enters = []
        return self._apply_triple()

    def discard_gesture(self) -> None:
        """Void the Enters counted so far, without acting on them.

        The UI calls this when an unrelated keystroke lands between two Enters:
        they were not a pair, and letting the window expire later would pause on
        a gesture the operator abandoned.
        """
        self._enters = []

    def tick(self, now: float | None = None) -> Change:
        """Called by the UI timer. Settles an expired gesture window, then
        advances at most one queue step."""
        now = self._clock() if now is None else now

        settled = self._settle_window(now)
        if settled is Gesture.DOUBLE:
            return self._apply_double()
        if settled is Gesture.LONE:
            return Change(self._state, self._reason, gesture=Gesture.LONE)

        if self._state is not State.RUNNING:
            return Change(self._state, self._reason)
        if self._inflight is not None:
            return Change(self._state, self._reason)
        if not self._queue:
            # Reached when a resumed or denied run has nothing left; without this
            # the machine would sit "running" on an empty queue forever.
            return self._finish()
        if now - self._last_step_at < self.step_interval_s:
            return Change(self._state, self._reason)
        return self._run_next(now)

    def finish_step(self, *, ok: bool = True, detail: str = "") -> Change:
        """Report the outcome of a dispatched background step (GUI thread)."""
        step = self._inflight
        if step is None:
            return Change(self._state, self._reason,
                          "no step was in flight — the report was ignored.")
        self._inflight = None
        step.status = "done" if ok else "failed"
        step.detail = detail
        return self._close_step(step)

    # ------------------------------------------------------------------ approval
    def approve_pending(self) -> Change:
        """Confirm the step sitting at the ASK gate and continue."""
        if self._pending is None:
            return Change(self._state, self._reason,
                          "nothing is waiting for approval.")
        step = self._pending.step
        capability = self._pending.capability
        self._pending = None
        step.confirmed = True
        self._audit("autopilot.approve", f"{capability} approved for '{step.label}'")
        change = self._set(State.RUNNING, Reason.NONE, action="autopilot.resume",
                           note=f"approved '{step.label}' — continuing")
        self._record("approve", change.note)
        return change

    def deny_pending(self) -> Change:
        """Skip the gated step. The rest of the queue still runs, because the
        operator declined one action rather than the whole run."""
        if self._pending is None:
            return Change(self._state, self._reason,
                          "nothing is waiting for approval.")
        step = self._pending.step
        capability = self._pending.capability
        self._pending = None
        step.status = "skipped"
        step.detail = f"{capability} declined by the operator"
        if self._queue and self._queue[0] is step:
            self._queue.pop(0)
        self._audit("autopilot.deny",
                    f"{capability} declined for '{step.label}'", risk="warn")
        self._close_step(step)
        change = self._set(State.RUNNING, Reason.NONE, action="autopilot.resume",
                           note=f"skipped '{step.label}' — continuing")
        self._record("deny", change.note)
        return change

    # ------------------------------------------------------------------ internals
    def _settle_window(self, now: float) -> Gesture:
        """Resolve a grace window that has already closed, else report ``none``."""
        if not self._enters:
            return Gesture.NONE
        if now - self._enters[0] < self.window_s:
            return Gesture.NONE
        depth = len(self._enters)
        self._enters = []
        if depth >= 3:
            # Only reachable if a tick lands between the second and third press.
            return Gesture.TRIPLE
        return Gesture.DOUBLE if depth == 2 else Gesture.LONE

    def _apply_double(self) -> Change:
        if self._state is not State.RUNNING:
            return Change(self._state, self._reason, gesture=Gesture.DOUBLE,
                          note="double Enter ignored — the run is not active")
        change = self._set(State.PAUSED, Reason.GESTURE, action="autopilot.pause",
                           note="double Enter — paused", gesture=Gesture.DOUBLE)
        self._record("pause", "double Enter")
        return change

    def _apply_triple(self) -> Change:
        if self._state is State.RUNNING:
            return Change(self._state, self._reason, gesture=Gesture.TRIPLE,
                          note="triple Enter ignored — already running")
        if self._state is State.IDLE:
            return Change(self._state, self._reason, gesture=Gesture.TRIPLE,
                          note="triple Enter ignored — nothing is paused")
        change = self._set(State.RUNNING, Reason.NONE, action="autopilot.resume",
                           note="triple Enter — resumed", gesture=Gesture.TRIPLE)
        self._record("resume", "triple Enter")
        return change

    def _run_next(self, now: float) -> Change:
        step = self._queue[0]
        self._last_step_at = now

        if step.capability:
            from pyfix360.services import settings_service

            with self._sessions() as s:
                level = settings_service.access_level(s, step.capability)
            if level is AccessLevel.BLOCKED:
                step.status = "refused"
                step.detail = f"{step.capability} is blocked in Settings → Access Control"
                self._queue.pop(0)
                self._audit("autopilot.step.refused", step.detail, risk="warn")
                self._close_step(step)
                return Change(self._state, self._reason,
                              note=f"refused '{step.label}' — {step.detail}")
            if level is AccessLevel.ASK and not step.confirmed:
                self._pending = PendingApproval(
                    step=step, capability=step.capability,
                    message=f"'{step.label}' needs {step.capability}, "
                            "which is set to ASK")
                change = self._set(State.PAUSED, Reason.APPROVAL,
                                   action="autopilot.pause",
                                   note=self._pending.message)
                self._record("approval", self._pending.prompt)
                return change

        if step.background and step.run is not None:
            # Popped here, not in _close_step: leaving it queued would hand the
            # same step back to the next tick forever.
            self._queue.pop(0)
            step.status = "running"
            self._inflight = step
            self._audit("autopilot.step.start", step.label)
            return Change(self._state, self._reason,
                          note=f"dispatched '{step.label}'", dispatch=step)
        return self._run_inline(step)

    def _run_inline(self, step: Step) -> Change:
        self._queue.pop(0)
        step.status = "running"
        try:
            step.detail = step.run() if step.run is not None else ""
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            step.status = "failed"
            step.detail = f"{type(exc).__name__}: {exc}"
            logger.exception("autopilot step failed: %s", step.label)
        else:
            step.status = "done"
        return self._close_step(step)

    def _close_step(self, step: Step) -> Change:
        self._completed.append(step)
        self._audit(f"autopilot.step.{step.status}",
                    f"{step.label} — {step.detail}"[:400],
                    risk="warn" if step.status in ("failed", "refused", "skipped")
                    else "info")
        self._record("step", step.describe())
        if step.status == "failed":
            return self._set(State.PAUSED, Reason.FAILED, action="autopilot.pause",
                             note=f"'{step.label}' failed: {step.detail}")
        if not self._queue and self._inflight is None and self._state is State.RUNNING:
            return self._finish()
        return Change(self._state, self._reason, note=step.describe())

    def _finish(self) -> Change:
        failed = [s.label for s in self._completed if s.status != "done"]
        note = f"queue drained ({len(self._completed)} steps)"
        if failed:
            note += f"; not completed: {', '.join(failed)}"
        change = self._set(State.IDLE, Reason.FINISHED, action="autopilot.finish",
                           note=note, risk="warn" if failed else "info")
        self._record("finish", note)
        return change

    def _set(self, state: State, reason: Reason, *, action: str, note: str,
             gesture: Gesture = Gesture.NONE, risk: str = "info") -> Change:
        previous = self._state
        self._state = state
        self._reason = reason
        self._audit(action, f"{previous.value} → {state.value}: {note}", risk=risk)
        return Change(state, reason, note, gesture)

    def _record(self, kind: str, note: str) -> None:
        self.events.append(Event(at=self._clock(), kind=kind, note=note))
        if len(self.events) > 500:
            del self.events[: len(self.events) - 500]

    # ------------------------------------------------------------------ plumbing
    def _sessions(self):
        """A session scope for reads and audit writes.

        Audit failures propagate on purpose: a transition that cannot be
        recorded is a transition the operator should not be told succeeded.
        """
        factory = self._session_factory
        if factory is None:
            from pyfix360.db import session_scope

            factory = session_scope
        return factory()

    def _audit(self, action: str, details: str, *, risk: str = "info") -> None:
        with self._sessions() as s:
            audit.record(s, action, project_id=self.project_id,
                         target="autopilot", details=details,
                         actor="autopilot", risk=risk)


class AutopilotBusy(Exception):
    """Raised when a new queue is loaded while a run is active."""


# --------------------------------------------------------------------------------------
# the standard unattended run
# --------------------------------------------------------------------------------------


def pipeline_steps(project_id: int, *, session_factory: Callable | None = None,
                   workspace: Path | None = None) -> list[Step]:
    """The pipeline worth running unattended, in the order that fails cheapest.

    Every ``run`` opens its own session, so a step can execute on a worker
    thread. The repair step is deliberately gated on ``apply_to_original`` —
    which ships as ``ASK`` — so an unattended run reaches a real approval
    prompt before anything is written to the operator's project.
    """
    from pyfix360.db import session_scope as _session_scope

    factory = session_factory or _session_scope

    def _scan() -> str:
        from pyfix360.services import scan_service

        with factory() as s:
            scan = scan_service.create_scan(s, project_id)
            done = scan_service.run_scan(s, scan.id)
            return f"scan #{done.id} → {done.status} ({done.findings_count} findings)"

    def _tests() -> str:
        from pyfix360.services import test_runner

        root = workspace
        if root is None:
            from pyfix360.db.models import ProjectRow

            with factory() as s:
                project = s.get(ProjectRow, project_id)
                root = Path(project.root_path) if project else Path(".")
        with factory() as s:
            run = test_runner.run_tests(s, project_id, root, phase="autopilot",
                                        confirmed=True)
            return f"tests → {run.outcome} ({run.passed} passed, {run.failed} failed)"

    def _repairs() -> str:
        from sqlalchemy import select

        from pyfix360.db.models import RepairPlanRow
        from pyfix360.services import repair_service

        with factory() as s:
            plan = s.scalars(
                select(RepairPlanRow)
                .where(RepairPlanRow.project_id == project_id)
                .order_by(RepairPlanRow.id.desc())
            ).first()
            if plan is None:
                return "no repair plan for this project — nothing applied"
            result = repair_service.execute_repair(
                s, plan.id, apply_to_original=True, confirmed=True)
            return f"plan #{plan.id} → {result.status.value}"

    def _gates() -> str:
        from pyfix360.services import gate_service

        with factory() as s:
            report = gate_service.evaluate(s, project_id)
            return gate_service.summary_line(report)

    def _report() -> str:
        from pyfix360.services import report_service

        with factory() as s:
            path = report_service.generate_report(s, project_id, confirmed=True)
            return f"report → {path.name}"

    return [
        Step("Scan the project", run=_scan),
        Step("Run the test suite", capability="run_tests", run=_tests),
        Step("Apply the newest repair plan to the original",
             capability="apply_to_original", run=_repairs),
        Step("Evaluate quality gates", run=_gates),
        Step("Export a report", capability="export_reports", run=_report),
    ]
