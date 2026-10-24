"""Tests for P2: the settings that are now actually honoured.

Each test here pairs a switch in the Settings Center with the code that
consumes it, so a future change that quietly disconnects the two is caught.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from pyfix360.analysis import tool_adapters
from pyfix360.db.models import FindingRow, SnapshotRow
from pyfix360.models import FindingStatus, Severity
from pyfix360.services import (
    findings_service,
    project_service,
    scan_service,
    settings_service,
    snapshot_service,
    test_runner,
)

# ---------------------------------------------------------------------------
# scanning settings
# ---------------------------------------------------------------------------


def test_include_glob_excludes_everything_else(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    st = settings_service.get_settings(session)
    st.scan_include_globs = ["**/nothing_matches_this/**"]
    settings_service.save_settings(session, st)

    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    assert scan.findings_count == 0


def test_exclude_glob_removes_a_file(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    st = settings_service.get_settings(session)
    st.scan_exclude_globs = ["**/app.py"]
    settings_service.save_settings(session, st)

    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    assert scan.findings_count == 0


def test_glob_matching_covers_names_and_paths():
    assert scan_service.matches_any("src/app.py", ["**/*.py"]) is True
    assert scan_service.matches_any("src/app.py", ["*.py"]) is True
    assert scan_service.matches_any("src/app.py", ["src/**"]) is True
    assert scan_service.matches_any("src/app.py", ["**/*.js"]) is False
    assert scan_service.matches_any("src/app.py", []) is False


def test_glob_matching_normalises_windows_separators():
    assert scan_service.matches_any("src\\app.py", ["**/*.py"]) is True


def test_large_files_are_skipped_not_analysed(session, tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    (root / "huge.py").write_text("x = 1\n" * 5000, encoding="utf-8")
    project = project_service.import_project(session, str(root), name="big")
    st = settings_service.get_settings(session)
    st.scan_max_file_kb = 1
    settings_service.save_settings(session, st)

    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    assert scan.files_skipped >= 1
    assert scan.findings_count == 0


def test_max_files_caps_the_scan(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    st = settings_service.get_settings(session)
    st.scan_max_files = 1
    settings_service.save_settings(session, st)

    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    assert scan.files_scanned <= 1


def test_auto_workers_sizes_the_pool_from_the_core_count():
    assert scan_service._auto_workers(0) == 1
    assert scan_service._auto_workers(1) == 1
    assert scan_service._auto_workers(4) >= 2


# ---------------------------------------------------------------------------
# findings settings
# ---------------------------------------------------------------------------


def _finding(session, scan_id: int, *, severity: str, status: str = "detected",
             root: str = "rc-1") -> FindingRow:
    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key=f"k-{severity}-{status}-{root}",
        rule_id="r", category="logic", severity=severity,
        confidence="high", status=status, title="t", description="d",
        file_path="a.py", line_start=1, line_end=2, tool="builtin",
        root_cause_group=root, dedup_count=1,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def scan_id(session, sample_project):
    """A real scan, so the findings have a scan to belong to."""
    project = project_service.import_project(session, str(sample_project), name="p")
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    return scan.id


def _mine(rows) -> list[str]:
    """The severities of the rows this module added, ignoring the scan's own."""
    return [r.severity for r in rows if r.stable_key.startswith("k-")]


def test_min_severity_hides_the_less_severe_rows(session, scan_id):
    _finding(session, scan_id, severity="high")
    _finding(session, scan_id, severity="low")
    st = settings_service.get_settings(session)
    st.min_severity = Severity.HIGH
    settings_service.save_settings(session, st)

    rows = findings_service.visible_findings(session, scan_id=scan_id)
    assert _mine(rows) == ["high"]


def test_lowering_the_floor_brings_the_rows_back(session, scan_id):
    _finding(session, scan_id, severity="high")
    _finding(session, scan_id, severity="low")
    st = settings_service.get_settings(session)
    st.min_severity = Severity.HIGH
    settings_service.save_settings(session, st)
    assert _mine(findings_service.visible_findings(session, scan_id=scan_id)) == ["high"]

    st.min_severity = Severity.INFO
    settings_service.save_settings(session, st)
    assert _mine(findings_service.visible_findings(session, scan_id=scan_id)) == [
        "high", "low"]


def test_false_positives_are_hidden_by_default(session, scan_id):
    _finding(session, scan_id, severity="high", status="false_positive")
    _finding(session, scan_id, severity="high", root="rc-2")
    rows = findings_service.visible_findings(session, scan_id=scan_id)
    assert len(_mine(rows)) == 1
    assert _mine(rows)[0] == "high"


def test_false_positives_are_shown_when_the_switch_is_on(session, scan_id):
    _finding(session, scan_id, severity="high", status="false_positive")
    st = settings_service.get_settings(session)
    st.show_false_positive_rules = True
    settings_service.save_settings(session, st)
    rows = findings_service.visible_findings(session, scan_id=scan_id)
    assert len(_mine(rows)) == 1


def test_visible_findings_are_ordered_most_severe_first(session, scan_id):
    _finding(session, scan_id, severity="low", root="a")
    _finding(session, scan_id, severity="critical", root="b")
    _finding(session, scan_id, severity="medium", root="c")
    rows = findings_service.visible_findings(session, scan_id=scan_id)
    order = [r.severity for r in rows]
    assert order == sorted(order, key=lambda s: findings_service.severity_rank(s))


def test_grouping_collapses_one_root_cause_into_one_group(session, scan_id):
    _finding(session, scan_id, severity="high", root="rc-1")
    _finding(session, scan_id, severity="low", root="rc-1")
    _finding(session, scan_id, severity="medium", root="rc-2")
    rows = [r for r in findings_service.visible_findings(session, scan_id=scan_id)
            if r.stable_key.startswith("k-")]
    groups = findings_service.group_by_root_cause(rows)
    assert [g.count for g in groups] == [2, 1]
    assert groups[0].severity == "high"


def test_acknowledge_info_only_touches_info_findings(session, scan_id):
    info = _finding(session, scan_id, severity="info", root="a")
    low = _finding(session, scan_id, severity="low", root="b")
    changed = findings_service.acknowledge_info(session, scan_id)
    assert changed >= 1
    assert info.status == FindingStatus.ACKNOWLEDGED.value
    assert low.status == "detected"


def test_acknowledge_info_leaves_work_in_progress_alone(session, scan_id):
    row = _finding(session, scan_id, severity="info", status="planned", root="a")
    findings_service.acknowledge_info(session, scan_id)
    assert row.status == "planned"


def test_a_scan_auto_acknowledges_info_when_the_setting_is_on(
        session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    st = settings_service.get_settings(session)
    st.auto_acknowledge_info = True
    settings_service.save_settings(session, st)

    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    infos = session.query(FindingRow).filter_by(scan_id=scan.id,
                                                severity="info").all()
    for row in infos:
        assert row.status == FindingStatus.ACKNOWLEDGED.value


# ---------------------------------------------------------------------------
# repair approvals
# ---------------------------------------------------------------------------


def test_approval_categories_read_the_finding(session, scan_id):
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="k", rule_id="py-eval",
        category="security", severity="high", confidence="high", status="detected",
        title="t", description="d", file_path="app.py", line_start=1, line_end=1,
        tool="builtin", root_cause_group="rc", dedup_count=1,
    )
    session.add(row)
    session.flush()
    model = repair_service._finding_model(row)
    assert "security" in repair_service.approval_categories(model)


def test_a_production_path_needs_the_production_approval(session, scan_id):
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="k2", rule_id="r",
        category="logic", severity="low", confidence="low", status="detected",
        title="t", description="d", file_path="deploy/production.yml",
        line_start=1, line_end=1, tool="builtin", root_cause_group="rc",
        dedup_count=1,
    )
    session.add(row)
    session.flush()
    model = repair_service._finding_model(row)
    assert "production" in repair_service.approval_categories(model)


def test_a_destructive_patch_needs_its_own_approval():
    from pyfix360.services import repair_service

    assert repair_service.APPROVAL_KEYS["destructive"] == \
        "require_approval_destructive"


def test_required_approvals_reflects_the_switches(session, scan_id):
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="s", rule_id="r",
        category="security", severity="high", confidence="high", status="detected",
        title="t", description="d", file_path="app.py", line_start=1, line_end=1,
        tool="builtin", root_cause_group="rc", dedup_count=1,
    )
    session.add(row)
    session.flush()
    plan = repair_service.plan_repair(session, row.id)
    session.flush()

    st = settings_service.get_settings(session)
    st.require_approval_security = True
    settings_service.save_settings(session, st)
    assert "security" in repair_service.required_approvals(session, plan.id)

    st.require_approval_security = False
    settings_service.save_settings(session, st)
    assert "security" not in repair_service.required_approvals(session, plan.id)


def test_repair_refuses_when_isolation_is_disabled(session, sample_project):
    from pyfix360.services import repair_service

    project = project_service.import_project(session, str(sample_project), name="p")
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    row = session.query(FindingRow).filter_by(scan_id=scan.id).first()
    plan = repair_service.plan_repair(session, row.id)
    session.flush()

    st = settings_service.get_settings(session)
    st.isolate_execution = False
    settings_service.save_settings(session, st)

    with pytest.raises(repair_service.RepairError) as exc:
        repair_service.execute_repair(session, plan.id, confirmed=True)
    assert "isolation" in str(exc.value).lower()


def test_a_missing_approval_raises_approval_required(session, scan_id):
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="sec", rule_id="r",
        category="security", severity="high", confidence="high", status="detected",
        title="t", description="d", file_path="app.py", line_start=1, line_end=1,
        tool="builtin", root_cause_group="rc", dedup_count=1,
    )
    session.add(row)
    session.flush()
    plan = repair_service.plan_repair(session, row.id)
    session.flush()

    st = settings_service.get_settings(session)
    st.require_approval_security = True
    settings_service.save_settings(session, st)

    with pytest.raises(repair_service.ApprovalRequired) as exc:
        repair_service.execute_repair(session, plan.id, confirmed=True)
    assert "security" in exc.value.categories


def test_granting_the_approval_lets_the_repair_proceed(session, scan_id):
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="sec2", rule_id="r",
        category="security", severity="high", confidence="high", status="detected",
        title="t", description="d", file_path="app.py", line_start=1, line_end=1,
        tool="builtin", root_cause_group="rc", dedup_count=1,
    )
    session.add(row)
    session.flush()
    plan = repair_service.plan_repair(session, row.id)
    session.flush()

    st = settings_service.get_settings(session)
    st.require_approval_security = True
    settings_service.save_settings(session, st)

    result = repair_service.execute_repair(session, plan.id, confirmed=True,
                                           approvals={"security"})
    assert result.status is not None


def test_the_cli_asks_for_the_approvals_it_needs(session, scan_id, monkeypatch):
    """The CLI must not confirm a gated step on the user's behalf."""
    from pyfix360.cli import _confirm_approvals
    from pyfix360.services import repair_service

    row = FindingRow(
        scan_id=scan_id, project_id=1, stable_key="sec3", rule_id="r",
        category="security", severity="high", confidence="high", status="detected",
        title="t", description="d", file_path="app.py", line_start=1, line_end=1,
        tool="builtin", root_cause_group="rc", dedup_count=1,
    )
    session.add(row)
    session.flush()
    plan = repair_service.plan_repair(session, row.id)
    session.flush()

    st = settings_service.get_settings(session)
    st.require_approval_security = True
    settings_service.save_settings(session, st)

    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "y")
    granted = _confirm_approvals(session, plan.id)
    assert granted is not None
    assert "security" in granted
    assert asked, "the CLI must ask, not assume"


def test_tests_refuse_to_run_when_isolation_is_disabled(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    st = settings_service.get_settings(session)
    st.isolate_execution = False
    settings_service.save_settings(session, st)

    run = test_runner.run_tests(session, project.id, Path(project.root_path))
    assert run.outcome == "error"
    assert "isolation" in (run.log_excerpt or "").lower()


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def test_prune_removes_old_archives_only(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    root = Path(project.root_path)
    archive = SnapshotRow(
        project_id=project.id, kind="archive", label="old",
        path=str(root / "old.zip"), created_at=_days_ago(90),
    )
    git = SnapshotRow(
        project_id=project.id, kind="git", label="old-commit",
        path=str(root / ".git"), created_at=_days_ago(90),
    )
    fresh = SnapshotRow(
        project_id=project.id, kind="archive", label="new",
        path=str(root / "new.zip"), created_at=_days_ago(1),
    )
    session.add_all([archive, git, fresh])
    session.flush()
    archive_id, git_id, fresh_id = archive.id, git.id, fresh.id

    removed = snapshot_service.prune_snapshots(session, retention_days=30)
    assert removed == 1
    assert session.get(SnapshotRow, archive_id) is None
    assert session.get(SnapshotRow, git_id) is not None
    assert session.get(SnapshotRow, fresh_id) is not None


def test_prune_does_nothing_when_the_window_is_zero(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    session.add(SnapshotRow(
        project_id=project.id, kind="archive", label="old",
        path=str(Path(project.root_path) / "old.zip"), created_at=_days_ago(90),
    ))
    session.flush()
    assert snapshot_service.prune_snapshots(session, retention_days=0) == 0


def _days_ago(days: int):
    from datetime import datetime, timedelta

    return datetime.now(UTC) - timedelta(days=days)


# ---------------------------------------------------------------------------
# test command and tool paths
# ---------------------------------------------------------------------------


def test_split_command_keeps_quoted_arguments_together():
    assert test_runner.split_command("pytest -q") == ["pytest", "-q"]
    assert test_runner.split_command('pytest -k "a b"') == ["pytest", "-k", "a b"]
    assert test_runner.split_command("") == []


def test_split_command_keeps_windows_backslashes():
    assert test_runner.split_command(r"C:\tools\pytest.exe -q") == [
        r"C:\tools\pytest.exe", "-q"]


def test_the_configured_test_command_wins_over_detection(tmp_path):
    cmd = test_runner.detect_test_command(tmp_path, override="mytest --fast")
    assert cmd[:2] == ["mytest", "--fast"]


def test_a_configured_tool_path_wins_over_path(tmp_path):
    exe = tmp_path / "ruff.exe"
    exe.write_text("", encoding="utf-8")
    resolved = tool_adapters.resolve_tool("ruff", {"ruff": str(exe)})
    assert resolved == str(exe)


def test_a_configured_tool_path_that_does_not_exist_is_not_ignored(tmp_path):
    missing = str(tmp_path / "nope" / "ruff.exe")
    assert tool_adapters.resolve_tool("ruff", {"ruff": missing}) is None


def test_an_unconfigured_tool_resolves_from_path():
    assert tool_adapters.resolve_tool("definitely-not-a-real-tool-xyz") is None


# ---------------------------------------------------------------------------
# notification settings
# ---------------------------------------------------------------------------


def test_scan_notifications_follow_the_switch(session, data_dir):
    from pyfix360.ui.context import AppContext

    ctx = AppContext()
    seen: list[tuple[str, str]] = []
    ctx.notification_requested.connect(lambda t, b: seen.append((t, b)))

    st = settings_service.get_settings(session)
    st.notify_on_scan_complete = False
    settings_service.save_settings(session, st)
    session.commit()
    assert ctx.notify("scan", "Scan finished", "3 findings") is False
    assert seen == []

    st.notify_on_scan_complete = True
    settings_service.save_settings(session, st)
    session.commit()
    assert ctx.notify("scan", "Scan finished", "3 findings") is True
    assert seen == [("Scan finished", "3 findings")]


def test_repair_notifications_follow_their_own_switch(session, data_dir):
    from pyfix360.ui.context import AppContext

    ctx = AppContext()
    st = settings_service.get_settings(session)
    st.notify_on_scan_complete = True
    st.notify_on_repair_verified = False
    settings_service.save_settings(session, st)
    session.commit()

    assert ctx.notify("scan", "Scan finished", "x") is True
    assert ctx.notify("repair", "Repair verified", "x") is False


def test_an_unknown_notification_kind_is_refused(session, data_dir):
    from pyfix360.ui.context import AppContext

    ctx = AppContext()
    assert ctx.notify("something-else", "t", "b") is False


# ---------------------------------------------------------------------------
# the appearance settings
# ---------------------------------------------------------------------------


def test_the_accent_override_is_applied_to_the_theme():
    from pyfix360.ui.theme import get_theme, theme_with_accent

    base = get_theme("dark")
    themed = theme_with_accent(base, "#ff8800")
    assert themed.accent == "#ff8800"
    # The label colour is derived from the new accent, so a custom colour can
    # never silently make a button label unreadable.
    assert themed.button_text in ("#000000", "#ffffff")


def test_an_empty_accent_override_leaves_the_theme_alone():
    from pyfix360.ui.theme import get_theme, theme_with_accent

    base = get_theme("dark")
    assert theme_with_accent(base, "  ") is base
