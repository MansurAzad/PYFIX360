"""Autopilot UI: the wiring that makes the gesture and the badge real.

The state machine itself is covered in ``test_autopilot.py``. These tests only
ask whether the desktop half actually connects it to the application: does a key
press anywhere reach the machine, does the toolbar say what the machine needs,
and does the screen show the truth while it is running.

They use the ``data_dir`` fixture rather than ``session`` on purpose. The UI
opens its own sessions and commits; borrowing a fixture session would nest a
second transaction against it and lock the database.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QWidget,
)

from pyfix360.db.models import ProjectRow
from pyfix360.models import AccessLevel
from pyfix360.services import settings_service
from pyfix360.services.autopilot_service import Reason, State, Step
from pyfix360.ui.context import AppContext
from pyfix360.ui.main_window import MainWindow
from pyfix360.ui.screens.autopilot import AutopilotScreen


@pytest.fixture()
def ctx(data_dir):
    return AppContext()


@pytest.fixture()
def project(ctx, tmp_path) -> int:
    with ctx.session() as s:
        row = ProjectRow(name="demo", root_path=str(tmp_path))
        s.add(row)
        s.flush()
        pid = row.id
    ctx.set_project(pid)
    return pid


def _press(app, widget) -> None:
    """Deliver one genuine Enter press through the application's notify chain."""
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Return,
                      Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget, event)


def _run(controller, *steps: Step) -> None:
    """Start the machine on exactly these steps, with no pacing delay."""
    controller.pilot.step_interval_s = 0.0
    controller.start(list(steps), confirmed=True)


def _cell(widget, row: int, col: int) -> str:
    item = widget.item(row, col)
    return "" if item is None else item.text()


def _rows(table) -> list[list[str]]:
    return [[_cell(table, r, c) for c in range(table.columnCount())]
            for r in range(table.rowCount())]


# --------------------------------------------------------------------------------------
# the filter is on the application, not on a widget
# --------------------------------------------------------------------------------------


def test_the_context_hands_every_caller_the_same_controller(ctx, qapp):
    assert ctx.autopilot is ctx.autopilot


def test_a_key_press_on_any_widget_reaches_the_machine(ctx, qapp):
    """The point of an app-level filter: focus could be anywhere.

    A ``QShortcut`` would not fire for the text edit at all, which is the
    failure this wiring exists to prevent.
    """
    controller = ctx.autopilot
    for widget in (QTableWidget(), QPlainTextEdit(), QLineEdit(), QWidget()):
        _press(qapp, widget)
        assert controller.pilot.gesture_depth == 1, widget
        controller.pilot.discard_gesture()


def test_typing_into_a_field_does_not_count_as_a_gesture(ctx, qapp):
    controller = ctx.autopilot
    edit = QPlainTextEdit("a sentence")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    _press(qapp, edit)
    assert controller.pilot.gesture_depth == 0


def test_the_key_still_reaches_the_widget_it_was_sent_to(ctx, qapp):
    """Watching must not consume: the newline the operator asked for appears."""
    controller = ctx.autopilot
    edit = QPlainTextEdit("a sentence")
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    _press(qapp, edit)
    assert edit.toPlainText() == "a sentence\n"
    controller.pilot.discard_gesture()


def test_a_press_inside_a_dialog_belongs_to_the_dialog(ctx, qapp):
    from PySide6.QtWidgets import QDialog

    controller = ctx.autopilot
    dialog = QDialog()
    dialog.setModal(True)
    _press(qapp, QPushButton("OK", dialog))
    assert controller.pilot.gesture_depth == 0


def test_two_presses_quickly_do_not_pause_yet(ctx, qapp, project):
    """The grace window survives the wiring: no pause until the window closes."""
    controller = ctx.autopilot
    _run(controller, Step("one"), Step("two"))
    _press(qapp, QTableWidget())
    _press(qapp, QTableWidget())
    assert controller.pilot.gesture_depth == 2
    assert controller.pilot.state is State.RUNNING
    controller.pilot.window_s = 0.0
    controller._tick()
    assert controller.pilot.state is State.PAUSED
    assert controller.pilot.reason is Reason.GESTURE


# --------------------------------------------------------------------------------------
# the toolbar badge
# --------------------------------------------------------------------------------------


def test_the_badge_names_the_keypress_wanted_now(ctx, qapp, project):
    from pyfix360.services.autopilot_service import PendingApproval

    window = MainWindow(ctx)
    window._refresh_autopilot_badge()
    assert window.autopilot_badge.text() == "autopilot: idle"

    pilot = window.autopilot.pilot
    pilot.load([Step("x", capability="apply_to_original")])
    pilot._pending = PendingApproval(pilot.queue[0], "apply_to_original",
                                     "needs approval")
    window._refresh_autopilot_badge()
    assert window.autopilot_badge.text() == "press Enter to approve"
    assert "Press Enter to approve" in window.autopilot_badge.toolTip()


def test_the_badge_counts_steps_while_running(ctx, qapp, project):
    window = MainWindow(ctx)
    _run(window.autopilot, Step("one"), Step("two"), Step("three"))
    window._refresh_autopilot_badge()
    assert window.autopilot_badge.text() == "autopilot: running 0/3"
    window.autopilot.stop()
    window._refresh_autopilot_badge()
    assert window.autopilot_badge.text() == "autopilot: idle"


def test_the_badge_follows_the_machine_without_a_page_being_open(ctx, qapp, project):
    """``changed`` is what repaints it — the gesture can fire from any screen."""
    window = MainWindow(ctx)
    seen: list[str] = []
    window.autopilot.changed.connect(
        lambda: seen.append(window.autopilot_badge.text()))
    _run(window.autopilot, Step("one"), Step("two"))
    assert seen, "the badge never repainted when the run started"


# --------------------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------------------


def test_the_idle_screen_previews_the_pipeline_with_its_gates(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    planned = _rows(screen.queue_table)
    assert [row[1] for row in planned] == [
        "Scan the project",
        "Run the test suite",
        "Apply the newest repair plan to the original",
        "Evaluate quality gates",
        "Export a report",
    ]
    notes = {row[1]: row[3] for row in planned}
    assert notes["Scan the project"] == "runs unattended"
    assert notes["Apply the newest repair plan to the original"] == "asks you first"


def test_the_preview_relabels_a_blocked_capability(ctx, qapp, project):
    with ctx.session() as s:
        settings = settings_service.get_settings(s)
        settings.access_export_reports = AccessLevel.BLOCKED
        settings_service.save_settings(s, settings,
                                       keys=["access_export_reports"])
    screen = AutopilotScreen(ctx)
    notes = {row[1]: row[3] for row in _rows(screen.queue_table)}
    assert notes["Export a report"] == "blocked in Settings — would be refused"


def test_a_dispatched_step_shows_as_running_then_closes(ctx, qapp, project):
    """The worker reports back on the GUI thread; the screen must not lie first."""
    from PySide6.QtCore import QThreadPool

    screen = AutopilotScreen(ctx)
    _run(screen.controller, Step("first", run=lambda: "ok"), Step("second"))
    screen.controller._tick()
    assert screen.pilot.inflight is not None
    screen.refresh()
    assert "running" in [row[0] for row in _rows(screen.queue_table)]

    QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()                    # the completion callback lands here
    assert screen.pilot.inflight is None
    assert {s.label: s.status for s in screen.pilot.completed}[
        "first"] == "done"


def test_the_approval_card_offers_the_same_answer_as_enter(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    gated = Step("Write to the original", capability="apply_to_original",
                 background=False, run=lambda: "applied")
    plain = Step("Scan", background=False, run=lambda: "scanned")
    _run(screen.controller, plain, gated)
    screen.controller._tick()          # the scan runs inline
    screen.controller._tick()          # the gated step reaches the ASK gate
    assert screen.pilot.state is State.PAUSED
    assert not screen._approval.isHidden()
    assert "apply_to_original" in screen.approval_prompt.text()

    screen._approve()
    screen.controller._tick()
    assert screen._approval.isHidden()
    assert {s.label: s.status for s in screen.pilot.completed}[
        "Write to the original"] == "done"


def test_the_skip_button_drops_only_the_gated_step(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    gated = Step("Write to the original", capability="apply_to_original",
                 background=False, run=lambda: "should not run")
    after = Step("Evaluate gates", background=False, run=lambda: "gates")
    _run(screen.controller, gated, after)
    screen.controller._tick()
    assert screen.pilot.pending is not None
    screen._skip()
    screen.controller._tick()
    statuses = {s.label: s.status for s in screen.pilot.completed}
    assert statuses["Write to the original"] == "skipped"
    assert statuses["Evaluate gates"] == "done"


def test_the_stop_button_abandons_the_run(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    _run(screen.controller, Step("one"), Step("two"))
    screen._stop()
    screen.refresh()
    assert screen.pilot.state is State.IDLE
    assert not screen.stop_button.isEnabled()
    assert screen.start_button.isEnabled()


def test_the_screen_renders_the_session_log(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    _run(screen.controller, Step("one"), Step("two"))
    screen.controller._tick()
    screen.refresh()
    assert "start" in [row[0] for row in _rows(screen.event_table)]


def test_the_state_line_is_what_the_machine_reports(ctx, qapp, project):
    screen = AutopilotScreen(ctx)
    _run(screen.controller, Step("one"), Step("two"))
    screen.refresh()
    assert screen.state_line.text() == screen.pilot.status_line()


def test_approve_without_a_pending_answer_is_silent(ctx, qapp, project):
    """Esc on a clear queue must not spam the status bar."""
    messages: list[str] = []
    ctx.status_message.connect(messages.append)
    screen = AutopilotScreen(ctx)
    screen._approve()
    screen._skip()
    assert messages == []


# --------------------------------------------------------------------------------------
# the callback has to survive the caller forgetting the worker
# --------------------------------------------------------------------------------------


def _settle(qapp) -> None:
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()


def test_a_forgotten_worker_still_delivers_its_result(qapp):
    """Autopilot dispatches steps and keeps no reference, like every screen does.

    ``setAutoDelete(True)`` frees the runnable the moment it returns, and with it
    the ``WorkerSignals`` carrying the connection — so a forgotten worker emitted
    its result into nothing and the run hung with a step forever "in flight".
    """
    from pyfix360.ui import workers

    hits: list = []
    worker = workers.run_async(lambda: "value", on_done=hits.append,
                               on_error=lambda m: hits.append(("err", m)))
    del worker
    _settle(qapp)
    assert hits == ["value"]
    assert workers._inflight == set()


def test_a_forgotten_worker_still_reports_its_failure(qapp):
    from pyfix360.ui import workers

    def explode() -> str:
        raise RuntimeError("boom")

    hits: list = []
    worker = workers.run_async(explode, on_done=hits.append,
                               on_error=hits.append)
    del worker
    _settle(qapp)
    assert hits and hits[0].startswith("RuntimeError: boom")
    assert workers._inflight == set()
