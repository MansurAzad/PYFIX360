"""Autopilot: the state machine, the grace window and the approval gate.

The clock is injected everywhere, so the gesture arithmetic is tested against
exact times rather than against somebody's sleep tolerance.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from pyfix360.models import AccessLevel, AppSettings
from pyfix360.services import autopilot_service, settings_service
from pyfix360.services.autopilot_service import (
    Autopilot,
    AutopilotBusy,
    Gesture,
    Reason,
    State,
    Step,
)

WINDOW = 0.500


# --------------------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------------------


class Clock:
    """A hand-advanced monotonic clock."""

    def __init__(self, at: float = 1000.0) -> None:
        self.now = at

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> float:
        self.now += delta
        return self.now


@contextlib.contextmanager
def _borrow(session):
    """Hand the test's own session to the machine.

    The machine opens a session per audit write; in tests that would nest a
    second transaction against the fixture's open one, so the fixture session is
    lent instead. Production passes ``AppContext.session``, which is a real
    ``session_scope``.
    """
    yield session


def _factory(session):
    def scope() -> Iterator:
        return _borrow(session)
    return scope


def _steps(*labels: str, capability: str = "", run=None) -> list[Step]:
    return [Step(label, capability=capability, run=run, background=False)
            for label in labels]


def _set(session, **keys) -> None:
    settings = settings_service.get_settings(session)
    for key, value in keys.items():
        setattr(settings, key, value)
    settings_service.save_settings(session, settings, keys=list(keys))


def _pilot(session, clock=None, **kwargs) -> tuple[Autopilot, Clock]:
    clock = clock or Clock()
    pilot = Autopilot(clock=clock, window_s=WINDOW, step_interval_s=0.0,
                      session_factory=_factory(session), **kwargs)
    return pilot, clock


def _double(pilot, clock) -> None:
    """Two Enters inside the window, then let the window close."""
    pilot.press_enter(clock.advance(0.01))
    pilot.press_enter(clock.advance(0.05))
    clock.advance(WINDOW)
    pilot.tick()


def _triple(pilot, clock) -> None:
    for _ in range(3):
        pilot.press_enter(clock.advance(0.05))


def _audit_actions(session) -> list[str]:
    from pyfix360.db.models import AuditEventRow

    session.flush()
    return [row.action for row in session.query(AuditEventRow).all()]


# --------------------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------------------


def test_starts_idle(session):
    pilot, _clock = _pilot(session)
    assert pilot.state is State.IDLE
    assert pilot.reason is Reason.NONE


def test_start_moves_idle_to_running(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    change = pilot.start(_steps("a", "b"))
    assert change.state is State.RUNNING
    assert pilot.progress == (0, 2)


def test_start_is_refused_when_the_capability_is_blocked(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        pilot.start(_steps("a"))
    assert pilot.state is State.IDLE


def test_start_needs_confirmation_when_the_capability_asks(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        pilot.start(_steps("a"))
    change = pilot.start(_steps("a"), confirmed=True)
    assert change.state is State.RUNNING


def test_start_with_an_empty_queue_says_so_rather_than_running(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    change = pilot.start([])
    assert change.state is State.IDLE
    assert "empty" in change.note


def test_stop_returns_to_idle_and_discards_the_queue(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a", "b", "c"))
    change = pilot.stop()
    assert change.state is State.IDLE
    assert pilot.queue == ()
    assert "3 queued steps discarded" in change.note


def test_queue_cannot_be_replanned_mid_run(session):
    pilot, _clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    with pytest.raises(AutopilotBusy):
        pilot.load(_steps("b"))


def test_running_a_step_paces_the_queue(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.step_interval_s = 0.2
    pilot.start(_steps("a", "b"))
    pilot.tick(clock.advance(1.0))
    assert pilot.progress == (1, 2)
    pilot.tick(clock.advance(0.05))          # too soon for the next step
    assert pilot.progress == (1, 2)
    pilot.tick(clock.advance(0.5))
    assert pilot.progress == (2, 2)


def test_draining_the_queue_finishes_rather_than_hanging(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("only"))
    pilot.tick(clock.advance(1.0))
    assert pilot.state is State.IDLE
    assert pilot.reason is Reason.FINISHED


# --------------------------------------------------------------------------------------
# the grace window — the part that is easy to get wrong
# --------------------------------------------------------------------------------------


def test_a_single_enter_does_nothing(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    change = pilot.press_enter(clock.advance(0.01))
    assert change.gesture is Gesture.PENDING
    assert pilot.state is State.RUNNING
    clock.advance(WINDOW)
    settled = pilot.tick()
    assert settled.gesture is Gesture.LONE
    assert pilot.state is State.RUNNING


def test_the_second_enter_does_not_pause_immediately(session):
    """Acting on press two is the bug: a triple would pause before resuming."""
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    pilot.press_enter(clock.advance(0.01))
    change = pilot.press_enter(clock.advance(0.05))
    assert change.gesture is Gesture.PENDING
    assert pilot.state is State.RUNNING, "the pause must wait out the window"
    assert pilot.gesture_depth == 2


def test_double_enter_pauses_once_the_window_closes(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    _double(pilot, clock)
    assert pilot.state is State.PAUSED
    assert pilot.reason is Reason.GESTURE


def test_triple_enter_resumes_without_ever_pausing(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    _double(pilot, clock)
    assert pilot.state is State.PAUSED

    seen = []
    for _ in range(3):
        seen.append(pilot.press_enter(clock.advance(0.05)).state)
    assert seen == [State.PAUSED, State.PAUSED, State.RUNNING]
    assert pilot.state is State.RUNNING


def test_triple_enter_inside_the_window_never_pauses_at_all(session):
    """The whole point of the grace window, stated directly."""
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a", "b"))
    seen = [pilot.press_enter(clock.advance(0.05)).state for _ in range(3)]
    assert State.PAUSED not in seen, "a triple must never pause on the way through"
    assert pilot.state is State.RUNNING
    clock.advance(WINDOW)
    assert pilot.tick().gesture is Gesture.NONE
    assert pilot.state is State.RUNNING


def test_three_presses_spread_wider_than_the_window_count_as_a_double(session):
    """Documented behaviour: 'three inside 500 ms' is measured from the first."""
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    pilot.press_enter(clock.advance(0.01))
    pilot.press_enter(clock.advance(0.05))
    pilot.press_enter(clock.advance(WINDOW))       # third lands after the window
    assert pilot.state is State.PAUSED
    assert pilot.reason is Reason.GESTURE


def test_a_lone_enter_long_before_the_pair_does_not_join_it(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    pilot.press_enter(clock.advance(0.01))
    clock.advance(WINDOW * 3)                      # the first window lapses
    pilot.tick()
    pilot.press_enter(clock.advance(0.01))
    pilot.press_enter(clock.advance(0.02))
    assert pilot.state is State.RUNNING, "a fresh pair must start from press one"
    clock.advance(WINDOW)
    pilot.tick()
    assert pilot.state is State.PAUSED


def test_discarding_voids_a_pending_pause(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a", "b"))
    pilot.press_enter(clock.advance(0.01))
    pilot.press_enter(clock.advance(0.05))
    pilot.discard_gesture()
    clock.advance(WINDOW)
    pilot.tick()
    assert pilot.state is State.RUNNING


def test_double_enter_while_idle_is_reported_not_applied(session):
    pilot, clock = _pilot(session)
    _double(pilot, clock)
    assert pilot.state is State.IDLE


def test_triple_enter_while_running_says_it_was_ignored(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    _triple(pilot, clock)
    assert pilot.state is State.RUNNING


# --------------------------------------------------------------------------------------
# the approval gate
# --------------------------------------------------------------------------------------


def _gated_session_steps(session) -> list[Step]:
    return [Step("apply", capability="apply_to_original", run=None,
                 background=False)]


def test_an_ask_capability_pauses_for_approval(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    assert pilot.state is State.PAUSED
    assert pilot.reason is Reason.APPROVAL
    assert pilot.pending is not None
    assert "Press Enter to approve" in pilot.pending.prompt


def test_the_approval_prompt_names_the_capability_and_the_step(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    assert "apply_to_original" in pilot.pending.message
    assert "'apply'" in pilot.pending.message


def test_one_enter_approves_and_resumes(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    change = pilot.press_enter(clock.advance(0.01))
    assert change.state is State.RUNNING
    assert pilot.pending is None
    assert pilot.completed == (), "approval resumes; the step runs on the next tick"
    pilot.tick(clock.advance(1.0))
    assert pilot.completed[0].status == "done"
    assert pilot.completed[0].confirmed is True


def test_an_answered_approval_is_not_also_counted_as_a_gesture(session):
    """A single Enter must approve, not open a grace window."""
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    pilot.press_enter(clock.advance(0.01))          # approves
    assert pilot.gesture_depth == 0
    clock.advance(WINDOW)
    settled = pilot.tick()
    assert settled.gesture is Gesture.NONE


def test_deny_skips_only_that_step(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    steps = _gated_session_steps(session) + [
        Step("after", run=None, background=False)]
    pilot.start(steps)
    pilot.tick(clock.advance(1.0))
    change = pilot.deny_pending()
    assert change.state is State.RUNNING
    assert pilot.completed[0].status == "skipped"
    assert [s.label for s in pilot.queue] == ["after"]


def test_a_blocked_capability_refuses_the_step_and_keeps_going(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.BLOCKED)
    steps = _gated_session_steps(session) + [
        Step("after", run=None, background=False)]
    pilot.start(steps)
    pilot.tick(clock.advance(1.0))
    assert pilot.completed[0].status == "refused"
    assert pilot.state is State.RUNNING
    assert "blocked" in pilot.completed[0].detail


def test_status_line_carries_the_approval_prompt(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    line = pilot.status_line()
    assert "AUTOPILOT paused" in line
    assert "Press Enter to approve" in line


def test_approving_with_nothing_pending_says_so(session):
    pilot, _clock = _pilot(session)
    change = pilot.approve_pending()
    assert "nothing is waiting" in change.note


# --------------------------------------------------------------------------------------
# failures and background dispatch
# --------------------------------------------------------------------------------------


def test_a_failing_step_pauses_and_records_why(session):
    def boom() -> str:
        raise RuntimeError("disk on fire")

    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start([Step("explode", run=boom, background=False)])
    pilot.tick(clock.advance(1.0))
    assert pilot.state is State.PAUSED
    assert pilot.reason is Reason.FAILED
    assert "disk on fire" in pilot.completed[0].detail


def test_a_background_step_is_dispatched_then_closed(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start([Step("slow", run=lambda: "finished quietly", background=True)])
    change = pilot.tick(clock.advance(1.0))
    assert change.dispatch is not None
    assert change.dispatch.label == "slow"
    assert pilot.progress == (0, 1), "still in flight, not completed"

    closed = pilot.finish_step(ok=True, detail="finished quietly")
    assert closed.state is State.IDLE
    assert pilot.completed[0].status == "done"


def test_a_dispatched_step_is_not_handed_back_again(session):
    """The bug this guards: leaving a dispatched step queued loops forever."""
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start([Step("slow", run=lambda: "x", background=True)])
    first = pilot.tick(clock.advance(1.0))
    assert first.dispatch is not None
    pilot.finish_step(ok=True, detail="x")
    assert pilot.tick(clock.advance(1.0)).dispatch is None


def test_a_worker_failure_is_reported_not_swallowed(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start([Step("slow", run=lambda: "x", background=True)])
    pilot.tick(clock.advance(1.0))
    change = pilot.finish_step(ok=False, detail="OSError: gone")
    assert change.state is State.PAUSED
    assert "OSError" in pilot.completed[0].detail


def test_a_stray_step_report_is_called_out(session):
    pilot, _clock = _pilot(session)
    change = pilot.finish_step(ok=True, detail="nobody asked")
    assert "no step was in flight" in change.note


# --------------------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------------------


def test_every_transition_is_audited(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    _double(pilot, clock)
    _triple(pilot, clock)
    pilot.stop()
    actions = _audit_actions(session)
    for expected in ("autopilot.start", "autopilot.pause", "autopilot.resume",
                     "autopilot.stop"):
        assert expected in actions, expected


def test_step_outcomes_are_audited_with_their_status(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    pilot.tick(clock.advance(1.0))
    assert "autopilot.step.done" in _audit_actions(session)


def test_a_failed_step_is_audited_as_a_warning(session):
    def boom() -> str:
        raise RuntimeError("no")

    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start([Step("explode", run=boom, background=False)])
    pilot.tick(clock.advance(1.0))
    from pyfix360.db.models import AuditEventRow

    session.flush()
    risky = {row.action: row.risk
             for row in session.query(AuditEventRow).all()
             if row.action.startswith("autopilot")}
    assert risky["autopilot.step.failed"] == "warn"


def test_approval_decisions_are_audited(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL,
         access_apply_to_original=AccessLevel.ASK)
    pilot.start(_gated_session_steps(session))
    pilot.tick(clock.advance(1.0))
    pilot.approve_pending()
    actions = _audit_actions(session)
    assert "autopilot.approve" in actions


def test_events_are_kept_for_the_ui_log(session):
    pilot, clock = _pilot(session)
    _set(session, access_autopilot=AccessLevel.FULL)
    pilot.start(_steps("a"))
    _double(pilot, clock)
    kinds = [e.kind for e in pilot.events]
    assert "start" in kinds
    assert "pause" in kinds


# --------------------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------------------


def test_gesture_window_defaults_to_half_a_second():
    assert autopilot_service.DEFAULT_WINDOW_S == 0.5
    assert settings_service.DEFAULTS["autopilot_gesture_window_ms"] == "500"


def test_gesture_window_round_trips_through_the_settings_store(session):
    _set(session, autopilot_gesture_window_ms=900)
    reloaded = settings_service.get_settings(session)
    assert reloaded.autopilot_gesture_window_ms == 900


def test_autopilot_starts_behind_a_confirmation_gate_by_default():
    defaults = AppSettings()
    assert defaults.access_autopilot is AccessLevel.ASK
    assert settings_service.DEFAULTS["access_autopilot"] == "ask"


def test_the_preset_pipeline_reaches_a_real_approval_gate(session):
    """The shipped queue must contain an ASK step, or the gate is theoretical."""
    steps = autopilot_service.pipeline_steps(1, session_factory=_factory(session))
    gated = [s for s in steps if s.capability]
    assert gated, "pipeline steps carry no capability"
    _set(session, access_autopilot=AccessLevel.FULL)
    ask = [s for s in gated
           if settings_service.access_level(session, s.capability) is AccessLevel.ASK]
    assert ask, "no shipped step is gated at ASK by default"


# --------------------------------------------------------------------------------------
# the key filter — which widget an Enter belongs to
# --------------------------------------------------------------------------------------


def _enter(widget, qapp, *, repeat: bool = False):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from pyfix360.ui.autopilot import EnterGestureFilter

    filt = EnterGestureFilter()
    seen: list[str] = []
    filt.enter_pressed.connect(lambda: seen.append("enter"))
    filt.typing_interrupted.connect(lambda: seen.append("typing"))
    filt.escape_pressed.connect(lambda: seen.append("escape"))
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return,
                      Qt.KeyboardModifier.NoModifier, "", repeat)
    filt.eventFilter(widget, event)
    return seen


def test_enter_on_a_plain_widget_is_a_gesture(qapp):
    from PySide6.QtWidgets import QTableWidget

    assert _enter(QTableWidget(), qapp) == ["enter"]


def test_enter_after_text_is_typing_not_a_gesture(qapp):
    """The rule that stops a paragraph from pausing the run."""
    from PySide6.QtWidgets import QPlainTextEdit

    edit = QPlainTextEdit("a line of prose")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    assert _enter(edit, qapp) == ["typing"]


def test_enter_on_a_blank_line_is_a_gesture(qapp):
    """A deliberate empty line under the cursor is an instruction, not prose."""
    from PySide6.QtWidgets import QPlainTextEdit

    edit = QPlainTextEdit("some finished thought\n")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    assert _enter(edit, qapp) == ["enter"]


def test_enter_in_a_non_empty_line_edit_is_not_a_gesture(qapp):
    from PySide6.QtWidgets import QLineEdit

    assert _enter(QLineEdit("search term"), qapp) == ["typing"]
    assert _enter(QLineEdit("   "), qapp) == ["enter"]


def test_enter_on_a_button_belongs_to_the_button(qapp):
    from PySide6.QtWidgets import QPushButton

    assert _enter(QPushButton("Send"), qapp) == ["typing"]


def test_a_held_enter_is_not_a_burst_of_presses(qapp):
    """Auto-repeat must not read as a triple."""
    from PySide6.QtWidgets import QTableWidget

    assert _enter(QTableWidget(), qapp, repeat=True) == []


def test_escape_is_reported_separately(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QTableWidget

    from pyfix360.ui.autopilot import EnterGestureFilter

    filt = EnterGestureFilter()
    seen: list[str] = []
    filt.escape_pressed.connect(lambda: seen.append("escape"))
    filt.enter_pressed.connect(lambda: seen.append("enter"))
    filt.eventFilter(QTableWidget(), QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert seen == ["escape"]


def test_a_press_inside_a_modal_dialog_is_not_a_gesture(qapp):
    """There Enter presses the default button; it must not also drive a run."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QDialog, QPushButton

    from pyfix360.ui.autopilot import EnterGestureFilter

    dialog = QDialog()
    dialog.setModal(True)
    filt = EnterGestureFilter()
    seen: list[str] = []
    filt.enter_pressed.connect(lambda: seen.append("enter"))
    filt.escape_pressed.connect(lambda: seen.append("escape"))
    filt.eventFilter(QPushButton("OK", dialog), QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert seen == []


def test_a_receiver_that_is_not_a_widget_is_read_through_its_focus(
        qapp):
    """The application object gets key events too and only exposes ``focusWidget``.

    With nothing focused the press has no owner, so it is treated as a gesture on
    the window rather than dropped — the operator meant it.
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from pyfix360.ui.autopilot import EnterGestureFilter

    filt = EnterGestureFilter()
    seen: list[str] = []
    filt.enter_pressed.connect(lambda: seen.append("enter"))
    filt.eventFilter(qapp, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert seen == ["enter"]


def test_the_filter_never_consumes_the_key(qapp):
    """Consuming Enter would break the widget the operator is typing in."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QPlainTextEdit

    from pyfix360.ui.autopilot import EnterGestureFilter

    filt = EnterGestureFilter()
    edit = QPlainTextEdit("text\n")
    handled = filt.eventFilter(edit, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert handled is False
