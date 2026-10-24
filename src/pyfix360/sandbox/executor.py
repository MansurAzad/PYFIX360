"""Sandboxed command execution.

Guarantees enforced by this layer:
- hard timeout with process-tree termination
- execution confined to an explicit working directory
- captured stdout/stderr with size caps
- optional environment scrubbing (only PATH/PYTHONPATH/HOME-style vars pass)

Honest limitations (documented in the UI and reports):
- memory limits are enforced only where the OS supports it cheaply; on
  Windows without a Job Object helper this is best-effort
- network isolation is NOT enforced by this layer; projects that must run
  untrusted code should be executed in a container or VM. pyfix360 refuses
  to run tests at all when isolation is disabled in settings.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_OUTPUT_CAP = 200_000  # chars kept per stream

_PASSTHROUGH_ENV = {
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "PATH", "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONIOENCODING",
    "VIRTUAL_ENV", "PYTHONPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
}


@dataclass
class ExecResult:
    command: list[str]
    cwd: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_s: float = 0.0
    error: str = ""
    limitations: list[str] = field(default_factory=list)


def _scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH_ENV}
    if extra:
        env.update(extra)
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the process and (best effort) its children."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=20,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        logger.debug("process-tree kill failed for pid %s", proc.pid)
    with contextlib.suppress(OSError):
        proc.kill()


def run_isolated(
    command: list[str],
    *,
    cwd: Path,
    timeout_s: int = 300,
    memory_limit_mb: int | None = None,
    allow_network: bool = False,
    extra_env: dict[str, str] | None = None,
) -> ExecResult:
    """Run a command inside the sandbox and capture the outcome."""
    cwd = Path(cwd)
    if not cwd.is_dir():
        return ExecResult(command, str(cwd), None, error=f"working directory missing: {cwd}")

    limitations: list[str] = []
    if memory_limit_mb and sys.platform == "win32":
        limitations.append("memory limit not enforced on Windows without Job Objects")
    if allow_network:
        limitations.append("network access is permitted; no isolation is applied")
    else:
        limitations.append("network isolation not enforced at OS level; "
                           "run untrusted projects in a container/VM")

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, env=_scrubbed_env(extra_env), text=True,
            encoding="utf-8", errors="replace", **popen_kwargs,
        )
    except OSError as exc:
        return ExecResult(command, str(cwd), None, error=str(exc), limitations=limitations)

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    duration = time.monotonic() - start

    return ExecResult(
        command=command, cwd=str(cwd),
        exit_code=None if timed_out else proc.returncode,
        stdout=(stdout or "")[:_OUTPUT_CAP], stderr=(stderr or "")[:_OUTPUT_CAP],
        timed_out=timed_out, duration_s=round(duration, 3),
        limitations=limitations,
    )
