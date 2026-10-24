"""Installing the background host so it outlives the window — and the logoff.

The commission asks for one command, ``pyfix360 service install``, but on Windows
there are five genuinely different things that could mean, and they trade rights
against persistence. Choosing silently would be the wrong kind of helpful, so
:func:`probe` says what this machine allows, :func:`recommend` takes the strongest
mode that is actually available, and every :class:`Report` names the mode it used
and what that mode cannot do.

    mode             needs admin   survives logoff   starts at
    ==============   ===========   ===============   ===========
    ``service``          yes            yes          boot
    ``nssm``             yes            yes          boot
    ``task-account``     no             yes          boot
    ``task``             no             no           logon
    ``process``          no             no           when you run it

Two limits are stated rather than worked around, because they cannot be fixed:

**The process is always visible in Task Manager.** A windowless host with no tray
icon is what "background" means here. Leaving the process list would need process
hiding, which is a rootkit technique, not a feature.

**A service runs as a machine account.** ``LocalSystem`` has a keyring of its own
and cannot unlock the file fallback, which is keyed to your user, so a credential
you stored simply is not reachable from a service. A step that needs one then sends
no key and the provider refuses it — the job never runs as somebody else and never
invents a secret. If you need your own identity without administrator rights, that
is what ``task-account`` is for.

Nothing here touches the database, and nothing shells out under test: every OS
interaction goes through the injected ``runner``, so the whole decision tree is
exercised without changing a machine.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pyfix360 import service_host
from pyfix360.config import get_config

MODE_SERVICE = "service"
MODE_NSSM = "nssm"
MODE_TASK_ACCOUNT = "task-account"
MODE_TASK = "task"
MODE_PROCESS = "process"
MODES = (MODE_SERVICE, MODE_NSSM, MODE_TASK_ACCOUNT, MODE_TASK, MODE_PROCESS)

#: What each mode costs, said in the operator's terms rather than the API's.
TRADE_OFFS = {
    MODE_SERVICE: "a real service: no window, starts at boot, runs whether or not "
                  "anybody is logged on. Needs administrator rights, and runs as a "
                  "machine account, so it cannot read your keyring.",
    MODE_NSSM: "a real service wrapping the host, without pywin32. Needs "
               "administrator rights, and needs NSSM installed.",
    MODE_TASK_ACCOUNT: "a scheduled task with stored credentials: starts at boot, "
                       "survives logoff, keeps your own identity. Windows stores "
                       "the password to make that work.",
    MODE_TASK: "a scheduled task at logon: no window, no administrator rights "
               "needed, but Windows ends it when you log off.",
    MODE_PROCESS: "a detached process started now: nothing is installed, nothing "
                  "restarts it, and it dies with your session.",
}

#: How much of the rotated log to read when somebody asks to see it.
LOG_TAIL_BYTES = 65536

#: Who answers for each mode that is driven by an external command.
_LAUNCHER_LABEL = {
    MODE_NSSM: "nssm",
    MODE_TASK_ACCOUNT: "Task Scheduler",
    MODE_TASK: "Task Scheduler",
}

logger = logging.getLogger("pyfix360.service")

Runner = Callable[[list[str]], "CommandResult"]


# --------------------------------------------------------------------------------------
# running commands
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    def message(self) -> str:
        """The one line an operator needs from a tool that just failed."""
        for line in reversed((self.stderr or self.stdout).strip().splitlines()):
            if line.strip():
                return line.strip()
        return f"exit code {self.code}"


def subprocess_runner(argv: Sequence[str]) -> CommandResult:
    """The real runner: capture the output, never open a window, always return."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(list(argv), capture_output=True, timeout=60,
                              creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(127, stderr=f"{type(exc).__name__}: {exc}")
    return CommandResult(proc.returncode,
                         (proc.stdout or b"").decode(errors="replace"),
                         (proc.stderr or b"").decode(errors="replace"))


def _run(argv: list[str], runner: Runner | None) -> CommandResult:
    return (runner or subprocess_runner)(argv)


# --------------------------------------------------------------------------------------
# what this machine will allow
# --------------------------------------------------------------------------------------


def is_admin() -> bool:
    """Whether this process may talk to the service control manager at all."""
    if os.name != "nt":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):               # pragma: no cover - exotic hosts
        return False


@dataclass(frozen=True)
class Probe:
    windows: bool
    admin: bool
    pywin32: bool
    scheduler: bool
    nssm: bool

    @property
    def options(self) -> tuple[str, ...]:
        """Every mode this machine could use, strongest first.

        ``service`` needs pywin32 *and* administrator rights, because registering
        the class the service control manager loads means writing under
        ``HKLM\\System\\CurrentControlSet``.
        """
        out: list[str] = []
        if self.windows:
            if self.pywin32 and self.admin:
                out.append(MODE_SERVICE)
            if self.nssm and self.admin:
                out.append(MODE_NSSM)
            if self.scheduler:
                out.append(MODE_TASK_ACCOUNT)
                out.append(MODE_TASK)
        out.append(MODE_PROCESS)
        return tuple(out)

    def blocker(self, mode: str) -> str:
        """Why ``mode`` is not on offer here — empty when it is."""
        if mode in self.options:
            return ""
        if mode == MODE_SERVICE:
            if not self.windows:
                return "there is no Windows service control manager here"
            if not self.pywin32:
                return "pywin32 is not installed (pip install 'pyfix360[service]')"
            return "administrator rights are needed to register a service"
        if mode == MODE_NSSM:
            if not self.windows:
                return "NSSM is a Windows service wrapper"
            if not self.nssm:
                return "nssm.exe is not on PATH"
            return "administrator rights are needed to register a service"
        if mode in (MODE_TASK, MODE_TASK_ACCOUNT):
            if not self.windows:
                return "scheduled tasks are a Windows feature"
            return "schtasks.exe is not available"
        return ""

    def lines(self) -> list[str]:
        options = self.options
        return [
            f"platform: {'windows' if self.windows else 'posix'}",
            f"administrator rights: {'yes' if self.admin else 'no'}",
            f"pywin32: {'available' if self.pywin32 else 'missing'}",
            f"schtasks.exe: {'found' if self.scheduler else 'missing'}",
            f"nssm.exe: {'found' if self.nssm else 'missing'}",
            f"modes available: {', '.join(options)}",
            f"recommended: {options[0]}",
        ]


def probe() -> Probe:
    return Probe(
        windows=sys.platform == "win32",
        admin=is_admin(),
        pywin32=service_host.services_supported(),
        scheduler=shutil.which("schtasks") is not None,
        nssm=shutil.which("nssm") is not None,
    )


def recommend() -> str:
    """The most persistent mode this machine will actually let us install."""
    return probe().options[0]


# --------------------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------------------


@dataclass
class Report:
    """What was asked, what was done, and what the operator must still know."""

    action: str
    mode: str = ""
    ok: bool = False
    summary: str = ""
    notes: list[str] = field(default_factory=list)
    remedy: str = ""
    running: bool | None = None
    pid: int | None = None

    def lines(self) -> list[str]:
        label = f"{self.action} ({self.mode}): " if self.mode else f"{self.action}: "
        out = [label + self.summary] if self.summary else [self.action]
        out += [f"  - {note}" for note in self.notes]
        if self.remedy:
            out.append(f"  to do it: {self.remedy}")
        return out


def _fail(action: str, mode: str, summary: str, *, remedy: str = "",
          notes: Sequence[str] = ()) -> Report:
    return Report(action=action, mode=mode, ok=False, summary=summary,
                  notes=list(notes), remedy=remedy)


def _accepted(action: str, mode: str, summary: str, *, running: bool | None = None,
              pid: int | None = None, notes: Sequence[str] = ()) -> Report:
    return Report(action=action, mode=mode, ok=True, summary=summary,
                  running=running, pid=pid, notes=list(notes))


# --------------------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------------------


def install(*, mode: str | None = None, runner: Runner | None = None,
            username: str | None = None, password: str | None = None,
            start: bool = True) -> Report:
    """Register the background host so it starts without the window.

    Leaving ``mode`` empty takes :func:`recommend`, which prefers whatever survives
    a logoff; naming one that this machine refuses is an error with the reason in
    it, not a silent downgrade.
    """
    kind = probe()
    if mode is not None and mode not in MODES:
        return _fail("install", str(mode), f"{mode!r} is not a mode",
                     remedy=f"choose one of: {', '.join(MODES)}")
    chosen = mode or recommend()
    downgrade = ""
    if mode is None and chosen == MODE_TASK_ACCOUNT and not (username and password):
        # Strongest is not the same as installable: a task that runs without you
        # has to say *who* runs it, and this call was given no answer.
        chosen = MODE_TASK
        downgrade = ("the most persistent mode here is task-account, but it needs "
                     "--username and --password to store, so the logon task was "
                     "installed instead — it ends when you log off")
    blocked = kind.blocker(chosen)
    if blocked:
        available = ", ".join(kind.options)
        return _fail("install", chosen, f"{chosen} is not available here: {blocked}",
                     remedy=f"run this from an elevated prompt, or install with "
                            f"--mode set to one of: {available}")

    # A stop request outlives the host it was written for. A request that names no
    # pid means "for whoever is listening", so the new host would drain itself at
    # its first heartbeat; one that names a dead pid is only stale. The marker is
    # cleared whole before anything new is started.
    stale_stop = service_host.clear_stop(force=True)
    service_host.write_launcher()
    if chosen == MODE_SERVICE:
        report = _install_service(runner)
    elif chosen == MODE_NSSM:
        report = _install_nssm(runner)
    elif chosen == MODE_TASK_ACCOUNT:
        report = _install_task(username=username, password=password, runner=runner,
                               task_account=True)
    elif chosen == MODE_TASK:
        report = _install_task(username=None, password=None, runner=runner,
                               task_account=False)
    else:
        report = _install_process(start=start)
    if not report.ok:
        return report

    report.notes.append(TRADE_OFFS[chosen])
    if stale_stop:
        report.notes.append("a stop request left by the previous host was cleared")
    if downgrade:
        report.notes.append(downgrade)
    report.notes.append("the host process stays visible in Task Manager; hiding a "
                        "process is not something this app does")
    report.notes.append(f"log: {get_config().service_log_path}")
    report.notes.extend(_record(chosen, machine_wide=chosen in (MODE_SERVICE,
                                                                MODE_NSSM)))
    if not start:
        report.notes.append("installed but not started: `pyfix360 service start`")
        return report
    if chosen == MODE_PROCESS:
        # Starting *is* installing here; asking for a start again would find no pid
        # file yet — the host has not had a moment to write one — and spawn a
        # second host onto the same queue.
        report.running = True
        return report
    started = control("start", runner=runner)
    report.running = started.running
    report.notes.append(f"start: {started.summary}")
    return report


def _record(mode: str, *, machine_wide: bool) -> list[str]:
    """Persist how the host was installed, so ``status`` can say it back.

    A machine account sees a different ``%LOCALAPPDATA%`` than you do, so a real
    service also needs the copy under ``%PROGRAMDATA%`` — that is the only way it
    can find the database it was installed against. Failing to write it is
    reported rather than swallowed, because a service that opens the wrong empty
    database is worse than one that refuses to start.
    """
    state = {
        "mode": mode,
        "data_dir": str(service_host.resolve_data_dir()),
        "python": sys.executable,
        "launcher": str(service_host.launcher_path()),
        "service_name": service_host.SERVICE_NAME,
        "task_name": service_host.TASK_NAME,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        notes = [f"install recorded in {service_host.write_state(state)}"]
    except OSError as exc:
        return [f"the install was not recorded ({exc}) — status will be wrong"]
    if machine_wide:
        try:
            notes.append(f"machine-wide pointer in "
                         f"{service_host.write_state(state, pointer=True)}")
        except OSError as exc:
            return notes + [f"the machine-wide copy failed ({exc}): a service "
                            f"would not find this data dir. Re-run install "
                            f"elevated."]
    return notes


def _install_service(runner: Runner | None) -> Report:
    """Register the host with the service control manager, through pywin32.

    pywin32 is not interchangeable with ``sc.exe`` here: ``InstallService`` writes
    the ``PythonClass`` registry value that ``pythonservice.exe`` reads back, and
    it is ``sc.exe`` that can set the recovery action which restarts the service
    if the loop ever dies.
    """
    try:                                            # pragma: no cover - needs admin
        import win32service
        import win32serviceutil

        win32serviceutil.InstallService(
            service_host.SERVICE_CLASS,
            service_host.SERVICE_NAME,
            service_host.DISPLAY_NAME,
            startType=win32service.SERVICE_AUTO_START,
            description=service_host.DESCRIPTION,
        )
    except Exception as exc:                        # noqa: BLE001 - surfaced below
        return _fail("install", MODE_SERVICE,
                     f"the service could not be registered: {exc}",
                     remedy="run this from an elevated prompt, or install with "
                            "--mode task")
    recovery = _run(["sc", "failure", service_host.SERVICE_NAME,
                     "reset=", "60",
                     "actions=", "restart/5000/restart/5000/restart/5000"], runner)
    notes = ([] if recovery.ok else
             ["it is registered but will not restart itself after a crash: "
              + recovery.message()])
    return _accepted("install", MODE_SERVICE,
                     f"service {service_host.SERVICE_NAME} registered", notes=notes)


def _install_nssm(runner: Runner | None) -> Report:
    """Register the host as a service through NSSM, for hosts without pywin32."""
    exe, arguments = _host_command()
    nssm = shutil.which("nssm") or "nssm"
    created = _run([nssm, "install", service_host.SERVICE_NAME, exe, *arguments],
                   runner)
    if not created.ok:
        return _fail("install", MODE_NSSM,
                     f"nssm could not register the service: {created.message()}",
                     remedy="nssm.exe must be the 64-bit build, and this prompt "
                            "must be elevated")
    notes = []
    configured = _run([nssm, "set", service_host.SERVICE_NAME, "AppDirectory",
                       str(service_host.resolve_data_dir())], runner)
    if not configured.ok:
        notes.append(f"the working directory was not set: {configured.message()}")
    return _accepted("install", MODE_NSSM,
                     f"service {service_host.SERVICE_NAME} registered by nssm",
                     notes=notes)


def _install_task(*, username: str | None, password: str | None,
                  runner: Runner | None, task_account: bool) -> Report:
    """Register a scheduled task: autostart without administrator rights.

    Without stored credentials the task can only run "when the user is logged on",
    which is precisely the limit the commission asks to avoid — so that variant
    says so, instead of claiming to be a service.
    """
    mode = MODE_TASK_ACCOUNT if task_account else MODE_TASK
    if task_account and not (username and password):
        return _fail("install", mode,
                     "a task that survives logoff has to say which account runs it",
                     remedy="pyfix360 service install --mode task-account "
                            "--username NAME --password-ask")
    exe, arguments = _host_command()
    argv = ["schtasks", "/create", "/tn", service_host.TASK_NAME, "/f",
            "/sc", "onstart" if task_account else "onlogon",
            "/tr", _command_line(exe, arguments)]
    if task_account:
        argv += ["/ru", str(username), "/rp", str(password)]
    created = _run(argv, runner)
    if not created.ok:
        return _fail("install", mode,
                     f"the scheduled task was not created: {created.message()}",
                     remedy="install with --mode task, which needs no rights, or "
                            "with an elevated prompt for --mode service")
    when = "at boot, with stored credentials" if task_account else "at logon"
    return _accepted("install", mode,
                     f"scheduled task {service_host.TASK_NAME} created ({when})")


def _install_process(*, start: bool) -> Report:
    """The mode that installs nothing — it only has to start something.

    There is no launcher to register, so "install" and "start" are the same act
    here and the report says which of the two just happened.
    """
    if not start:
        return _accepted("install", MODE_PROCESS,
                         "recorded, but this mode has nothing to register: "
                         "`pyfix360 service start` will spawn the host")
    pid = spawn_detached()
    if pid is None:
        return _fail("install", MODE_PROCESS, "the background host would not start",
                     remedy="run `pyfix360 service run` and read what it says")
    return _accepted("install", MODE_PROCESS, f"background host started as pid {pid}",
                     running=True, pid=pid)


def _host_command() -> tuple[str, list[str]]:
    """The interpreter and arguments that run the host with no window.

    ``pythonw.exe`` whenever it exists: a console interpreter started by a
    scheduled task leaves a black window on the desktop, which is the one thing
    this was asked not to do.
    """
    exe = Path(sys.executable)
    if os.name == "nt":
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            exe = windowless
    return str(exe), [str(service_host.launcher_path())]


def _command_line(exe: str, arguments: Sequence[str]) -> str:
    """A ``/tr`` value. Paths carry spaces — this one lives under a user profile —
    so each word is quoted here, and the task engine sees the whole string as one
    command. Long values are truncated silently by ``schtasks``, which is why the
    install writes a short launcher file instead of a long inline command.
    """
    return " ".join([_quote(exe), *[_quote(argument) for argument in arguments]])


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


# --------------------------------------------------------------------------------------
# start / stop
# --------------------------------------------------------------------------------------


def start(*, runner: Runner | None = None) -> Report:
    """Start whatever ``install`` registered."""
    return control("start", runner=runner)


def stop(*, force: bool = False, runner: Runner | None = None) -> Report:
    """Stop the host.

    For anything the scheduler or a console started, stopping is a *request*: a
    host killed mid-step leaves a half-applied repair with nothing to roll it back,
    while the same host finishing its step records the job properly. ``force`` is
    the answer for a host that never replies.
    """
    return control("stop", runner=runner, force=force)


def control(action: str, *, runner: Runner | None = None,
            force: bool = False) -> Report:
    mode = installed_mode()
    if not mode:
        return _fail(action, "", "the background host was never installed",
                     remedy="pyfix360 service install")
    if action == "start":
        return _start(mode, runner)
    return _stop(mode, runner, force)


def _start(mode: str, runner: Runner | None) -> Report:
    if mode == MODE_PROCESS:
        live = live_pid()
        if live is not None:
            return _accepted("start", MODE_PROCESS,
                             f"already running as pid {live}",
                             running=True, pid=live)
    # Starting is the operator asking for a running host, and a stop request left
    # from an earlier command says the opposite: the fresh host would read it and
    # drain itself at its first heartbeat. Clearing it is the whole point of the
    # request naming a pid — this is the one place a request with no pid could
    # still do harm.
    cleared = service_host.clear_stop(force=True)
    report = _launch(mode, runner)
    if cleared:
        report.notes.append("a stop request left from earlier was cleared")
    return report


def _launch(mode: str, runner: Runner | None) -> Report:
    if mode == MODE_SERVICE:
        return _scm_control("start", mode, lambda: _win32_service_call("StartService"))
    if mode == MODE_NSSM:
        result = _run([shutil.which("nssm") or "nssm", "start",
                       service_host.SERVICE_NAME], runner)
        return _report("start", mode, result)
    if mode in (MODE_TASK, MODE_TASK_ACCOUNT):
        result = _run(["schtasks", "/run", "/tn", service_host.TASK_NAME], runner)
        return _report("start", mode, result)
    return _install_process(start=True)


def _stop(mode: str, runner: Runner | None, force: bool) -> Report:
    if mode == MODE_SERVICE:
        return _scm_control("stop", mode, lambda: _win32_service_call("StopService"))
    if mode == MODE_NSSM:
        result = _run([shutil.which("nssm") or "nssm",
                       "kill" if force else "stop", service_host.SERVICE_NAME],
                      runner)
        return _report("stop", mode, result)
    if mode in (MODE_TASK, MODE_TASK_ACCOUNT):
        if not force:
            return _request_stop(mode)
        result = _run(["schtasks", "/end", "/tn", service_host.TASK_NAME], runner)
        report = _report("stop", mode, result)
        if report.ok:
            report.running = False
            report.notes.append("ended, not drained: a job that was mid-step is "
                                "recorded interrupted, not failed")
        return report
    live = live_pid()
    if live is None:
        return _accepted("stop", MODE_PROCESS, "not running", running=False)
    if not force:
        return _request_stop(mode)
    killer = (_run(["taskkill", "/pid", str(live), "/f"], runner) if os.name == "nt"
              else _run(["kill", str(live)], runner))
    if not killer.ok:
        return _fail("stop", MODE_PROCESS, f"the process would not end: "
                                           f"{killer.message()}",
                     remedy="`pyfix360 service status` shows the pid it is holding")
    return _accepted("stop", MODE_PROCESS, f"pid {live} ended", running=False, notes=[
        "killed, not drained: any job it held is recorded interrupted and a "
        "half-applied repair is not rolled back"])


def _request_stop(mode: str) -> Report:
    """Leave the marker file the host reads at its next heartbeat.

    The marker names the pid it is aimed at, taken from the host's own pid file.
    Without a name — a host that died before writing one, or a task whose process
    this prompt cannot see — the request means "whoever is listening", and the
    next install clears it.
    """
    live = live_pid()
    path = service_host.request_stop(for_pid=live)
    who = f"pid {live}" if live is not None else "the host that reads it"
    return _accepted("stop", mode, f"a stop was requested for {who} "
                                  f"({path.name})",
                     running=True, pid=live, notes=[
                         "the host finishes its current step first, so this can "
                         "take one poll cycle — `pyfix360 service status` says "
                         "when it has gone",
                         "if it never answers, `pyfix360 service stop --force` "
                         "ends the process"])


def _win32_service_call(name: str):                # pragma: no cover - needs the SCM
    import win32serviceutil

    return getattr(win32serviceutil, name)(service_host.SERVICE_NAME)


def _scm_control(action: str, mode: str, call: Callable[[], object]) -> Report:
    """Ask the service control manager, through pywin32 and not through text.

    pywin32 answers with numbers. ``sc.exe`` answers with words, and on a
    non-English Windows the words move: the same service that reads ``RUNNING`` in
    English is not guaranteed a parseable label anywhere else.
    """
    try:                                            # pragma: no cover - needs the SCM
        call()
    except ImportError:
        return _fail(action, mode,
                     "pywin32 is not installed, so this process cannot reach the "
                     "service control manager",
                     remedy="pip install 'pyfix360[service]', or use services.msc")
    except Exception as exc:                        # noqa: BLE001 - surfaced
        return _fail(action, mode, f"the service control manager refused: {exc}",
                     remedy="a limited prompt cannot start or stop a service; "
                            "use an elevated one")
    return _accepted(action, mode,
                     f"service {service_host.SERVICE_NAME} {action} requested")


def _report(action: str, mode: str, result: CommandResult) -> Report:
    if result.ok:
        return _accepted(action, mode,
                         f"{_LAUNCHER_LABEL.get(mode, mode)} accepted it")
    return _fail(action, mode, f"{action} was refused: {result.message()}",
                 remedy="`pyfix360 service status` shows what is registered")


# --------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------


def status(*, runner: Runner | None = None) -> Report:
    """What is installed, and whether it is running right now.

    Two different questions with two different sources: the launcher (the SCM, the
    task engine) knows what it *thinks* is running, and the host's own pid file
    knows what is actually here. They disagree whenever a host was killed or a
    service is still starting, so both are reported.
    """
    mode = installed_mode()
    if not mode:
        return _accepted("status", "", "not installed", running=False,
                         notes=["nothing will run in the background until "
                                "`pyfix360 service install` is run"])
    report = _accepted("status", mode, TRADE_OFFS[mode])
    live = live_pid()
    report.pid = live
    if mode == MODE_SERVICE:
        state = _service_state()
        report.running = state == "running"
        report.notes.append(f"service control manager says: {state or 'not registered'}")
    elif mode == MODE_NSSM:
        result = _run([shutil.which("nssm") or "nssm", "status",
                       service_host.SERVICE_NAME], runner)
        report.running = "SERVICE_RUNNING" in result.stdout
        report.notes.append(f"nssm says: "
                            f"{(result.stdout or result.stderr).strip() or 'no answer'}")
    elif mode in (MODE_TASK, MODE_TASK_ACCOUNT):
        state = _task_state(runner)
        report.running = state == "Running"
        report.notes.append(
            f"task scheduler says: {state or 'nothing — the task may not exist'}")
    else:
        report.running = live is not None
    if live is not None:
        report.notes.append(f"a host is running as pid {live}, started "
                            f"{read_host_info().get('started_at', 'unknown')}")
    elif report.running:
        report.notes.append("the launcher says it is running but no host has "
                            "written a pid — it is probably still starting")
    asked, aimed_at = service_host.stop_request()
    if asked:
        who = f"pid {aimed_at}" if aimed_at is not None else "whoever is running"
        report.notes.append(
            f"a stop is pending for {who}"
            + ("; the host finishes its current step first" if live is not None
               else " — no live host answers it, so `pyfix360 service start` "
                    "or `install` clears the leftover"))
    return report


def _service_state() -> str:                        # pragma: no cover - needs the SCM
    """The service's state, as a word translated from a number."""
    try:
        import win32service
        import win32serviceutil

        state = win32serviceutil.QueryServiceStatus(service_host.SERVICE_NAME)[0]
        return {
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_START_PENDING: "start pending",
            win32service.SERVICE_STOP_PENDING: "stop pending",
            win32service.SERVICE_RUNNING: "running",
            win32service.SERVICE_CONTINUE_PENDING: "continue pending",
            win32service.SERVICE_PAUSE_PENDING: "pause pending",
            win32service.SERVICE_PAUSED: "paused",
        }.get(state, f"code {state}")
    except ImportError:
        return "pywin32 missing"
    except Exception:                                # noqa: BLE001 - not registered
        return ""


def _task_state(runner: Runner | None) -> str:
    """``Running`` / ``Ready`` for the task, read out of its XML.

    ``schtasks`` will print a plain listing instead, but that listing is localised
    while the XML is not, and guessing a running host from a translated label is
    how an operator ends up with two hosts and one queue.
    """
    result = _run(["schtasks", "/query", "/tn", service_host.TASK_NAME, "/xml"],
                  runner)
    if not result.ok:
        return ""
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return ""
    for element in root.iter("Status"):
        return (element.text or "").strip()
    return ""


def installed_mode() -> str:
    """The mode ``install`` recorded, or ``""`` when nothing was installed."""
    return str(service_host.read_state().get("mode") or "")


def read_host_info() -> dict:
    """What the running host said about itself, or ``{}``."""
    return service_host.read_pid()


def live_pid() -> int | None:
    """The pid of a host that is alive, or ``None``.

    The file is read *and* probed, because a crashed host leaves its file behind
    and either source alone would be wrong: trusting the file reports a dead
    daemon as running, trusting nothing at all reports a running one as dead.
    """
    from pyfix360.services import job_service

    pid = read_host_info().get("pid")
    return pid if isinstance(pid, int) and job_service.pid_alive(pid) else None


# --------------------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------------------


def uninstall(*, runner: Runner | None = None) -> Report:
    """Remove what was registered, then forget the install record.

    Stopping first is not politeness: a running host that has been deregistered
    keeps working and keeps holding jobs while nothing can any longer tell it to
    stop.
    """
    mode = installed_mode()
    if not mode:
        return _fail("uninstall", "", "nothing is installed")
    stopped = control("stop", runner=runner)
    if mode == MODE_SERVICE:
        ok, detail = _remove_service()
    elif mode == MODE_NSSM:
        result = _run([shutil.which("nssm") or "nssm", "remove",
                       service_host.SERVICE_NAME, "confirm"], runner)
        ok, detail = result.ok, result.message()
    elif mode in (MODE_TASK, MODE_TASK_ACCOUNT):
        result = _run(["schtasks", "/delete", "/tn", service_host.TASK_NAME, "/f"],
                      runner)
        ok, detail = result.ok, result.message()
    else:
        ok, detail = True, stopped.summary
    if not ok:
        return _fail("uninstall", mode, f"it is still registered: {detail}",
                     remedy="removing a service needs an elevated prompt")
    notes = ["jobs already on the queue were left there, and no job history "
             "was deleted"]
    if mode in (MODE_PROCESS, MODE_TASK, MODE_TASK_ACCOUNT):
        # The stop asked for above is a request the host reads at its next
        # heartbeat, so the marker has to stay exactly where it is: clearing it
        # here would cancel the stop this uninstall just asked for.
        notes.append("the host was asked to stop and finishes its current step "
                    "first — it may still be running for a few seconds")
    else:
        # A service or NSSM host answers the service control manager and never
        # reads the marker file, so a request left here is only ever a leftover —
        # and a leftover aimed at no particular pid would stop the next host at
        # birth.
        service_host.clear_stop(force=True)
    notes.extend(_forget())
    draining = mode in (MODE_PROCESS, MODE_TASK, MODE_TASK_ACCOUNT)
    return _accepted("uninstall", mode, f"{mode} removed",
                     running=None if draining else False, notes=notes)


def _remove_service() -> tuple[bool, str]:          # pragma: no cover - needs admin
    try:
        import win32serviceutil

        win32serviceutil.RemoveService(service_host.SERVICE_NAME)
        return True, ""
    except ImportError:
        return False, ("pywin32 is not installed, so this process cannot "
                       "deregister a service")
    except Exception as exc:                        # noqa: BLE001 - surfaced
        return False, str(exc)


def _forget() -> list[str]:
    """Remove the install record from both places it may have been written.

    Deleted rather than blanked: the host reads the machine-wide copy first, so an
    empty file left behind there would hide the record of the next, per-user
    install and ``status`` would report a host that is running as not installed.
    """
    return [f"the record is still in {path}, which this account cannot delete"
            for path in service_host.forget_state()]


# --------------------------------------------------------------------------------------
# the log, and the detached process
# --------------------------------------------------------------------------------------


def log_tail(limit: int = 40) -> list[str]:
    """The last few host log lines, for an operator standing in a console.

    Read backwards rather than forwards: the file rotates at two megabytes, so on
    a long-lived host the earliest lines are the least interesting it has.
    """
    path = get_config().service_log_path
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return [f"no host log yet at {path}"]
    lines = [line.decode(errors="replace") for line in raw.splitlines()]
    return lines[-limit:] if limit > 0 else lines


def spawn_detached() -> int | None:
    """Start the host as a windowless child and return its pid.

    The child is given no handles to this console and no process group of its own
    to be interrupted by, which is exactly why stopping it is a request through a
    file rather than a Ctrl-C.
    """
    exe, arguments = _host_command()
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:                                            # pragma: no cover - spawns a host
        pid = subprocess.Popen([exe, *arguments], **kwargs).pid
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("the background host would not start: %s", exc)
        return None
    # The child needs a moment to import itself, and until it has, it has no pid
    # file — so ``status`` run immediately after a successful install would say the
    # host it just started is not running. Record the pid now; the host rewrites it.
    service_host.write_pid(for_pid=pid)
    return pid
