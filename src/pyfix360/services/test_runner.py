"""Test execution and verification.

Test runs happen inside an isolated working copy, never against the user's
original tree. pytest is preferred; when no harness is detected the run is
recorded as NOT_RUN — an untested patch is never labelled verified.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from pyfix360.db.models import TestCaseRow, TestRunRow
from pyfix360.models import TestOutcome, utcnow
from pyfix360.sandbox.executor import run_isolated
from pyfix360.services import settings_service


def split_command(text: str) -> list[str]:
    """Split a command line into argv, honouring double quotes.

    Windows paths contain backslashes, so ``shlex`` in POSIX mode is not safe
    here: it would eat them. Only whitespace and quotes are special.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in (text or "").strip():
        if ch == '"':
            in_quotes = not in_quotes
            continue
        if ch.isspace() and not in_quotes:
            if current:
                parts.append("".join(current))
                current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def detect_test_command(root: Path, *, override: str = "",
                         tool_paths: dict[str, str] | None = None) -> list[str] | None:
    """Choose a test command based on what the project actually uses.

    A command configured in Settings → Tools wins over detection, and the
    configured pytest path wins over the one on ``PATH``.
    """
    override = (override or "").strip()
    if override:
        parts = split_command(override)
        if parts:
            return parts
    if tool_paths:
        configured = (tool_paths.get("pytest") or "").strip()
        if configured and Path(configured).exists():
            return [configured, "-q", "--no-header", "-p", "no:cacheprovider"]
    if (root / "conftest.py").exists() or (root / "pytest.ini").exists() or \
       any(root.glob("test_*.py")) or (root / "tests").is_dir():
        return ["python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
    if (root / "package.json").exists():
        pj = root / "package.json"
        try:
            data = json.loads(pj.read_text(encoding="utf-8-sig", errors="replace"))
        except json.JSONDecodeError:
            data = {}
        if "test" in (data.get("scripts") or {}):
            return ["npm", "test"]
    if any(root.glob("*_test.py")) or (root / "tests").is_dir():
        return ["python", "-m", "unittest", "discover", "-q"]
    return None


def run_tests(
    session: Session,
    project_id: int,
    workspace: Path,
    *,
    phase: str = "adhoc",
    patch_id: int | None = None,
    scan_id: int | None = None,
    timeout_s: int | None = None,
    memory_limit_mb: int | None = None,
    command: list[str] | None = None,
    confirmed: bool = False,
) -> TestRunRow:
    """Run the project's tests in the given workspace and persist results.

    Running a project's test suite executes that project's own code, so it is
    gated by the ``run_tests`` capability at the service boundary. Callers that
    are part of an action the user already approved — the test runs inside a
    repair — pass ``confirmed=True``; a direct run has to carry its own.
    """
    settings_service.check_access(session, "run_tests", confirmed=confirmed)
    settings = settings_service.get_settings(session)
    if timeout_s is None:
        timeout_s = settings.execution_timeout_s
    if memory_limit_mb is None:
        memory_limit_mb = settings.memory_limit_mb

    run = TestRunRow(project_id=project_id, scan_id=scan_id, patch_id=patch_id,
                     phase=phase)
    session.add(run)
    if not settings.isolate_execution:
        run.outcome = TestOutcome.ERROR.value
        run.command = ""
        run.log_excerpt = (
            "Execution isolation is disabled in Settings → Repair & Safety, so "
            "no test was run: running project code outside an isolated copy "
            "would execute it against your own tree."
        )
        run.started_at = run.finished_at = utcnow()
        session.flush()
        return run

    cmd = command or detect_test_command(
        Path(workspace), override=settings.test_command_override,
        tool_paths=settings.custom_tool_paths)
    if cmd is None:
        run.outcome = TestOutcome.NOT_RUN.value
        run.command = ""
        run.log_excerpt = "No test harness detected; tests NOT run."
        run.started_at = run.finished_at = utcnow()
        session.flush()
        return run

    run.command = " ".join(cmd)
    run.started_at = utcnow()
    session.flush()

    result = run_isolated(cmd, cwd=Path(workspace), timeout_s=timeout_s,
                          memory_limit_mb=memory_limit_mb,
                          allow_network=settings.allow_network_in_sandbox)

    run.finished_at = utcnow()
    run.duration_s = result.duration_s
    run.log_excerpt = (result.stdout + "\n" + result.stderr)[-8000:]

    passed = failed = errors = skipped = 0
    outcomes: list[tuple[str, str, str]] = []
    parsed = _parse_pytest_json(result.stdout)
    if parsed is not None:
        for item in parsed:
            outcome = {"passed": "passed", "failed": "failed", "error": "error",
                       "skipped": "skipped"}.get(item.get("outcome", ""), "error")
            outcomes.append((item.get("node_id", ""), outcome, item.get("message") or ""))
        passed = sum(1 for o in outcomes if o[1] == "passed")
        failed = sum(1 for o in outcomes if o[1] == "failed")
        errors = sum(1 for o in outcomes if o[1] == "error")
        skipped = sum(1 for o in outcomes if o[1] == "skipped")
    else:
        # fall back to summary-line parsing
        summary = _pytest_summary(result.stdout + "\n" + result.stderr)
        passed, failed, errors, skipped = summary

    run.passed, run.failed, run.errors, run.skipped = passed, failed, errors, skipped
    for node_id, outcome, message in outcomes:
        session.add(TestCaseRow(test_run_id=run.id, node_id=node_id,
                                outcome=outcome, message=message[:2000]))

    if result.timed_out:
        run.outcome = TestOutcome.ERROR.value
        run.log_excerpt = (run.log_excerpt + "\n[test run timed out]")[-8000:]
    elif failed == 0 and errors == 0 and (passed > 0):
        run.outcome = TestOutcome.PASSED.value
    elif failed > 0:
        run.outcome = TestOutcome.FAILED.value
    elif errors > 0:
        run.outcome = TestOutcome.ERROR.value
    elif passed == 0 and skipped == 0:
        run.outcome = TestOutcome.ERROR.value
        run.log_excerpt = (run.log_excerpt + "\n[test run produced no results]")[-8000:]
    else:
        run.outcome = TestOutcome.SKIPPED.value
    session.flush()
    return run


def _parse_pytest_json(stdout: str) -> list[dict] | None:
    """Parse pytest --json-report style output if present (best effort)."""
    text = stdout.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    tests = data.get("tests")
    return tests if isinstance(tests, list) else None


def _pytest_summary(text: str) -> tuple[int, int, int, int]:
    """Extract pass/fail/error/skip counts from textual pytest output."""
    import re

    passed = failed = errors = skipped = 0
    for m in re.finditer(r"(\d+) passed", text):
        passed = max(passed, int(m.group(1)))
    for m in re.finditer(r"(\d+) failed", text):
        failed = max(failed, int(m.group(1)))
    for m in re.finditer(r"(\d+) errors?", text):
        errors = max(errors, int(m.group(1)))
    for m in re.finditer(r"(\d+) skipped", text):
        skipped = max(skipped, int(m.group(1)))
    return passed, failed, errors, skipped


def compare_runs(baseline: TestRunRow | None, post: TestRunRow | None) -> str:
    """Classify post-repair test results against the baseline."""
    if post is None or post.outcome == TestOutcome.NOT_RUN.value:
        return "inconclusive"
    if baseline is None or baseline.outcome == TestOutcome.NOT_RUN.value:
        # no baseline: only trust an all-green post run
        return "passed" if post.outcome == TestOutcome.PASSED.value else "failed"
    if post.failed > baseline.failed or post.errors > baseline.errors:
        return "regression"
    if post.outcome == TestOutcome.PASSED.value:
        return "passed" if (post.failed + post.errors) == 0 else "regression"
    if (post.failed + post.errors) <= (baseline.failed + baseline.errors) \
            and post.passed >= baseline.passed:
        return "improved_not_clean"
    return "failed"
