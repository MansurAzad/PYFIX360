"""Autopilot screen — start a run, watch it, and be able to stop it instantly.

The screen is a *window onto* the state machine in
:mod:`pyfix360.services.autopilot_service`; it owns no logic of its own. That
matters because the machine is also driven by the Enter gesture from anywhere in
the app: the queue can change while this page is not visible, so every widget is
rebuilt from the machine's current truth on each ``changed`` signal rather than
updated incrementally.

Approval is the one place the screen can act as a second entrance: the same
step an Enter approves has Approve and Skip buttons here, because a keyboard
gesture is a poor affordance for someone who cannot see the prompt.

The background queue at the bottom is a different process's work — a host that
keeps running after this window closes. Its rows are re-read on a timer because
nothing in this application tells us when that process writes one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pyfix360.models import AccessLevel, JobStatus
from pyfix360.services import autopilot_service, host_service, job_service
from pyfix360.ui.context import AppContext
from pyfix360.ui.widgets import (
    Card,
    current_theme,
    fill_table,
    make_button,
    make_table,
    page_header,
    scrollable,
)

State = autopilot_service.State

#: How often the background table re-reads rows another process writes.
_QUEUE_POLL_MS = 2000

#: What the operator is told the gesture does, in the order they need it.
RULES = [
    "Double Enter pauses a running queue.",
    "Triple Enter resumes it — the pause only happens once the window closes, "
    "so a triple never stutters.",
    "A single Enter answers an approval prompt; Esc skips that one step.",
    "Enters you type into a text field, and any key inside a dialog, are not "
    "gestures.",
]


class AutopilotScreen(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.controller = ctx.autopilot
        self.pilot = self.controller.pilot

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        lay.addWidget(page_header(
            "Autopilot",
            "Run the pipeline unattended and stop it the instant you disagree. "
            "Every step, pause and refusal is written to the audit trail, so an "
            "unattended run is never an unaccountable one.",
        ))

        lay.addWidget(self._state_card())
        lay.addWidget(self._approval_card())
        lay.addLayout(self._controls())

        split = QSplitter(Qt.Vertical)
        split.addWidget(self._queue_panel())
        split.addWidget(self._events_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        # A live queue is the one thing this page must never clip, and the
        # explainer below it is tall enough to squeeze it out on a laptop window.
        split.setMinimumHeight(400)
        lay.addWidget(split, 1)
        lay.addWidget(self._background_card())
        lay.addWidget(self._gesture_card())
        outer.addWidget(scrollable(inner))

        self.controller.changed.connect(self.refresh)
        self.ctx.project_changed.connect(lambda _pid: self.refresh())
        self._queue_timer = QTimer(self)
        self._queue_timer.timeout.connect(self._fill_jobs)
        self._queue_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._queue_timer.start(_QUEUE_POLL_MS)
        self.refresh()

    # ------------------------------------------------------------------ build
    def _state_card(self) -> Card:
        card = Card("Run state")
        self.state_line = QLabel("idle")
        self.state_line.setObjectName("sectionTitle")
        self.state_line.setWordWrap(True)
        card.add(self.state_line)
        self.progress_line = QLabel("")
        self.progress_line.setObjectName("muted")
        self.progress_line.setWordWrap(True)
        card.add(self.progress_line)
        return card

    def _approval_card(self) -> Card:
        card = Card("Waiting for you")
        self._approval = card
        self.approval_prompt = QLabel("")
        self.approval_prompt.setWordWrap(True)
        card.add(self.approval_prompt)
        row = QHBoxLayout()
        row.addWidget(make_button("Approve (Enter)", self._approve))
        row.addWidget(make_button("Skip this step (Esc)", self._skip,
                                  secondary=True))
        card.body.addLayout(row)
        card.setVisible(False)
        return card

    def _gesture_card(self) -> Card:
        card = Card("How to drive it without looking away")
        for rule in RULES:
            label = QLabel(f"• {rule}")
            label.setObjectName("muted")
            label.setWordWrap(True)
            card.add(label)
        self.window_line = QLabel("")
        self.window_line.setObjectName("muted")
        card.add(self.window_line)
        return card

    def _background_card(self) -> Card:
        card = Card("Background queue — work a second process carries on with")
        self.host_line = QLabel("")
        self.host_line.setObjectName("muted")
        self.host_line.setWordWrap(True)
        card.add(self.host_line)

        self.job_table = make_table(["id", "project", "status", "steps",
                                     "what it says", "updated"])
        self.job_table.setMinimumHeight(150)
        self.job_table.itemSelectionChanged.connect(self._job_selected)
        card.add(self.job_table)

        row = QHBoxLayout()
        row.addWidget(make_button("Queue a background run", self._enqueue))
        self.approve_job = make_button("Approve",
                                       lambda: self._answer_job("approve"))
        self.skip_job = make_button("Skip that step",
                                    lambda: self._answer_job("skip"),
                                    secondary=True)
        row.addWidget(self.approve_job)
        row.addWidget(self.skip_job)
        self.cancel_job = make_button("Cancel the job", self._cancel_job,
                                      danger=True)
        row.addWidget(self.cancel_job)
        row.addStretch(1)
        card.body.addLayout(row)

        note = QLabel(
            "A queued job is a row in the database: the window can close, and the "
            "host picks it up on its next poll. A step that reaches a capability "
            "set to ASK stops there and waits for you — a daemon does not get to "
            "answer for the operator. Install a host that survives logoff with "
            "`pyfix360 service install`; `pyfix360 service status` shows what is "
            "running.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        card.add(note)
        return card

    def _controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.start_button = make_button("Start unattended run", self._start)
        self.stop_button = make_button("Stop now", self._stop, danger=True)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(make_button("Refresh", self.refresh, ghost=True))
        row.addStretch(1)
        return row

    def _queue_panel(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Queue</b>"))
        self.queue_table = make_table(
            ["status", "step", "capability", "detail"])
        self.queue_table.setMinimumHeight(180)
        lay.addWidget(self.queue_table, 1)
        return page

    def _events_panel(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QLabel("<b>Session log</b> (newest last)"))
        self.event_table = make_table(["kind", "what happened"])
        self.event_table.setMinimumHeight(120)
        lay.addWidget(self.event_table, 1)
        return page

    # ------------------------------------------------------------------ state
    def _access(self) -> AccessLevel:
        from pyfix360.services import settings_service

        try:
            with self.ctx.session() as s:
                return settings_service.access_level(
                    s, autopilot_service.CAPABILITY_AUTOPILOT)
        except Exception:                         # pragma: no cover - first run
            return AccessLevel.ASK

    def refresh(self) -> None:
        theme = current_theme()
        state = self.pilot.state
        color = {State.IDLE: theme.text_muted,
                 State.RUNNING: theme.success,
                 State.PAUSED: theme.warning}[state]
        self.state_line.setText(self.pilot.status_line())
        self.state_line.setStyleSheet(f"color:{color};")
        done, total = self.pilot.progress
        self.progress_line.setText(
            f"{done} of {total} steps finished" if total else
            "No queue has been run in this session yet.")
        self.window_line.setText(
            f"Grace window: {self.pilot.window_s * 1000:.0f} ms, measured from "
            "the first Enter of the gesture.")

        pending = self.pilot.pending
        self._approval.setVisible(pending is not None)
        if pending is not None:
            self.approval_prompt.setText(pending.prompt)

        self._fill_queue()
        self._fill_events()
        self._fill_jobs()
        self.host_line.setText(self._host_line())
        self.start_button.setEnabled(state is State.IDLE)
        self.stop_button.setEnabled(state is not State.IDLE)

    def _fill_queue(self) -> None:
        colors = {"done": "#3fb950", "failed": "#f85149",
                  "refused": "#f85149", "skipped": "#d29922",
                  "running": "#58a6ff", "queued": "#8b949e",
                  "planned": "#8b949e"}
        if self.pilot.state is State.IDLE and not self.pilot.completed:
            planned = self._planned()
            levels = self._levels(planned)
            rows = []
            for step in planned:
                level = levels.get(step.capability)
                if level is AccessLevel.BLOCKED:
                    note = "blocked in Settings — would be refused"
                elif level is AccessLevel.ASK:
                    note = "asks you first"
                else:
                    note = "runs unattended"
                rows.append(["planned", step.label,
                             step.capability or "—", note])
            if not rows:
                rows = [["—", "No active project — pick one in the toolbar.",
                         "", ""]]
        else:
            in_flight = self.pilot.inflight
            steps = list(self.pilot.completed)
            if in_flight is not None:
                steps.append(in_flight)
            steps += list(self.pilot.queue)
            rows = [[s.status, s.label, s.capability or "—", s.detail or "—"]
                    for s in steps]
        fill_table(self.queue_table, rows, color_col=0, colors=colors)
        # resizeColumnsToContents sizes to the header, which clips "planned".
        self.queue_table.setColumnWidth(0, 86)

    def _fill_events(self) -> None:
        fill_table(self.event_table,
                   [[e.kind, e.note] for e in self.pilot.events])
        self.event_table.scrollToBottom()

    # ------------------------------------------------------------------ background
    def _fill_jobs(self) -> None:
        """Re-read the rows another process writes, and keep the selection."""
        selected = self._selected_job()
        try:
            with self.ctx.session() as s:
                jobs = job_service.list_jobs(s, limit=100)
        except Exception:                         # pragma: no cover - first run
            jobs = []
        rows = [[j.id, j.project_id if j.project_id is not None else "—",
                 j.status.value, f"{j.progress[0]}/{j.progress[1]}",
                 (j.detail or j.error or "—")[:90],
                 j.updated_at.strftime("%H:%M:%S") if j.updated_at else "—"]
                for j in jobs]
        colors = {"done": "#3fb950", "failed": "#f85149",
                  "cancelled": "#d29922", "interrupted": "#f85149",
                  "running": "#58a6ff", "waiting_approval": "#d29922",
                  "queued": "#8b949e"}
        fill_table(self.job_table, rows, color_col=2, colors=colors)
        self.job_table.setColumnWidth(0, 44)
        if selected is not None:
            for r, job in enumerate(jobs):
                if job.id == selected:
                    self.job_table.selectRow(r)
                    break
        self._job_selected()

    def _selected_job(self) -> int | None:
        """The id in column 0 of the selected row, whatever cell was clicked."""
        row = self.job_table.currentRow()
        item = self.job_table.item(row, 0) if row >= 0 else None
        return int(item.text()) if item is not None and item.text().isdigit() else None

    def _job_selected(self) -> None:
        """Enable only the answers a selected job can actually take."""
        job_id = self._selected_job()
        job = None
        if job_id is not None:
            with self.ctx.session() as s:
                job = job_service.get(s, job_id)
        parked = job is not None and job.status is JobStatus.WAITING_APPROVAL
        self.approve_job.setEnabled(parked)
        self.skip_job.setEnabled(parked)
        self.cancel_job.setEnabled(job is not None and job.open)

    def _host_line(self) -> str:
        pid = host_service.live_pid()
        mode = host_service.installed_mode()
        if pid is not None:
            return f"a host is running as pid {pid} (installed as '{mode}')"
        if mode:
            return (f"registered as '{mode}', but no host is running right now — "
                    "start it with `pyfix360 service start`")
        return ("no host is installed, so a queued job waits until you run "
                "`pyfix360 service install --mode task`")

    def _enqueue(self) -> None:
        if self.ctx.project_id is None:
            QMessageBox.information(
                self, "Background queue",
                "Pick an active project in the toolbar first — a background run "
                "belongs to one project.")
            return
        level = self._access()
        if level is AccessLevel.BLOCKED:
            QMessageBox.warning(
                self, "Background queue",
                "Autopilot is BLOCKED in Settings → Access Control, so an "
                "unattended run cannot be queued.")
            return
        steps = self.controller.default_steps()
        if not steps:
            QMessageBox.information(self, "Background queue",
                                    "The pipeline produced no steps.")
            return
        if level is AccessLevel.ASK and not self._confirm(steps):
            return
        with self.ctx.session() as s:
            job = job_service.enqueue(s, self.ctx.project_id, confirmed=True,
                                      note="queued from the Autopilot screen")
        self.ctx.status(f"job #{job.id} queued for the background host")
        self._fill_jobs()

    def _answer_job(self, action: str) -> None:
        job_id = self._selected_job()
        if job_id is None:
            return
        try:
            with self.ctx.session() as s:
                job = job_service.answer(s, job_id, action)
        except ValueError as exc:               # not parked, or already answered
            QMessageBox.warning(self, "Background queue", str(exc))
            self._fill_jobs()
            return
        if job is None:                         # pragma: no cover - row vanished
            return
        self.ctx.status(
            f"job #{job_id}: {action} recorded — the host applies it on its next "
            "poll, it is not instant")
        self._fill_jobs()

    def _cancel_job(self) -> None:
        job_id = self._selected_job()
        if job_id is None:
            return
        with self.ctx.session() as s:
            job = job_service.request_cancel(s, job_id)
        if job is None:                         # pragma: no cover - row vanished
            return
        self.ctx.status(
            "cancelled before it started" if job.status is JobStatus.CANCELLED
            else f"job #{job_id}: the cancel is recorded; a host mid-step finishes "
                 "that step first")
        self._fill_jobs()

    def _levels(self, planned) -> dict[str, AccessLevel]:
        """The current policy for every capability this pipeline might use."""
        from pyfix360.services import settings_service

        names = {s.capability for s in planned} - {""}
        if not names:
            return {}
        try:
            with self.ctx.session() as s:
                return {name: settings_service.access_level(s, name)
                        for name in names}
        except Exception:                         # pragma: no cover - first run
            return {}

    def _planned(self) -> list[autopilot_service.Step]:
        # Ask the controller, not the service: this preview must be built the
        # same way the run itself is, or it could advertise steps that never start.
        return self.controller.default_steps()

    # ------------------------------------------------------------------ actions
    def _start(self) -> None:
        if self.ctx.project_id is None:
            QMessageBox.information(
                self, "Autopilot",
                "Pick an active project in the toolbar first — autopilot runs "
                "the pipeline for one project at a time.")
            return
        steps = self.controller.default_steps()
        if not steps:
            QMessageBox.information(
                self, "Autopilot", "The pipeline produced no steps.")
            return
        level = self._access()
        if level is AccessLevel.BLOCKED:
            QMessageBox.warning(
                self, "Autopilot",
                "Autopilot is set to BLOCKED in Settings → Access Control, so "
                "no unattended run can start. Change it there if you want one.")
            return
        # ``approved`` is the operator's answer; the ``confirmed=True`` below is
        # the policy flag handed to the service. They are different questions.
        approved = level is AccessLevel.FULL
        if level is AccessLevel.ASK:
            approved = self._confirm(steps)
        if not approved:
            return
        try:
            self.controller.start(steps, confirmed=True)
        except Exception as exc:                  # noqa: BLE001 - surfaced
            QMessageBox.warning(self, "Autopilot", f"The run did not start: {exc}")

    def _confirm(self, steps: list[autopilot_service.Step]) -> bool:
        gated = [s for s in steps if s.capability]
        lines = [f"{i + 1}. {s.label}" for i, s in enumerate(steps)]
        if gated:
            lines.append("")
            lines.append("Steps guarded by a capability will pause and ask you "
                         "before they touch anything: " +
                         ", ".join(sorted({s.capability for s in gated})) + ".")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start autopilot?")
        box.setText("This run would do, in order:")
        box.setInformativeText("\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _stop(self) -> None:
        change = self.controller.stop()
        if change.note:
            self.ctx.status(change.note)

    def _approve(self) -> None:
        self.controller.approve()

    def _skip(self) -> None:
        self.controller.skip()
