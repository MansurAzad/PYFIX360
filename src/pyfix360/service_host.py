"""The process that keeps working after the window closes.

Three launch shapes share this module, and they are not interchangeable — the
difference is *who else is logged in* and *who can install it*:

``pyfix360 service run``
    Foreground, in a console. What you use to watch the loop while developing.

A detached child process or a scheduled task
    Starts when the task does, per user, no window and no taskbar entry. Needs no
    administrator rights. A task created for the current user ends at logoff; only
    a task with stored credentials, or a real service, survives it.

``BackgroundService``, hosted by the Service Control Manager
    A genuine Windows service in session 0: no window, no icon, running whether or
    not anybody is logged on. Installing one needs administrator rights, and the
    process then runs as a *machine* account, which is why the data directory has
    to be handed over explicitly.

That handover is the reason :func:`resolve_data_dir` exists. A session-0 service
sees ``%LOCALAPPDATA%`` of its own account, so the default path would silently
open a different, empty database. The install step writes the real path to
``host.json`` in two places — the user's data directory, and ``%PROGRAMDATA%``
which is reachable from any session — and the host reads whichever it can find.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pyfix360 import APP_NAME, __version__
from pyfix360.config import AppConfig, default_data_dir, get_config, set_config

SERVICE_NAME = "PYFIX360Background"
DISPLAY_NAME = "PYFIX 360 Background"
DESCRIPTION = (
    "Runs PYFIX 360 autopilot jobs with no window open. Every step is written to "
    "the audit trail and to logs/service.log; nothing here bypasses the access "
    "policy.")
#: How the SCM is told which class to host. Must stay importable as a module path.
SERVICE_CLASS = "pyfix360.service_host.BackgroundService"
TASK_NAME = "PYFIX360-Background"

LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 5

#: The live pid, written by the host itself. See :func:`write_pid`.
PID_FILENAME = "host.pid"
#: A stop asked for without killing the process. See :func:`request_stop`.
STOP_FILENAME = "stop.request"
#: The generated script a task or service is pointed at. See :func:`write_launcher`.
LAUNCHER_NAME = "run_host.py"

logger = logging.getLogger("pyfix360.service")


# --------------------------------------------------------------------------------------
# where the data lives
# --------------------------------------------------------------------------------------


def _pointer_path() -> Path:
    """The machine-wide handover file, reachable from session 0."""
    base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / "PYFIX360" / "host.json"


def _user_state_path() -> Path:
    return default_data_dir() / "service" / "host.json"


def read_state() -> dict:
    for path in (_pointer_path(), _user_state_path()):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def write_state(state: dict, *, pointer: bool = False) -> Path:
    """Record how the host was installed, so ``status`` can say it back.

    ``pointer=True`` is the machine-wide copy a service needs. It is written only
    by ``service install``, which is already an administrator action — an
    unprivileged process normally cannot create it, and that failure is reported
    rather than papered over.
    """
    path = _pointer_path() if pointer else _user_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload.setdefault("written_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# who is running, and how to ask it to stop
# --------------------------------------------------------------------------------------


def _service_file(name: str) -> Path:
    return get_config().service_dir / name


def write_pid(for_pid: int | None = None) -> Path:
    """Record the live pid, so ``status`` can answer without asking the SCM.

    The host stamps this itself rather than the launcher doing it, which is what
    makes the file mean the same thing for a process started from a console, by a
    scheduled task, or by the service control manager. Which of those installed it
    is the install record's business, not this file's.

    ``for_pid`` exists for the one case the host cannot cover: a spawner that has
    a pid a second before the child has imported enough to write one, and would
    otherwise report its own successful start as "not running".
    """
    path = _service_file(PID_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": os.getpid() if for_pid is None else for_pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_dir": str(get_config().data_dir),
        "host": _hostname(),
    }, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_pid() -> dict:
    try:
        data = json.loads(_service_file(PID_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_pid() -> None:
    with contextlib.suppress(OSError):
        _service_file(PID_FILENAME).unlink()


def request_stop(for_pid: int | None = None) -> Path:
    """Ask a running host to finish, without a process kill.

    A hard kill would leave its job half-applied with no rollback, so stopping is
    a request the host reads at its next heartbeat. The request *names the pid it
    is aimed at*: without that, a stop asked for while a host was still starting
    would be swallowed by that host's own startup, and a stop meant for the
    process being replaced would land on its replacement instead.

    ``for_pid=None`` is for the one case where the pid is not yet knowable — it
    means "whoever is listening".
    """
    path = _service_file(STOP_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": for_pid,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2, sort_keys=True), encoding="utf-8")
    return path


def stop_request() -> tuple[bool, int | None]:
    """(was a stop asked for, and which pid was it aimed at)."""
    path = _service_file(STOP_FILENAME)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False, None
    try:
        return True, json.loads(raw).get("pid")
    except ValueError:
        return True, None            # made by hand: aimed at whoever reads it


def stop_requested(for_pid: int | None = None) -> bool:
    asked, target = stop_request()
    return asked and (target is None or target == (for_pid or os.getpid()))


def clear_stop(for_pid: int | None = None, *, force: bool = False) -> bool:
    """Remove a stop request — but not one that is still aimed at somebody else.

    ``force`` is what ``service install`` uses: a request left over from the host
    being replaced must not stop the new one at birth.
    """
    asked, target = stop_request()
    if not asked:
        return False
    if not force and target is not None and target != (for_pid or os.getpid()):
        return False
    _service_file(STOP_FILENAME).unlink(missing_ok=True)
    return True


def forget_state() -> list[str]:
    """Delete the install record from both places it may have been written.

    Deleted rather than blanked, because :func:`read_state` prefers the
    machine-wide copy: an empty file left there would hide the record of the next,
    per-user install. A machine-wide copy written by a service account may not be
    deletable from a limited prompt, so what could not be removed is returned for
    the caller to report.
    """
    left = []
    for path in (_pointer_path(), _user_state_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            left.append(str(path))
    return left


def launcher_path() -> Path:
    return _service_file(LAUNCHER_NAME)


def write_launcher() -> Path:
    """Write the tiny file a scheduled task or service host is pointed at.

    Both launchers start a *fresh* interpreter, and a fresh interpreter does not
    know where this package lives — true in a source checkout, harmless once
    installed. Keeping the path insert in a generated file also keeps the command
    line short: ``schtasks /tr`` truncates anything long, and a truncated command
    fails as a task that silently never starts.
    """
    root = str(Path(__file__).resolve().parent.parent)
    path = launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '"""Start the PYFIX 360 background host. Written by `pyfix360 service install`."""\n'
        "import sys\n\n"
        f"if {root!r} not in sys.path:\n"
        f"    sys.path.insert(0, {root!r})\n\n"
        "from pyfix360.service_host import main\n\n"
        "raise SystemExit(main())\n",
        encoding="utf-8")
    return path


def resolve_data_dir() -> Path:
    """The data directory this process should use.

    Ordered by how deliberately it was set: an environment override, then a
    recorded install, then the per-user default.
    """
    if os.environ.get("PYFIX360_DATA_DIR"):
        return default_data_dir()
    stored = read_state().get("data_dir")
    if stored:
        candidate = Path(str(stored)).expanduser()
        if candidate.is_dir():
            return candidate
    return default_data_dir()


def bind_data_dir(data_dir: Path | None = None) -> AppConfig:
    """Point the whole process at ``data_dir`` before anything reads the config."""
    cfg = AppConfig(data_dir=data_dir or resolve_data_dir())
    cfg.ensure_dirs()
    set_config(cfg)
    from pyfix360.db import reset_engine

    reset_engine()
    return cfg


# --------------------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------------------


def setup_logging(cfg: AppConfig, *, level: str | None = None,
                  to_console: bool | None = None) -> Path:
    """Send the host's log to ``logs/service.log`` and return its path.

    A separate file, not the desktop's ``pyfix360.log``: a background run is the
    one thing an operator reads *after* the fact, usually while the desktop app is
    writing its own lines to the same directory.
    """
    cfg.ensure_dirs()
    path = cfg.logs_dir / "service.log"
    handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES,
                                  backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, str(level or cfg.log_level).upper(),
                          logging.INFO))
    if to_console is None:
        # A windowless host has no usable stderr; writing to it would raise.
        to_console = bool(sys.stderr)
    if to_console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(handler.formatter)
        root.addHandler(stream)
    return path


# --------------------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------------------


def build_host(**kwargs):
    """A configured :class:`~pyfix360.services.background_service.Background`."""
    from pyfix360.db import init_db, session_scope
    from pyfix360.services import background_service, settings_service

    init_db()
    with session_scope() as s:
        settings_service.apply_from_config(s)
    return background_service.from_settings(session_factory=session_scope, **kwargs)


def _hostname() -> str:
    """Stamped onto a claimed job, so a log line says which machine did the work."""
    import socket

    return socket.gethostname()


def run_foreground(*, stop: threading.Event | None = None,
                   max_polls: int | None = None,
                   quiet: bool = False) -> int:
    """Poll the queue until told to stop. Returns the process exit code."""
    cfg = bind_data_dir()
    log_path = setup_logging(cfg, to_console=False if quiet else None)
    stop = stop or threading.Event()
    host = build_host(host=_hostname(), sleeper=stop.wait)
    _install_signals(stop)
    write_pid()

    logger.info("%s %s background host starting (data_dir=%s, poll=%.2fs, log=%s)",
                APP_NAME, __version__, cfg.data_dir, host.poll_s, log_path)
    try:
        # `stop_requested()` is the other half of stopping: a host with no window
        # and no console cannot be interrupted by Ctrl-C from whoever asked.
        counts = host.run(stop=lambda: stop.is_set() or stop_requested(),
                          max_polls=max_polls)
    except Exception:                              # noqa: BLE001 - logged, then exit
        logger.exception("the background host stopped because of an error")
        _abandon(host)
        return 1
    finally:
        clear_pid()
    # Whatever ended the loop, a job still held by this process would look alive
    # to readers until a reaper proved otherwise — say so now instead.
    _abandon(host)
    clear_stop()
    logger.info("background host stopping: %s", counts)
    return 0


def _install_signals(stop: threading.Event) -> None:
    """Make Ctrl-Break and service control end the loop cleanly."""
    def _handler(_signum, _frame) -> None:
        logger.info("termination requested; finishing the current step")
        stop.set()

    for name in ("SIGBREAK", "SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            # Not the main thread, or a host that forbids the signal at all.
            signal.signal(sig, _handler)


def _abandon(host) -> None:
    """Mark this host's own job interrupted so readers are not misled."""
    job = host.job
    if job is None:
        return
    from pyfix360.db import session_scope
    from pyfix360.models import JobStatus
    from pyfix360.services import job_service

    # A job sitting at an approval gate has nothing in flight, so saying "work may
    # be half-applied" about it would send the operator to check a step that never
    # started. Either way it is not resumable: only a queued row can be claimed.
    waiting = host.waiting
    detail = ("the background host stopped while this job waited for approval"
              if waiting else
              "the background host was terminated while holding this job")
    error = ("no step was in flight, and nothing needs rolling back — queue the "
             "job again once you have decided the approval"
             if waiting else
             "the host ended mid-run; work may be half-applied and has not been "
             "rolled back")
    try:
        with session_scope() as s:
            job_service.finish(s, job.id, JobStatus.INTERRUPTED,
                               detail=detail, error=error, actor="host")
    except Exception:                              # pragma: no cover - best effort
        logger.exception("could not record the abandoned job #%s", job.id)


# --------------------------------------------------------------------------------------
# Windows service hosting
# --------------------------------------------------------------------------------------

try:                                    # pragma: no cover - platform specific
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    _WIN32_SERVICES = True
except ImportError:                     # non-Windows, or pywin32 not installed
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None
    _WIN32_SERVICES = False


def services_supported() -> bool:
    """True when this process could host a real Windows service."""
    return _WIN32_SERVICES and sys.platform == "win32"


if _WIN32_SERVICES:                     # pragma: no cover - needs the SCM

    class BackgroundService(win32serviceutil.ServiceFramework):
        """PYFIX 360 as an actual service: session 0, no window, no icon.

        The loop runs on a worker thread while the service thread waits on the
        stop handle, because ``SvcStop`` has to answer the SCM promptly — a service
        that blocks in its stop path gets killed by the manager and looks crashed.
        """

        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = DESCRIPTION

        def __init__(self, args) -> None:
            super().__init__(args)
            self._stop_handle = win32event.CreateEvent(None, 0, 0, None)
            self._stopping = threading.Event()
            self._finished = threading.Event()
            self._host = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            logger.info("the service control manager asked this service to stop")
            self._stopping.set()
            win32event.SetEvent(self._stop_handle)

        def SvcDoRun(self) -> None:
            servicemanager.LogMsg(servicemanager.eventLogInformation, 0,
                                  f"{DISPLAY_NAME} starting")
            cfg = bind_data_dir()
            setup_logging(cfg, to_console=False)
            self._host = build_host(host=SERVICE_NAME, sleeper=self._wait)
            write_pid()
            worker = threading.Thread(target=self._loop, name="pyfix360-background",
                                      daemon=True)
            worker.start()
            win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
            self._finished.wait(10.0)
            clear_pid()
            _abandon(self._host)
            servicemanager.LogMsg(servicemanager.eventLogInformation, 0,
                                  f"{DISPLAY_NAME} stopped")

        def _loop(self) -> None:
            try:
                self.ReportServiceStatus(win32service.SERVICE_RUNNING)
                self._host.run(stop=self._stopping.is_set)
            except Exception:                    # noqa: BLE001 - logged for SCM
                logger.exception("the background loop crashed")
            finally:
                self._finished.set()

        def _wait(self, seconds: float) -> None:
            """Sleep that wakes immediately when the SCM asks us to stop."""
            self._stopping.wait(seconds)

else:
    BackgroundService = None             # type: ignore[assignment]


# --------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`python -m pyfix360.service_host …` — what the service binary launches.

    ``--svc`` is only used by a pywin32 console host and is never what the SCM
    starts: the manager runs ``pythonservice.exe`` with this class name, which
    calls :class:`BackgroundService` directly.
    """
    parser = argparse.ArgumentParser(
        prog="pyfix360-service",
        description="Run the PYFIX 360 background host (no window).")
    parser.add_argument("--once", action="store_true",
                        help="poll a bounded number of times and exit")
    parser.add_argument("--polls", type=int, default=3,
                        help="how many polls --once may take (default: 3)")
    parser.add_argument("--quiet", action="store_true",
                        help="log to the file only")
    parser.add_argument("--svc", action="store_true",
                        help="host the service in this process for debugging")
    parser.add_argument("--version", action="store_true",
                        help="print the version and exit")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    if args.svc:
        if BackgroundService is None:
            print("pywin32 is required to host a Windows service.\n"
                  "Install it with:  pip install 'pyfix360[service]'",
                  file=sys.stderr)
            return 2
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(BackgroundService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0

    return run_foreground(max_polls=args.polls if args.once else None,
                          quiet=args.quiet)


if __name__ == "__main__":               # pragma: no cover
    sys.exit(main())
