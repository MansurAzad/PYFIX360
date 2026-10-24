"""The desktop half of autopilot: the key filter and the timers.

The state machine itself is Qt-free (:mod:`pyfix360.services.autopilot_service`).
This module supplies the two things that cannot be done without a GUI thread —
an application-wide Enter filter, and the QTimer that closes the grace window
and paces the queue.

**Why an event filter and not a ``QShortcut``.** A shortcut for ``Return`` never
fires while a ``QPlainTextEdit`` has focus: the text widget consumes the key
first, which is exactly the widget the operator is looking at when they want to
interrupt a run. An application-level filter is installed ahead of the target
widget, so it sees the press and then lets it through untouched — the filter
never consumes Enter, because stealing it would break the user's typing.

**Why Enter only counts on a blank line inside a text widget.** Two Enters in a
row is what a blank line *looks* like, so that is the signal used. Any Enter
that is inserting a newline after real text belongs to the prose, not to the
gesture, and is ignored. Without this rule, writing a paragraph with normal line
breaks would pause the run. Outside text widgets every Enter counts, so a plain
double Enter works from tables, lists and the main window.

**Why auto-repeat is dropped.** Holding Enter generates a burst of press events.
Treated as presses, a single held key would read as a triple. Only genuine,
separate presses are counted.

**Why a modal dialog swallows the gesture.** Inside a dialog, Enter already has
a job — it presses the default button. Letting the same key also pause or resume
a run would mean answering a question changed the state of something else.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from pyfix360.services import autopilot_service
from pyfix360.services.autopilot_service import Autopilot, Change, Step
from pyfix360.ui.context import AppContext
from pyfix360.ui.workers import run_async

logger = logging.getLogger(__name__)

#: How often the ticker polls the machine, as a fraction of the grace window.
_TICK_DIVISOR = 4
_MIN_TICK_MS = 40


def _enter_is_free(widget: QWidget | None) -> bool:
    """Whether an Enter press on ``widget`` is a gesture rather than input.

    ``True`` when the key is not being used for something else. This is the rule
    that keeps the gesture from corrupting typing, so the branches are ordered
    from most specific to least.
    """
    if widget is None:
        return True
    if isinstance(widget, (QComboBox, QAbstractButton)):
        return False            # Enter opens the popup or presses the button
    if isinstance(widget, QAbstractItemView):
        return True             # Enter activates a row; it is not typing
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return _cursor_line_is_blank(widget)
    if isinstance(widget, QLineEdit):
        return not widget.text().strip()
    return True


def _cursor_line_is_blank(edit: QPlainTextEdit | QTextEdit) -> bool:
    """True when the line under the cursor holds nothing but whitespace."""
    cursor = edit.textCursor()
    if cursor.hasSelection():
        return False
    return not cursor.block().text().strip()


def _inside_modal(widget: QWidget | None) -> bool:
    """True when ``widget`` belongs to a dialog that is asking something."""
    if widget is None:
        return False
    window = widget.window()
    return bool(window.isModal())


class EnterGestureFilter(QObject):
    """Watches every Enter press in the application and forwards it.

    Installed with ``QApplication.installEventFilter`` so it runs before the
    focused widget. It never returns ``True``: the key is always delivered to
    the widget that asked for it. Presses inside a modal dialog are ignored —
    there Enter already means "accept this dialog", and honouring it as a
    gesture would pause a run in the middle of answering a question.
    """

    enter_pressed = Signal()
    escape_pressed = Signal()
    typing_interrupted = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        # A held key is not a press. Without this, one long Enter reads as a triple.
        if event.isAutoRepeat():
            return False
        widget = self._target(obj)
        if _inside_modal(widget):
            # A dialog owns Enter: it presses the default button. Counting it as
            # a gesture too would pause a run while answering a question.
            return False
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if _enter_is_free(widget):
                self.enter_pressed.emit()
            else:
                # Typing a line break: it is neither a gesture nor a reset,
                # because the operator may still be finishing their text.
                self.typing_interrupted.emit()
        elif key == Qt.Key_Escape:
            self.escape_pressed.emit()
        else:
            # Any other key between two Enters means they were not a pair.
            self.typing_interrupted.emit()
        return False

    @staticmethod
    def _target(obj: QObject) -> QWidget | None:
        """The widget that would receive this key.

        An application-level filter is handed the receiver itself, which for a
        ``KeyPress`` is the focus widget. Receivers that are not widgets (the
        application object) only expose it through ``focusWidget()``, so both
        shapes are accepted.
        """
        if isinstance(obj, QWidget):
            return obj
        focus = getattr(obj, "focusWidget", None)
        if callable(focus):
            found = focus()
            return found if isinstance(found, QWidget) else None
        return None


class AutopilotController(QObject):
    """Owns the machine, the ticker and the worker dispatch."""

    changed = Signal()
    notice = Signal(str)
    approval = Signal(str)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pilot = Autopilot(
            window_s=self._window_s(),
            session_factory=ctx.session,
            project_id=ctx.project_id,
        )
        self.filter = EnterGestureFilter(self)
        self.filter.enter_pressed.connect(self._on_enter)
        self.filter.escape_pressed.connect(self._on_escape)
        self.filter.typing_interrupted.connect(self._reset_gesture)

        self._timer = QTimer(self)
        self._timer.setInterval(self._tick_ms())
        self._timer.timeout.connect(self._tick)

        ctx.project_changed.connect(self._project_changed)

    # ------------------------------------------------------------------ config
    def _window_s(self) -> float:
        from pyfix360.services import settings_service

        try:
            with self.ctx.session() as s:
                ms = settings_service.get_settings(s).autopilot_gesture_window_ms
        except Exception:                          # pragma: no cover - first run
            ms = autopilot_service.DEFAULT_WINDOW_S * 1000
        return max(0.15, float(ms)) / 1000.0

    def _tick_ms(self) -> int:
        return max(_MIN_TICK_MS, int(self.pilot.window_s * 1000 / _TICK_DIVISOR))

    def _project_changed(self, project_id) -> None:
        self.pilot.project_id = project_id

    # ------------------------------------------------------------------ install
    def install(self, app) -> None:
        """Attach the gesture filter to the application."""
        app.installEventFilter(self.filter)

    # ------------------------------------------------------------------ actions
    def start(self, steps: list[Step] | None = None, *,
              confirmed: bool = False) -> Change:
        self.pilot.window_s = self._window_s()
        self._timer.setInterval(self._tick_ms())
        steps = steps if steps is not None else self.default_steps()
        return self._apply(self.pilot.start(steps, confirmed=confirmed))

    def stop(self) -> Change:
        return self._apply(self.pilot.stop())

    def approve(self) -> Change:
        """Say yes to the outstanding approval — the same answer Enter gives."""
        if self.pilot.pending is None:
            return Change(self.pilot.state, self.pilot.reason,
                          "nothing is waiting for approval.")
        return self._apply(self.pilot.approve_pending())

    def skip(self) -> Change:
        """Say no to the outstanding approval and let the rest of the run go on."""
        if self.pilot.pending is None:
            return Change(self.pilot.state, self.pilot.reason,
                          "nothing is waiting for approval.")
        return self._apply(self.pilot.deny_pending())

    def default_steps(self) -> list[Step]:
        if self.ctx.project_id is None:
            return []
        return autopilot_service.pipeline_steps(self.ctx.project_id,
                                                session_factory=self.ctx.session)

    # ------------------------------------------------------------------ handlers
    def _on_enter(self) -> None:
        self._apply(self.pilot.press_enter())

    def _on_escape(self) -> None:
        self.skip()

    def _reset_gesture(self) -> None:
        """A keystroke that is not part of the gesture voids the presses so far."""
        if self.pilot.gesture_depth:
            self.pilot.discard_gesture()

    def _tick(self) -> None:
        self._apply(self.pilot.tick())

    # ------------------------------------------------------------------ plumbing
    def _apply(self, change: Change) -> Change:
        if change.dispatch is not None:
            self._dispatch(change.dispatch)
        if change.changed:
            self.notice.emit(change.note)
            self.ctx.status(change.note)
        if self.pilot.pending is not None:
            self.approval.emit(self.pilot.pending.prompt)
        self._sync_timer()
        self.changed.emit()
        return change

    def _sync_timer(self) -> None:
        """Run the ticker only while something is actually pending on it."""
        needs = (self.pilot.state is autopilot_service.State.RUNNING
                 or self.pilot.gesture_depth > 0)
        if needs and not self._timer.isActive():
            self._timer.start()
        elif not needs and self._timer.isActive():
            self._timer.stop()

    def _dispatch(self, step: Step) -> None:
        """Run a background step off the GUI thread and report back here."""

        def work() -> tuple[bool, str]:
            try:
                return True, step.run() if step.run is not None else ""
            except Exception as exc:               # noqa: BLE001 - surfaced, not hidden
                logger.exception("autopilot step raised: %s", step.label)
                return False, f"{type(exc).__name__}: {exc}"

        def done(result: tuple[bool, str]) -> None:
            ok, detail = result
            self._apply(self.pilot.finish_step(ok=ok, detail=detail))

        def failed(message: str) -> None:
            self._apply(self.pilot.finish_step(ok=False, detail=message))

        run_async(work, on_done=done, on_error=failed)
