"""The desktop half of background mode: the tray icon and the window's exit.

These tests answer one question with real widgets: can a user close the window
and still reach the application again? The Qt-free host is covered in
``test_background_host.py``.

The notification area is a desktop feature a test runner may not have, so the
tests that care about it say so explicitly — they patch
``QSystemTrayIcon.isSystemTrayAvailable`` instead of trusting whatever machine is
running pytest.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from pyfix360.db.models import BackgroundJobRow, ProjectRow
from pyfix360.models import AccessLevel, AppSettings, JobStatus
from pyfix360.services import host_service, job_service, settings_service
from pyfix360.services.autopilot_service import Step
from pyfix360.ui.context import AppContext
from pyfix360.ui.main_window import MainWindow
from pyfix360.ui.screens.autopilot import AutopilotScreen
from pyfix360.ui.screens.settings import SettingsScreen
from pyfix360.ui.tray import TrayController

TRAY_PRESENT = "isSystemTrayAvailable"


@pytest.fixture()
def ctx(data_dir):
    return AppContext()


@pytest.fixture()
def tray(ctx, monkeypatch, qapp):
    """A controller on a desktop that definitely has a notification area."""
    monkeypatch.setattr(QSystemTrayIcon, TRAY_PRESENT, staticmethod(lambda: True))
    return TrayController(ctx)


def _rows(menu: QMenu) -> list[tuple[str, bool]]:
    """(label, enabled) for every non-separator row."""
    return [(a.text(), a.isEnabled()) for a in menu.actions() if not a.isSeparator()]


def _parked(ctx, job_id: int) -> None:
    """Put a row into the state a host writes when it hits an ASK gate."""
    with ctx.session() as s:
        row = s.get(BackgroundJobRow, job_id)
        row.status = JobStatus.WAITING_APPROVAL.value
        row.detail = "needs you"


# --------------------------------------------------------------------------------------
# when hiding the window is allowed
# --------------------------------------------------------------------------------------


def test_the_tray_offers_to_hide_when_the_desktop_can_show_it(tray, ctx):
    assert tray.available and tray.should_hide_on_close


def test_a_desktop_without_a_notification_area_never_hides(ctx, monkeypatch, qapp):
    """The whole point of the guard: no icon means no way back.

    Without it, turning "close hides to tray" on in a headless session would
    leave a running application nobody can reach or stop.
    """
    monkeypatch.setattr(QSystemTrayIcon, TRAY_PRESENT, staticmethod(lambda: False))
    controller = TrayController(ctx)
    assert not controller.available
    assert not controller.should_hide_on_close
    assert controller.show() is False


def test_turning_the_icon_off_stops_the_window_from_hiding(tray, ctx):
    ctx.config.tray_enabled = False
    assert not tray.enabled and not tray.should_hide_on_close


def test_a_user_who_wants_close_to_quit_gets_close_to_quit(tray, ctx):
    ctx.config.close_hides_to_tray = False
    assert tray.enabled and not tray.should_hide_on_close


def test_the_settings_are_persisted_and_not_only_runtime(ctx, qapp):
    """The runtime config is a mirror; the database is what survives a restart."""
    with ctx.session() as s:
        settings_service.save_settings(
            s, AppSettings(tray_enabled=False, close_hides_to_tray=False,
                          service_poll_ms=7500))
    with ctx.session() as s:
        saved = settings_service.get_settings(s)
    assert (saved.tray_enabled, saved.close_hides_to_tray,
            saved.service_poll_ms) == (False, False, 7500)
    assert ctx.config.service_poll_ms == 7500      # applied on save


# --------------------------------------------------------------------------------------
# what the menu says
# --------------------------------------------------------------------------------------


def test_the_menu_describes_a_running_host_and_offers_only_stop(tray, monkeypatch):
    monkeypatch.setattr(host_service, "live_pid", lambda: 4321)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "task")
    menu = QMenu()
    tray.build_menu(menu)
    labels = dict(_rows(menu))
    line = "Background host: running (pid 4321)"
    assert line in labels
    assert labels[line] is False          # a state row, not a button
    assert "Stop the background host" in labels
    assert "Start the background host" not in labels
    assert menu.actions()[0].text().startswith("Open")
    assert menu.actions()[-1].text().startswith("Quit")


def test_the_menu_describes_an_idle_host_and_offers_start(tray, monkeypatch):
    monkeypatch.setattr(host_service, "live_pid", lambda: None)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "")
    menu = QMenu()
    tray.build_menu(menu)
    labels = dict(_rows(menu))
    assert "Background host: not running" in labels
    assert "Start the background host" in labels
    assert "Stop the background host" not in labels
    assert menu.actions()[-1].text().startswith("Quit")


def test_the_menu_counts_the_queue_written_by_another_process(tray, ctx):
    """The queue belongs to the host; the menu only reads the row.

    A job parked at an ASK gate is the one thing an operator must not lose sight
    of while the window is closed, so it is named out loud.
    """
    with ctx.session() as s:
        row = ProjectRow(name="demo", root_path=str(ctx.config.data_dir))
        s.add(row)
        s.flush()
        pid = row.id
        first = job_service.enqueue(s, pid, confirmed=True)
        job_service.enqueue(s, pid, confirmed=True)

    menu = QMenu()
    tray.build_menu(menu)
    assert [text for text, _ in _rows(menu) if "job(s)" in text] \
        == ["2 job(s) on the queue"]

    _parked(ctx, first.id)
    menu = QMenu()
    tray.build_menu(menu)
    assert [text for text, _ in _rows(menu) if "job(s)" in text] \
        == ["2 job(s) on the queue, 1 waiting for you"]


def test_a_state_read_that_raises_still_leaves_the_menu_open(tray, monkeypatch):
    """Losing a status line must not cost the user their only Quit action."""
    monkeypatch.setattr(host_service, "live_pid",
                        lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    menu = QMenu()
    tray._rebuild()                       # must not raise into a click
    assert not menu.actions()             # and must not open half a menu

    monkeypatch.setattr(tray, "job_counts", lambda: {})
    monkeypatch.setattr(host_service, "live_pid", lambda: None)
    tray.build_menu(menu)
    assert menu.actions()[-1].text().startswith("Quit")
    assert [text for text, _ in _rows(menu) if "job(s)" in text] \
        == ["0 job(s) on the queue"]


# --------------------------------------------------------------------------------------
# the window's close decision
# --------------------------------------------------------------------------------------


class _NoopSignal:
    def connect(self, _slot) -> None:
        pass


class _StubTray:
    """Only what :meth:`MainWindow.closeEvent` and ``set_tray`` ask of a tray."""

    restore_window = _NoopSignal()
    show_autopilot = _NoopSignal()
    quit_application = _NoopSignal()

    def __init__(self, hides: bool) -> None:
        self.should_hide_on_close = hides
        self.hidden = 0
        self.explained = 0

    def hide(self) -> None:
        self.hidden += 1

    def explain_hide(self) -> None:
        self.explained += 1

    def sync_to_settings(self) -> None:
        pass


def _window(ctx, qapp, hides: bool) -> tuple[MainWindow, _StubTray]:
    window = MainWindow(ctx)
    window.show()
    stub = _StubTray(hides)
    window.set_tray(stub)
    return window, stub


def test_closing_hides_when_the_tray_can_bring_it_back(ctx, qapp):
    window, stub = _window(ctx, qapp, hides=True)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window.isVisible()
    assert stub.explained == 1


def test_closing_quits_when_the_tray_cannot(ctx, qapp):
    window, stub = _window(ctx, qapp, hides=False)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert stub.explained == 0


def test_quitting_from_the_menu_ends_even_a_hiding_window(ctx, qapp):
    """The one action that must never hide, whatever the settings say."""
    window, stub = _window(ctx, qapp, hides=True)
    window.quit_application()
    assert stub.hidden == 1
    assert not window.isVisible()
    assert window._quitting is True


def test_a_saved_setting_moves_the_icon_without_a_restart(tray, ctx, monkeypatch):
    """The Settings Center writes the config and broadcasts; the tray obeys."""
    assert tray.show() is True
    monkeypatch.setattr(ctx.config, "tray_enabled", False)
    ctx.notify_changed()
    assert tray._icon.isVisible() is False
    monkeypatch.setattr(ctx.config, "tray_enabled", True)
    tray.sync_to_settings()
    assert tray._icon.isVisible() is True


# --------------------------------------------------------------------------------------
# the Settings Center tab
# --------------------------------------------------------------------------------------


def test_the_background_tab_round_trips_its_three_controls(ctx, qapp):
    screen = SettingsScreen(ctx)
    with ctx.session() as s:
        st = settings_service.get_settings(s)
    screen.tray_enabled.setChecked(False)
    screen.close_to_tray.setChecked(False)
    screen.service_poll_ms.setValue(1500)
    collected = screen._collect(st)
    assert (collected.tray_enabled, collected.close_hides_to_tray,
            collected.service_poll_ms) == (False, False, 1500)

    screen.refresh()
    assert screen.tray_enabled.isChecked() is True      # defaults, not what was typed
    assert screen.service_poll_ms.value() == 2000


def test_the_tab_reports_the_host_another_process_installed(ctx, qapp, monkeypatch):
    monkeypatch.setattr(host_service, "live_pid", lambda: 99)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "nssm")
    screen = SettingsScreen(ctx)
    assert "pid 99" in screen.host_state.text()
    assert "nssm" in screen.host_state.text()

    monkeypatch.setattr(host_service, "live_pid", lambda: None)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "")
    screen._refresh_host_state()
    assert "nothing is registered" in screen.host_state.text()


# --------------------------------------------------------------------------------------
# the background queue card
# --------------------------------------------------------------------------------------


@pytest.fixture()
def project(ctx) -> int:
    with ctx.session() as s:
        row = ProjectRow(name="demo", root_path=str(ctx.config.data_dir))
        s.add(row)
        s.flush()
        pid = row.id
    ctx.set_project(pid)
    return pid


def _table(table) -> list[list[str]]:
    return [["" if table.item(r, c) is None else table.item(r, c).text()
             for c in range(table.columnCount())]
            for r in range(table.rowCount())]


def _park(ctx, job_id: int, status: JobStatus) -> None:
    """Write the state a host would write, without running a host."""
    with ctx.session() as s:
        s.get(BackgroundJobRow, job_id).status = status.value


def _select(screen, job_id: int) -> None:
    rows = _table(screen.job_table)
    screen.job_table.selectRow(next(r for r, row in enumerate(rows)
                                    if row[0] == str(job_id)))


def test_the_card_lists_jobs_a_different_process_writes(ctx, qapp, project):
    with ctx.session() as s:
        job = job_service.enqueue(s, project, confirmed=True, note="for the host")
    screen = AutopilotScreen(ctx)
    row = _table(screen.job_table)[0]
    assert row[0] == str(job.id)
    assert row[2] == "queued"
    assert "for the host" in row[4]


def test_the_card_names_the_host_without_inventing_one(ctx, qapp, project,
                                                     monkeypatch):
    monkeypatch.setattr(host_service, "live_pid", lambda: None)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "")
    screen = AutopilotScreen(ctx)
    assert "no host is installed" in screen.host_line.text()

    monkeypatch.setattr(host_service, "live_pid", lambda: 7)
    monkeypatch.setattr(host_service, "installed_mode", lambda: "service")
    screen.refresh()
    assert "running as pid 7" in screen.host_line.text()
    assert "'service'" in screen.host_line.text()


def test_only_a_parked_job_can_be_answered(ctx, qapp, project):
    with ctx.session() as s:
        queued = job_service.enqueue(s, project, confirmed=True)
        parked = job_service.enqueue(s, project, confirmed=True)
    _park(ctx, parked.id, JobStatus.WAITING_APPROVAL)
    screen = AutopilotScreen(ctx)

    _select(screen, queued.id)
    assert not screen.approve_job.isEnabled()
    assert not screen.skip_job.isEnabled()
    assert screen.cancel_job.isEnabled()      # open, so still worth cancelling

    _select(screen, parked.id)
    assert screen.approve_job.isEnabled()
    assert screen.skip_job.isEnabled()


def test_answering_from_the_card_is_recorded_not_applied(ctx, qapp, project):
    """The host reads the answer on its next poll; the card has to say so."""
    with ctx.session() as s:
        job = job_service.enqueue(s, project, confirmed=True)
    _park(ctx, job.id, JobStatus.WAITING_APPROVAL)
    screen = AutopilotScreen(ctx)
    _select(screen, job.id)
    said: list[str] = []
    screen.ctx.status_message.connect(said.append)
    screen._answer_job("approve")
    with ctx.session() as s:
        assert s.get(BackgroundJobRow, job.id).resume_action == "approve"
    assert [line for line in said if "next poll" in line and "not instant" in line]


def test_a_stale_answer_click_is_shown_instead_of_raising(ctx, qapp, project,
                                                         monkeypatch):
    """Answering a job the host already moved on from is refused, not swallowed."""
    with ctx.session() as s:
        job = job_service.enqueue(s, project, confirmed=True)
    screen = AutopilotScreen(ctx)
    _select(screen, job.id)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    screen._answer_job("approve")             # queued, so job_service refuses
    assert shown and "not waiting for approval" in shown[0]


def test_cancelling_a_queued_job_ends_it_and_a_live_one_only_agrees(
        ctx, qapp, project):
    with ctx.session() as s:
        queued = job_service.enqueue(s, project, confirmed=True)
        running = job_service.enqueue(s, project, confirmed=True)
    _park(ctx, running.id, JobStatus.RUNNING)
    screen = AutopilotScreen(ctx)

    _select(screen, queued.id)
    screen._cancel_job()
    with ctx.session() as s:
        assert s.get(BackgroundJobRow, queued.id).status == "cancelled"

    _select(screen, running.id)
    screen._cancel_job()
    with ctx.session() as s:
        row = s.get(BackgroundJobRow, running.id)
        assert row.status == "running"        # a host is mid-step; only it can stop
        assert row.cancel_requested is True


def test_queueing_from_the_card_carries_the_confirmation_forward(
        ctx, qapp, project, monkeypatch):
    """``confirmed`` travels in the row, because the host is another process.

    With the capability at ASK and the operator declining the dialog, nothing is
    queued at all — a declined answer must not become a silent yes later.
    """
    screen = AutopilotScreen(ctx)
    monkeypatch.setattr(screen.controller, "default_steps",
                        lambda: [Step(label="Scan the project")])
    with ctx.session() as s:
        st = settings_service.get_settings(s)
        st.access_autopilot = AccessLevel.FULL
        settings_service.save_settings(s, st)

    screen._enqueue()
    with ctx.session() as s:
        jobs = job_service.list_jobs(s)
    assert len(jobs) == 1
    assert jobs[0].confirmed is True and jobs[0].project_id == project

    with ctx.session() as s:
        st = settings_service.get_settings(s)
        st.access_autopilot = AccessLevel.ASK
        settings_service.save_settings(s, st)
    monkeypatch.setattr(screen, "_confirm", lambda steps: False)
    screen._enqueue()
    with ctx.session() as s:
        assert len(job_service.list_jobs(s)) == 1


def test_a_blocked_capability_never_reaches_the_queue(ctx, qapp, project,
                                                     monkeypatch):
    with ctx.session() as s:
        st = settings_service.get_settings(s)
        st.access_autopilot = AccessLevel.BLOCKED
        settings_service.save_settings(s, st)
    screen = AutopilotScreen(ctx)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    screen._enqueue()
    assert shown and "BLOCKED" in shown[0]
    with ctx.session() as s:
        assert job_service.list_jobs(s) == []


def test_the_selection_survives_the_next_poll(ctx, qapp, project):
    """The table is rebuilt every couple of seconds; the cursor must not drift."""
    with ctx.session() as s:
        job_service.enqueue(s, project, confirmed=True)
        second = job_service.enqueue(s, project, confirmed=True)
    screen = AutopilotScreen(ctx)
    _select(screen, second.id)
    assert screen._selected_job() == second.id

    with ctx.session() as s:
        job_service.request_cancel(s, second.id)     # row order is by newest first
    screen._fill_jobs()
    assert screen._selected_job() == second.id
    assert _table(screen.job_table)[0][2] == "cancelled"
