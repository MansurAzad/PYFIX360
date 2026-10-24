"""Installing and driving the background host, without touching this machine.

``service_host`` and ``host_service`` are the only places PYFIX talks to Windows
about processes, which makes them the only places a test could genuinely harm the
computer it runs on: registering a service writes under ``HKLM``, a scheduled task
is created for the whole console session, and forgetting the install record deletes
the machine-wide handover file. So every test here keeps three things inside a
temporary directory:

* the data directory, through ``PYFIX360_DATA_DIR`` and the ``data_dir`` fixture;
* the ``%PROGRAMDATA%`` copy of the install record, by moving the path itself;
* the commands — ``schtasks``, ``nssm``, ``sc``, ``taskkill`` and ``Popen`` all go
  through an injected runner or a stand-in module.

What is left to prove is the decisions: which mode this machine is allowed to use,
what a refusal says back, who a stop request is aimed at, and whether a report
admits the things its mode cannot do.
"""

from __future__ import annotations

import logging
import os
import subprocess
import types
from pathlib import Path

import pytest

from pyfix360 import cli, service_host
from pyfix360.config import get_config
from pyfix360.db import session_scope
from pyfix360.db.models import ProjectRow
from pyfix360.services import host_service, job_service
from pyfix360.services.host_service import MODE_NSSM as NSSM
from pyfix360.services.host_service import MODE_PROCESS as PROCESS
from pyfix360.services.host_service import MODE_SERVICE as SERVICE
from pyfix360.services.host_service import MODE_TASK as TASK
from pyfix360.services.host_service import MODE_TASK_ACCOUNT as TASK_ACCOUNT
from pyfix360.services.host_service import CommandResult

#: ``schtasks /query /xml`` puts the state in ``<Status>``; only that tag is read,
#: because every other part of that output is localised.
TASK_XML_RUNNING = ('<Task><Data><ExecutionHistory><Status>Running</Status>'
                    '</ExecutionHistory></Data></Task>')
TASK_XML_READY = ('<Task><Data><ExecutionHistory><Status>Ready</Status>'
                  '</ExecutionHistory></Data></Task>')


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def sandbox(data_dir: Path, tmp_path: Path, monkeypatch):
    """Everything written here lands under ``tmp_path``, and the log handlers the
    host installs are put back so a later test keeps its own."""
    monkeypatch.setenv("PYFIX360_DATA_DIR", str(data_dir))
    monkeypatch.setattr(service_host, "_pointer_path",
                        lambda: tmp_path / "programdata" / "host.json")
    handlers = list(logging.getLogger().handlers)
    yield data_dir
    logging.getLogger().handlers = handlers


@pytest.fixture()
def project() -> int:
    with session_scope() as s:
        row = ProjectRow(name="demo", root_path="C:/nowhere")
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture()
def alive(monkeypatch) -> set[int]:
    """The pids the liveness probe swears are running.

    No test probes a real process: whether a host is alive is what ``status`` and
    the reaper exist to answer, and it has to be answerable on demand.
    """
    seen: set[int] = set()
    monkeypatch.setattr(job_service, "pid_alive", lambda pid: pid in seen)
    return seen


@pytest.fixture()
def use_probe(monkeypatch):
    """Declare what this imaginary machine has, per test."""
    def _apply(**flags) -> host_service.Probe:
        kind = host_service.Probe(
            windows=flags.get("windows", True),
            admin=flags.get("admin", True),
            pywin32=flags.get("pywin32", True),
            scheduler=flags.get("scheduler", True),
            nssm=flags.get("nssm", False))
        monkeypatch.setattr(host_service, "probe", lambda: kind)
        return kind
    return _apply


@pytest.fixture()
def spawn(monkeypatch) -> list[list[str]]:
    """The detached child, with a stand-in ``subprocess`` module.

    Patching the attribute on the real module would blind every other test that
    runs a command, so only the name inside ``host_service`` is swapped.
    """
    seen: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        seen.append(list(argv))
        return types.SimpleNamespace(pid=70707)

    monkeypatch.setattr(host_service, "subprocess", types.SimpleNamespace(
        Popen=fake_popen, DEVNULL=-3, DETACHED_PROCESS=0x8,
        CREATE_NEW_PROCESS_GROUP=0x200, SubprocessError=subprocess.SubprocessError))
    return seen


@pytest.fixture()
def no_scm(monkeypatch) -> list[str]:
    """Nothing in these tests asks the real service control manager anything."""
    asked: list[str] = []
    monkeypatch.setattr(host_service, "_win32_service_call",
                        lambda name: asked.append(name))
    monkeypatch.setattr(host_service, "_service_state", lambda: "running")
    return asked


class _Runner:
    """Every OS command the service layer would run, recorded and answered."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, CommandResult] = {}

    def answer(self, needle: str, result: CommandResult) -> None:
        self.answers[needle] = result

    def refuse(self, needle: str, message: str = "refused") -> None:
        self.answers[needle] = CommandResult(1, stderr=message)

    def __call__(self, argv) -> CommandResult:
        argv = list(argv)
        self.calls.append(argv)
        text = " ".join(argv)
        for needle, result in self.answers.items():
            if needle in text:
                return result
        return CommandResult(0)

    def call_with(self, needle: str) -> list[str]:
        for argv in self.calls:
            if needle in " ".join(argv):
                return argv
        raise AssertionError(f"no command mentioned {needle!r}; ran:\n{self.text}")

    @property
    def text(self) -> str:
        return "\n".join(" ".join(argv) for argv in self.calls)


@pytest.fixture()
def commands() -> _Runner:
    return _Runner()


def _flag(argv: list[str], name: str) -> str:
    """The argument following ``name``, for ``/sc onlogon`` style pairs."""
    return argv[argv.index(name) + 1]


def _installed(mode: str) -> None:
    """Say what ``service install`` would have recorded, without installing it."""
    service_host.write_state({"mode": mode, "service_name": service_host.SERVICE_NAME,
                              "task_name": service_host.TASK_NAME})


def _host_running(pid: int) -> None:
    """A live host's own pid file — written with ``for_pid``, as a spawner writes it."""
    service_host.write_pid(for_pid=pid)


def _notes_containing(report: host_service.Report, needle: str) -> list[str]:
    return [note for note in report.notes if needle in note]


# --------------------------------------------------------------------------------------
# the files the host and the CLI pass each other
# --------------------------------------------------------------------------------------


def test_the_pid_file_names_the_child_a_spawner_started():
    service_host.write_pid()
    assert service_host.read_pid()["pid"] == os.getpid()
    # A fresh interpreter has not imported far enough to write its own yet, and the
    # spawner already knows the pid — reporting "not running" after a successful
    # start would be worse than a stamp the host rewrites a second later.
    service_host.write_pid(for_pid=70707)
    assert service_host.read_pid()["pid"] == 70707
    service_host.clear_pid()
    assert service_host.read_pid() == {}


def test_a_stop_request_is_aimed_at_one_process():
    service_host.request_stop(for_pid=1234)
    assert service_host.stop_request() == (True, 1234)
    assert service_host.stop_requested(for_pid=1234) is True
    assert service_host.stop_requested(for_pid=9999) is False


def test_an_unnamed_stop_request_is_answered_by_whoever_reads_it():
    service_host.request_stop()
    assert service_host.stop_request() == (True, None)
    assert service_host.stop_requested(for_pid=4242) is True


def test_a_marker_written_by_hand_still_means_stop():
    """Not every file is written by this program.

    An unreadable marker is read as a stop aimed at nobody rather than ignored: the
    safe meaning of "someone left a stop request here" is that a stop was requested.
    """
    path = get_config().service_dir / service_host.STOP_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop please", encoding="utf-8")
    assert service_host.stop_request() == (True, None)
    assert service_host.stop_requested(for_pid=1) is True


def test_clearing_a_stop_request_does_not_clear_somebody_elses():
    service_host.request_stop(for_pid=1234)
    assert service_host.clear_stop(for_pid=9999) is False
    assert service_host.stop_request() == (True, 1234)
    assert service_host.clear_stop(for_pid=1234) is True
    assert service_host.clear_stop() is False           # nothing left to clear


def test_install_time_is_the_one_place_a_foreign_request_is_overruled():
    """``force`` exists because a request outlives the host it was written for."""
    service_host.request_stop(for_pid=1234)
    assert service_host.clear_stop(force=True) is True
    assert service_host.stop_request() == (False, None)


def test_the_machine_wide_copy_wins_because_a_service_cannot_see_yours(
        data_dir: Path):
    """A session-0 host reads ``%PROGRAMDATA%``, so that copy is the authority.

    Two records that disagree would otherwise mean the desktop talks to one
    database and the service to another.
    """
    service_host.write_state({"mode": TASK, "data_dir": str(data_dir)})
    assert service_host.read_state()["mode"] == TASK
    service_host.write_state({"mode": SERVICE}, pointer=True)
    assert service_host.read_state()["mode"] == SERVICE


def test_forgetting_the_record_reports_what_this_account_cannot_delete(
        tmp_path: Path):
    """A leftover pointer would hide the next, per-user install from ``status``."""
    service_host.write_state({"mode": TASK})
    blocker = tmp_path / "programdata" / "host.json"
    blocker.mkdir(parents=True)          # a directory cannot be unlinked: EACCES-ish
    left = service_host.forget_state()
    assert service_host.read_state() == {}
    assert [str(blocker)] == left


def test_the_data_dir_is_chosen_by_how_deliberately_it_was_set(
        tmp_path: Path, monkeypatch):
    """Override, then record, then default — in that order.

    Reversed, a recorded install would outvote the operator standing in a console.
    And a session-0 service that could not read the record would open its own empty
    database and quietly run against nothing.
    """
    base = tmp_path / "default"
    recorded, override = tmp_path / "recorded", tmp_path / "override"
    for path in (base, recorded, override):
        path.mkdir()

    def fake_default() -> Path:
        """The real one reads the environment too, which is the whole first branch."""
        asked = os.environ.get("PYFIX360_DATA_DIR")
        return Path(asked) if asked else base

    monkeypatch.setattr(service_host, "default_data_dir", fake_default)
    monkeypatch.delenv("PYFIX360_DATA_DIR")
    service_host.write_state({"mode": TASK, "data_dir": str(recorded)})
    assert service_host.resolve_data_dir() == recorded
    monkeypatch.setenv("PYFIX360_DATA_DIR", str(override))
    assert service_host.resolve_data_dir() == override
    monkeypatch.delenv("PYFIX360_DATA_DIR")
    # A record pointing at a directory that is gone must not be believed: the host
    # would create an empty database there and run against that instead.
    service_host.write_state({"mode": TASK, "data_dir": str(tmp_path / "was-removed")})
    assert service_host.resolve_data_dir() == base


def test_the_generated_launcher_is_what_a_task_is_pointed_at():
    """``schtasks /tr`` truncates, so the long path lives in a file instead.

    A truncated command becomes a task that reports itself registered and never
    starts anything.
    """
    path = service_host.write_launcher()
    text = path.read_text(encoding="utf-8")
    assert path.name == service_host.LAUNCHER_NAME
    assert "pyfix360.service_host import main" in text
    assert "sys.path.insert" in text


def test_the_host_writes_its_own_log_file(data_dir: Path):
    """Not the desktop's ``pyfix360.log``: a background run is read after the fact,
    usually while the desktop is still writing its own lines."""
    cfg = get_config()
    path = service_host.setup_logging(cfg, level="DEBUG", to_console=False)
    assert path == cfg.logs_dir / "service.log"
    logging.getLogger("pyfix360.test").info("written by the host")
    assert "written by the host" in path.read_text(encoding="utf-8")


def test_run_foreground_polls_bounded_and_undoes_its_own_markers(monkeypatch):
    """A host that has exited must not leave a pid file claiming it is here.

    Readers decide whether to queue work, and whether to reap a job, from those
    files.
    """
    monkeypatch.setattr(service_host, "_install_signals", lambda _stop: None)
    service_host.request_stop(for_pid=1234)          # a stop for the old host
    service_host.bind_data_dir()
    assert service_host.run_foreground(max_polls=2, quiet=True) == 0
    assert service_host.read_pid() == {}
    assert service_host.stop_request() == (True, 1234)   # not this host's to clear
    assert (get_config().service_dir / service_host.PID_FILENAME).exists() is False
    assert logging.getLogger().handlers               # it did attach a file handler


# --------------------------------------------------------------------------------------
# what a machine is allowed to do
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("flags,expected", [
    ({"admin": True, "pywin32": True, "nssm": True},
     (SERVICE, NSSM, TASK_ACCOUNT, TASK, PROCESS)),
    ({"admin": False}, (TASK_ACCOUNT, TASK, PROCESS)),
    ({"pywin32": False, "nssm": True}, (NSSM, TASK_ACCOUNT, TASK, PROCESS)),
    ({"windows": False, "admin": True, "pywin32": True, "scheduler": False,
      "nssm": True}, (PROCESS,)),
    ({"scheduler": False, "pywin32": False}, (PROCESS,)),
])
def test_the_modes_are_offered_strongest_first(flags, expected):
    kind = host_service.Probe(windows=flags.get("windows", True),
                              admin=flags.get("admin", True),
                              pywin32=flags.get("pywin32", True),
                              scheduler=flags.get("scheduler", True),
                              nssm=flags.get("nssm", False))
    assert kind.options == expected
    assert kind.blocker(expected[0]) == ""


@pytest.mark.parametrize("mode,needle,kind_flags", [
    (SERVICE, "administrator rights", {}),
    (SERVICE, "pywin32 is not installed", {"pywin32": False}),
    (NSSM, "nssm.exe is not on PATH", {"pywin32": False}),
    (TASK, "scheduled tasks are a Windows feature", {"windows": False}),
])
def test_a_refusal_names_the_missing_thing(mode, needle, kind_flags):
    flags = {"windows": True, "admin": False, "pywin32": True, "scheduler": True,
             "nssm": False}
    flags.update(kind_flags)
    kind = host_service.Probe(**flags)
    assert needle in kind.blocker(mode)


def test_the_process_mode_is_always_offered_because_it_installs_nothing():
    kind = host_service.Probe(windows=False, admin=False, pywin32=False,
                              scheduler=False, nssm=False)
    assert kind.options[-1] == PROCESS
    assert kind.blocker(PROCESS) == ""


def test_the_real_probe_answers_in_the_same_shape():
    """This machine is what it is; the contract is that the answer is readable."""
    kind = host_service.probe()
    assert kind.lines()[0].startswith("platform:")
    assert kind.lines()[-1].startswith("recommended: ")
    assert kind.options[0] in host_service.MODES


# --------------------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------------------


def test_an_unnamed_mode_is_an_error_not_a_guess(commands):
    report = host_service.install(mode="telepathy", runner=commands)
    assert report.ok is False
    assert "not a mode" in report.summary
    assert report.remedy.endswith(", ".join(host_service.MODES))
    assert commands.calls == []                 # refused before touching Windows


def test_a_mode_this_machine_refuses_is_refused_loudly_without_shelling_out(
        use_probe, commands):
    """Silently falling back to a weaker mode would install something the operator
    never asked for — and surviving logoff is precisely what was asked."""
    use_probe(admin=False)
    report = host_service.install(mode=SERVICE, runner=commands)
    assert report.ok is False
    assert "administrator rights" in report.summary
    assert "elevated" in report.remedy
    assert commands.calls == []
    assert host_service.installed_mode() == ""


def test_a_logon_task_is_created_for_the_launcher_not_a_long_inline_command(
        use_probe, commands):
    use_probe(admin=False)
    report = host_service.install(mode=TASK, runner=commands)
    assert report.ok is True
    argv = commands.call_with("/create")
    assert _flag(argv, "/tn") == service_host.TASK_NAME
    assert _flag(argv, "/sc") == "onlogon"
    assert service_host.LAUNCHER_NAME in _flag(argv, "/tr")
    assert "session_scope" not in commands.text        # no import gymnastics in /tr
    assert host_service.installed_mode() == TASK


def test_paths_with_spaces_survive_the_command_line(use_probe, commands):
    """This data directory lives under a user profile, so it always has a space."""
    use_probe(admin=False)
    host_service.install(mode=TASK, runner=commands)
    tr = _flag(commands.call_with("/create"), "/tr")
    assert tr.startswith('"') or " " not in tr.split('"')[0]


def test_a_task_that_survives_logoff_names_the_account_that_carries_it(
        use_probe, commands):
    use_probe(admin=False)
    report = host_service.install(mode=TASK_ACCOUNT, username="ops", password="letme",
                                  runner=commands)
    assert report.ok is True
    argv = commands.call_with("/create")
    assert _flag(argv, "/sc") == "onstart"
    assert _flag(argv, "/ru") == "ops"
    assert _flag(argv, "/rp") == "letme"
    assert _notes_containing(report, "Windows stores the password")


def test_a_task_account_install_without_credentials_says_who_it_needs(
        use_probe, commands):
    use_probe(admin=False)
    report = host_service.install(mode=TASK_ACCOUNT, runner=commands)
    assert report.ok is False
    assert "which account runs it" in report.summary
    assert "--password-ask" in report.remedy
    assert commands.calls == []


def test_the_strongest_mode_is_downgraded_only_when_it_cannot_be_stored(
        use_probe, commands):
    """``install`` with no ``--mode`` picks persistence, but only if it can be paid for."""
    use_probe(admin=False)
    report = host_service.install(runner=commands)
    assert report.mode == TASK
    assert _flag(commands.call_with("/create"), "/sc") == "onlogon"
    assert _notes_containing(report, "ends when you log off")
    assert _notes_containing(report, "task-account")


def test_an_install_the_tool_refuses_records_nothing(use_probe, commands):
    use_probe(admin=False)
    commands.refuse("/create", "ERROR: Access is denied.")
    report = host_service.install(mode=TASK, runner=commands)
    assert report.ok is False
    assert "Access is denied" in report.summary
    assert host_service.installed_mode() == ""
    assert _notes_containing(report, "start") == []


def test_every_install_says_what_that_mode_cannot_do(use_probe, commands):
    """The trade-off line is not decoration; it is stated before the operator has to
    ask why the host vanished at logoff."""
    use_probe(admin=False)
    report = host_service.install(mode=TASK, runner=commands)
    assert _notes_containing(report, "Task Manager")
    assert _notes_containing(report, str(get_config().service_log_path))
    assert _notes_containing(report, "when you log off")


def test_a_service_install_records_the_handover_a_session_zero_host_needs(
        use_probe, commands, monkeypatch, tmp_path: Path):
    """A machine account has its own ``%LOCALAPPDATA%``, so the machine-wide copy is
    the only way it finds the database it was installed against."""
    use_probe()
    monkeypatch.setattr(host_service, "_install_service",
                        lambda runner: host_service._accepted(
                            "install", SERVICE, "service registered"))
    report = host_service.install(mode=SERVICE, runner=commands)
    assert report.ok is True
    assert _notes_containing(report, "machine-wide pointer")
    assert (tmp_path / "programdata" / "host.json").is_file()
    assert service_host.read_state()["mode"] == SERVICE


def test_a_leftover_stop_request_does_not_stop_the_next_host_at_birth(
        use_probe, commands):
    use_probe(admin=False)
    service_host.request_stop(for_pid=1234)         # aimed at the host being replaced
    report = host_service.install(mode=TASK, runner=commands)
    assert report.ok is True
    assert _notes_containing(report, "stop request left by the previous host")
    assert service_host.stop_request() == (False, None)


def test_install_can_register_without_starting(use_probe, commands, spawn):
    report = host_service.install(mode=PROCESS, runner=commands, start=False)
    assert report.ok is True
    assert spawn == []                             # nothing was started
    assert _notes_containing(report, "installed but not started")


def test_starting_a_process_host_is_installing_it(use_probe, commands, spawn):
    """Both verbs are the same act in this mode, so the report says which happened."""
    report = host_service.install(mode=PROCESS, runner=commands)
    assert report.ok and report.running is True and report.pid == 70707
    assert len(spawn) == 1
    assert service_host.LAUNCHER_NAME in spawn[0][1]
    assert host_service.installed_mode() == PROCESS
    # No second start: the host has not had a moment to write its own pid file, and
    # asking again would put a second host on the same queue.
    assert commands.calls == []


def test_starting_an_installed_host_asks_the_launcher_that_owns_it(
        commands):
    _installed(NSSM)
    report = host_service.control("start", runner=commands)
    assert report.ok is True
    assert commands.call_with("start")[-1] == service_host.SERVICE_NAME
    assert report.running is None                  # nssm said yes; no pid stamped yet


# --------------------------------------------------------------------------------------
# start / stop
# --------------------------------------------------------------------------------------


def test_starting_is_refused_when_nothing_was_installed(commands):
    report = host_service.start(runner=commands)
    assert report.ok is False
    assert report.remedy == "pyfix360 service install"
    assert commands.calls == []


def test_a_second_start_does_not_put_two_hosts_on_one_queue(commands, alive, spawn):
    """Two hosts on one database is not redundancy: each would park approvals the
    other is waiting on and split the queue between them."""
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    report = host_service.start(runner=commands)
    assert report.running is True and report.pid == 9
    assert "already running as pid 9" in report.summary
    assert spawn == []


def test_a_start_clears_a_stop_request_that_would_otherwise_swallow_it(commands):
    _installed(TASK)
    service_host.request_stop()                    # aimed at whoever listens
    report = host_service.start(runner=commands)
    assert _notes_containing(report, "stop request left from earlier was cleared")
    assert service_host.stop_request() == (False, None)


def test_a_graceful_stop_is_a_request_aimed_at_the_live_host(commands, alive):
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    report = host_service.stop(runner=commands)
    assert report.ok is True and report.running is True and report.pid == 9
    assert "pid 9" in report.summary
    assert service_host.stop_request() == (True, 9)
    assert commands.calls == []                    # nothing was killed
    assert _notes_containing(report, "finishes its current step first")


def test_a_stop_with_nobody_listening_still_leaves_the_request(commands):
    """A task host may be starting right now, under a pid this prompt cannot see."""
    _installed(TASK)
    report = host_service.stop(runner=commands)
    assert service_host.stop_request() == (True, None)
    assert "the host that reads it" in report.summary


def test_a_forced_stop_says_the_work_was_not_drained(commands, alive):
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    report = host_service.stop(force=True, runner=commands)
    assert report.ok is True and report.running is False
    assert _flag(commands.call_with("taskkill"), "/pid") == "9"
    assert "/f" in commands.call_with("taskkill")
    assert _notes_containing(report, "killed, not drained")


def test_a_forced_stop_of_a_task_ends_the_run_it_owns(commands):
    _installed(TASK)
    report = host_service.stop(force=True, runner=commands)
    assert _flag(commands.call_with("/end"), "/tn") == service_host.TASK_NAME
    assert report.running is False
    assert _notes_containing(report, "ended, not drained")


def test_a_kill_the_tool_refuses_is_reported_with_the_pid_it_holds(commands, alive):
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    commands.refuse("taskkill", "ERROR: The process not found.")
    report = host_service.stop(force=True, runner=commands)
    assert report.ok is False
    assert "service status" in report.remedy


def test_a_service_stop_asks_the_control_manager_and_does_not_parse_text(
        no_scm, commands):
    """pywin32 answers with numbers; ``sc.exe`` answers in the machine's language."""
    _installed(SERVICE)
    report = host_service.stop(runner=commands)
    assert report.ok is True
    assert no_scm == ["StopService"]
    assert commands.calls == []                    # nothing was shelled out


# --------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------


def test_status_says_plainly_that_nothing_will_run(commands):
    report = host_service.status(runner=commands)
    assert report.ok is True and report.running is False
    assert report.summary == "not installed"
    assert _notes_containing(report, "pyfix360 service install")


def test_the_task_state_is_read_from_xml_because_labels_are_localised(commands):
    """Guessing a live host from a translated label is how an operator ends up with
    two hosts and one queue."""
    _installed(TASK)
    commands.answer("/xml", CommandResult(0, stdout=TASK_XML_RUNNING))
    report = host_service.status(runner=commands)
    assert report.mode == TASK and report.running is True
    assert _notes_containing(report, "task scheduler says: Running")


def test_an_idle_task_is_not_reported_as_a_running_host(commands):
    _installed(TASK)
    commands.answer("/xml", CommandResult(0, stdout=TASK_XML_READY))
    report = host_service.status(runner=commands)
    assert report.running is False
    assert _notes_containing(report, "Ready")


def test_an_unparseable_answer_is_nothing_rather_than_a_guess(commands):
    _installed(TASK)
    commands.answer("/xml", CommandResult(0, stdout="Der Vorgang wurde beendet."))
    assert host_service.status(runner=commands).running is False


def test_status_reports_a_launcher_that_thinks_it_is_running(commands):
    _installed(NSSM)
    commands.answer("status", CommandResult(0, stdout="SERVICE_RUNNING"))
    report = host_service.status(runner=commands)
    assert report.running is True and report.pid is None
    assert _notes_containing(report, "probably still starting")


def test_status_names_who_a_pending_stop_is_aimed_at(commands, alive):
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    service_host.request_stop(for_pid=9)
    report = host_service.status(runner=commands)
    assert _notes_containing(report, "a stop is pending for pid 9")
    assert _notes_containing(report, "current step")


def test_status_calls_a_leftover_stop_a_leftover(commands):
    _installed(TASK)
    service_host.request_stop(for_pid=1234)
    report = host_service.status(runner=commands)
    assert _notes_containing(report, "no live host answers it")


def test_status_distinguishes_a_dead_pid_file_from_a_live_one(commands, alive):
    """The file alone would report a crashed daemon as running."""
    _installed(PROCESS)
    _host_running(9)
    assert host_service.status(runner=commands).running is False
    alive.add(9)
    report = host_service.status(runner=commands)
    assert report.running is True
    assert _notes_containing(report, "a host is running as pid 9")


# --------------------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------------------


def test_uninstall_removes_the_registration_and_keeps_the_history(commands):
    _installed(TASK)
    report = host_service.uninstall(runner=commands)
    assert report.ok is True
    assert _flag(commands.call_with("/delete"), "/tn") == service_host.TASK_NAME
    assert host_service.installed_mode() == ""
    assert _notes_containing(report, "no job history was deleted")


def test_uninstall_of_a_draining_mode_leaves_the_stop_request_alone(commands,
                                                                   alive):
    """Clearing it here would cancel the very stop this uninstall just asked for."""
    _installed(TASK)
    _host_running(9)
    alive.add(9)
    report = host_service.uninstall(runner=commands)
    assert service_host.stop_request() == (True, 9)
    assert report.running is None                  # it may still be finishing a step
    assert _notes_containing(report, "asked to stop")


def test_uninstall_of_a_service_forgets_the_handover_it_made(commands, alive, no_scm,
                                                            monkeypatch):
    """A service never reads the marker file, so a leftover would stop the next host
    at birth — and the machine-wide pointer would hide a later per-user install."""
    monkeypatch.setattr(host_service, "_remove_service", lambda: (True, ""))
    _installed(SERVICE)
    service_host.request_stop(for_pid=1234)
    _host_running(9)
    alive.add(9)
    report = host_service.uninstall(runner=commands)
    assert report.ok is True and report.running is False
    assert no_scm == ["StopService"]
    assert service_host.stop_request() == (False, None)
    assert service_host.read_state() == {}
    assert not (get_config().service_dir / "host.json").exists()


def test_uninstall_refuses_when_the_removal_is_refused(commands, monkeypatch):
    monkeypatch.setattr(host_service, "_remove_service",
                        lambda: (False, "Access is denied"))
    _installed(SERVICE)
    report = host_service.uninstall(runner=commands)
    assert report.ok is False
    assert "elevated" in report.remedy
    assert host_service.installed_mode() == SERVICE     # record kept: it is still there


def test_uninstall_of_a_process_only_stops_it(commands, alive):
    """There is nothing registered to remove, so the report must not claim there was."""
    _installed(PROCESS)
    _host_running(9)
    alive.add(9)
    report = host_service.uninstall(runner=commands)
    assert report.ok is True
    assert commands.calls == []
    assert service_host.stop_request() == (True, 9)


# --------------------------------------------------------------------------------------
# the log and the detached child
# --------------------------------------------------------------------------------------


def test_the_log_is_read_from_the_end(data_dir: Path):
    """A two-megabyte rotating file puts its oldest lines at the front, which is the
    least useful place to start reading."""
    log = get_config().logs_dir / "service.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(f"line {i}" for i in range(1, 201)), encoding="utf-8")
    assert host_service.log_tail(2) == ["line 199", "line 200"]
    assert len(host_service.log_tail(0)) == 200      # 0 means "the whole tail window"
    log.unlink()
    assert "no host log yet" in host_service.log_tail(5)[0]


def test_a_spawned_child_gets_its_pid_written_before_it_can(spawn):
    """Otherwise ``status`` run straight after a successful install would report the
    host it just started as not running."""
    assert host_service.spawn_detached() == 70707
    assert service_host.read_pid()["pid"] == 70707
    assert spawn[0][-1] == str(service_host.launcher_path())


def test_a_child_that_refuses_to_start_says_so(spawn, monkeypatch):
    def boom(argv, **_kwargs):
        raise OSError(2, "no such interpreter")

    monkeypatch.setattr(host_service.subprocess, "Popen", boom)
    assert host_service.spawn_detached() is None
    assert service_host.read_pid() == {}           # nothing stamped, nothing claimed


# --------------------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------------------


def test_the_cli_reports_what_this_machine_allows(capsys):
    assert cli.main(["service", "probe"]) == 0
    out = capsys.readouterr().out
    assert "modes available:" in out and "recommended:" in out


def test_queueing_from_a_console_carries_the_confirmation_decision(capsys, project):
    assert cli.main(["service", "enqueue", str(project)]) == 0
    out = capsys.readouterr().out
    assert "job #1 queued (autopilot)" in out
    assert "parks the job" in out                  # the unconfirmed warning
    assert cli.main(["service", "jobs", "--open"]) == 0
    assert "queued" in capsys.readouterr().out


def test_answering_a_gate_that_is_not_open_is_reported_as_an_error(capsys, project):
    with session_scope() as s:
        job = job_service.enqueue(s, project, confirmed=True)
    capsys.readouterr()
    assert cli.main(["service", "approve", str(job.id)]) == 1
    assert "not waiting for approval" in capsys.readouterr().err


def test_cancelling_a_job_that_does_not_exist_says_so(capsys):
    assert cli.main(["service", "cancel", "999"]) == 1
    assert "not found" in capsys.readouterr().err


def test_the_status_exit_code_is_the_answer_a_script_wants(capsys, alive):
    _installed(PROCESS)
    assert cli.main(["service", "status"]) == 1        # installed, not running
    _host_running(9)
    alive.add(9)
    capsys.readouterr()
    assert cli.main(["service", "status"]) == 0
    assert "a host is running as pid 9" in capsys.readouterr().out


def test_a_failing_status_counts_the_jobs_it_printed(capsys, project):
    with session_scope() as s:
        job_service.enqueue(s, project, confirmed=True)
    assert cli.main(["service", "status"]) == 1
    assert "queued: 1" in capsys.readouterr().out


def test_the_install_password_never_reaches_the_command_line(capsys, monkeypatch):
    """``schtasks /rp`` must be handed the password to store it, so the only place
    this program takes it from is a prompt the shell does not remember."""
    seen: dict = {}

    def fake_install(**kwargs) -> host_service.Report:
        seen.update(kwargs)
        return host_service.Report(action="install", mode=TASK_ACCOUNT, ok=True,
                                   summary="created")

    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "letmein")
    monkeypatch.setattr(host_service, "install", fake_install)
    argv = ["service", "install", "--mode", TASK_ACCOUNT,
            "--username", "ops", "--password-ask"]
    assert cli.main(argv) == 0
    assert seen["password"] == "letmein"
    assert "letmein" not in " ".join(argv)
    assert "letmein" not in capsys.readouterr().out


def test_install_flags_reach_the_service_layer(capsys, monkeypatch):
    seen: dict = {}

    def fake_install(**kwargs) -> host_service.Report:
        seen.update(kwargs)
        return host_service.Report(action="install", mode=TASK, ok=True,
                                   summary="registered")

    monkeypatch.setattr(host_service, "install", fake_install)
    assert cli.main(["service", "install", "--mode", TASK, "--no-start"]) == 0
    assert seen == {"mode": TASK, "username": None, "password": None, "start": False}
    assert "registered" in capsys.readouterr().out


def test_a_refused_install_exits_nonzero(capsys, monkeypatch):
    monkeypatch.setattr(
        host_service, "install",
        lambda **kw: host_service.Report(action="install", mode=SERVICE, ok=False,
                                         summary="needs an elevated prompt"))
    assert cli.main(["service", "install", "--mode", SERVICE]) == 1
    assert "elevated" in capsys.readouterr().out


def test_stop_and_uninstall_flags_reach_the_service_layer(capsys, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        host_service, "stop",
        lambda **kw: seen.append(("stop", kw)) or host_service.Report(
            action="stop", mode=PROCESS, ok=True, summary="asked"))
    monkeypatch.setattr(
        host_service, "uninstall",
        lambda **kw: seen.append(("uninstall", kw)) or host_service.Report(
            action="uninstall", mode=PROCESS, ok=True, summary="process mode removed"))
    assert cli.main(["service", "stop", "--force"]) == 0
    assert seen == [("stop", {"force": True})]
    capsys.readouterr()
    assert cli.main(["service", "uninstall"]) == 0
    assert seen[-1] == ("uninstall", {})
    assert "removed" in capsys.readouterr().out


def test_the_cli_reports_a_stop_that_could_not_be_carried_out(capsys, monkeypatch):
    monkeypatch.setattr(
        host_service, "stop",
        lambda **kw: host_service.Report(action="stop", mode=PROCESS, ok=False,
                                         summary="the process would not end"))
    assert cli.main(["service", "stop"]) == 1
    assert "would not end" in capsys.readouterr().out


def test_the_run_subcommand_maps_its_bounds_through(capsys, monkeypatch):
    seen: dict = {}

    def fake_foreground(**kwargs) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(service_host, "run_foreground", fake_foreground)
    assert cli.main(["service", "run", "--once", "--polls", "4", "--quiet"]) == 0
    assert seen == {"max_polls": 4, "quiet": True}
    capsys.readouterr()
    assert cli.main(["service", "run"]) == 0
    assert seen["max_polls"] is None                  # unbounded, until interrupted


def test_the_cli_prints_the_host_log(capsys):
    log = get_config().logs_dir / "service.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("first\nsecond\nthird\n", encoding="utf-8")
    assert cli.main(["service", "logs", "-n", "2"]) == 0
    out = capsys.readouterr().out
    assert "second" in out and "first" not in out


def test_an_unknown_mode_is_rejected_before_anything_runs():
    with pytest.raises(SystemExit) as stop:
        cli.main(["service", "install", "--mode", "telepathy"])
    assert stop.value.code == 2
