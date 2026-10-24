"""Tests for the sandboxed command executor.

These assert the guarantees the module actually makes — hard timeout with
process-tree termination, environment scrubbing, output capping, working-directory
confinement — and that the known limitations (no OS-level network isolation,
best-effort memory limits on Windows) are disclosed rather than hidden.
"""

import sys
import time

import pytest

from pyfix360.sandbox import executor


def test_successful_command_captures_stdout(tmp_path):
    result = executor.run_isolated(
        [sys.executable, "-c", "print('hello sandbox')"],
        cwd=tmp_path, timeout_s=60,
    )
    assert result.exit_code == 0
    assert "hello sandbox" in result.stdout
    assert result.timed_out is False
    assert result.error == ""
    assert result.duration_s >= 0


def test_nonzero_exit_code_is_reported(tmp_path):
    result = executor.run_isolated(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        cwd=tmp_path, timeout_s=60,
    )
    assert result.exit_code == 3
    assert result.timed_out is False


def test_stderr_is_captured(tmp_path):
    result = executor.run_isolated(
        [sys.executable, "-c", "import sys; print('boom', file=sys.stderr)"],
        cwd=tmp_path, timeout_s=60,
    )
    assert "boom" in result.stderr


def test_missing_working_directory_is_refused(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = executor.run_isolated([sys.executable, "-c", "print(1)"],
                                   cwd=missing, timeout_s=10)
    assert result.exit_code is None
    assert "working directory missing" in result.error


def test_timeout_kills_the_process(tmp_path):
    start = time.monotonic()
    result = executor.run_isolated(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path, timeout_s=2,
    )
    elapsed = time.monotonic() - start
    assert result.timed_out is True
    assert result.exit_code is None
    # must return promptly rather than waiting for the 60s sleep
    assert elapsed < 30


def test_output_is_capped(tmp_path):
    result = executor.run_isolated(
        [sys.executable, "-c",
         "import sys; sys.stdout.write('x' * 400000); sys.stdout.flush()"],
        cwd=tmp_path, timeout_s=60,
    )
    assert len(result.stdout) <= executor._OUTPUT_CAP


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("PYFIX_TEST_SECRET", "must-not-leak")
    result = executor.run_isolated(
        [sys.executable, "-c",
         "import os; print(os.environ.get('PYFIX_TEST_SECRET', 'ABSENT'))"],
        cwd=tmp_path, timeout_s=60,
    )
    assert "ABSENT" in result.stdout
    assert "must-not-leak" not in result.stdout


def test_extra_env_is_passed_through(tmp_path):
    result = executor.run_isolated(
        [sys.executable, "-c", "import os; print(os.environ['PYFIX_INJECTED'])"],
        cwd=tmp_path, timeout_s=60, extra_env={"PYFIX_INJECTED": "ok-value"},
    )
    assert "ok-value" in result.stdout


def test_runs_in_the_given_working_directory(tmp_path):
    marker = tmp_path / "marker.txt"
    marker.write_text("here", encoding="utf-8")
    result = executor.run_isolated(
        [sys.executable, "-c",
         "import pathlib; print(pathlib.Path('marker.txt').read_text())"],
        cwd=tmp_path, timeout_s=60,
    )
    assert "here" in result.stdout


def test_network_isolation_limitation_is_disclosed_by_default(tmp_path):
    result = executor.run_isolated([sys.executable, "-c", "print(1)"],
                                   cwd=tmp_path, timeout_s=30)
    joined = " ".join(result.limitations).lower()
    assert "network isolation not enforced" in joined


def test_network_allowed_limitation_is_disclosed(tmp_path):
    result = executor.run_isolated([sys.executable, "-c", "print(1)"],
                                   cwd=tmp_path, timeout_s=30, allow_network=True)
    joined = " ".join(result.limitations).lower()
    assert "network access is permitted" in joined


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific memory caveat")
def test_windows_memory_limit_caveat_is_disclosed(tmp_path):
    result = executor.run_isolated([sys.executable, "-c", "print(1)"],
                                   cwd=tmp_path, timeout_s=30, memory_limit_mb=256)
    joined = " ".join(result.limitations).lower()
    assert "memory limit not enforced" in joined


def test_command_failure_to_start_is_reported(tmp_path):
    result = executor.run_isolated(["definitely-not-a-real-binary-xyz"],
                                   cwd=tmp_path, timeout_s=10)
    assert result.exit_code is None
    assert result.error  # OSError captured, not raised
