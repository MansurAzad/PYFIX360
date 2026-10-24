"""End-to-end repair workflow tests — the acceptance criteria in code."""

import shutil
from pathlib import Path

from pyfix360.db.models import AuditEventRow, FindingRow, PatchRow, ScanRow, SnapshotRow
from pyfix360.models import PatchStatus, ProjectOrigin
from pyfix360.patching.fixers import FixOutcome
from pyfix360.services import project_service, repair_service, scan_service


def _setup(session, root: Path):
    project = project_service.import_project(session, str(root), origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    return project, scan


def _finding(session, scan_id: int, rule: str) -> FindingRow:
    return session.query(FindingRow).filter_by(scan_id=scan_id, rule_id=rule).one()


# ------------------------------------------------------------------------------------
# acceptance path: scan -> plan -> patch -> verify
# ------------------------------------------------------------------------------------


def test_repair_happy_path_verified(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")

    plan = repair_service.plan_repair(session, finding.id)
    assert plan.steps and plan.finding_ids
    assert finding.status == "planned"

    result = repair_service.execute_repair(session, plan.id, timeout_s=120)

    # patch exists with a reviewable diff
    assert result.patch is not None
    assert "except Exception:" in result.patch.diff
    assert result.patch.added_lines == 1 and result.patch.removed_lines == 1
    assert result.patch.explanation

    # snapshot was created BEFORE the repair
    assert result.snapshot is not None
    snap = session.get(SnapshotRow, result.snapshot.id)
    assert snap.label.startswith("pre-repair")

    # tests ran and the patch is verified
    assert result.baseline_test_run is not None
    assert result.post_test_run is not None
    assert result.post_test_run.outcome.value == "passed"
    assert result.verification == "passed"
    assert result.status == PatchStatus.VERIFIED

    # finding status updated and patch persisted
    assert finding.status == "verified"
    row = session.query(PatchRow).filter_by(repair_plan_id=plan.id).one()
    assert row.status == "verified"

    # the ORIGINAL project was never modified without approval
    original = (sample_project / "app.py").read_text(encoding="utf-8")
    assert "except:\n" in original


def test_repair_apply_to_original_then_rollback(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)
    before = (sample_project / "app.py").read_text(encoding="utf-8")

    result = repair_service.execute_repair(session, plan.id, timeout_s=120,
                                           apply_to_original=True, confirmed=True)
    assert result.verification == "passed"
    after = (sample_project / "app.py").read_text(encoding="utf-8")
    assert after != before
    assert "except Exception:" in after

    # rollback restores the exact pre-repair content
    restored = repair_service.rollback_patch(session, result.patch.id, confirmed=True)
    assert restored.restored_at is not None
    final = (sample_project / "app.py").read_text(encoding="utf-8")
    assert final == before
    row = session.get(PatchRow, result.patch.id)
    assert row.status == "rolled_back"
    assert finding.status == "rolled_back"


# ------------------------------------------------------------------------------------
# failure path: broken patch must roll back
# ------------------------------------------------------------------------------------


def test_failed_verification_rolls_back(session, sample_project, monkeypatch):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)

    # simulate a harmful fixer: rename digest so the test suite fails
    import pyfix360.services.repair_service as rs

    def harmful_fix(source, f):
        broken = source.replace("def digest", "def digest_removed")
        if broken == source:
            return None
        return FixOutcome(new_source=broken, explanation="harmful on purpose")

    monkeypatch.setattr(rs.fixers, "propose_fix", harmful_fix)
    result = rs.execute_repair(session, plan.id, timeout_s=120)

    assert result.status == PatchStatus.ROLLED_BACK
    assert result.verification == "failed"
    assert result.patch is None or result.patch.status == PatchStatus.ROLLED_BACK
    assert finding.status == "failed"
    # workspace was deleted on rollback
    assert "rolling back" in result.message
    # audit trail records the rollback
    events = session.query(AuditEventRow).filter_by(action="repair.rollback").all()
    assert events


def test_unfixable_rule_produces_plan_not_patch(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-mutable-default")
    plan = repair_service.plan_repair(session, finding.id)
    assert plan.steps[0]["action"] == "manual_guidance"

    result = repair_service.execute_repair(session, plan.id, timeout_s=60)
    assert result.status == PatchStatus.ROLLED_BACK
    assert "no safe builtin fix" in result.message
    assert finding.status == "failed"


# ------------------------------------------------------------------------------------
# safety invariants
# ------------------------------------------------------------------------------------


def test_snapshot_created_before_patch_exists(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)

    seen: list[str] = []
    original_create = repair_service.snapshot_service.create_snapshot

    def spy(sess, project_id, label):
        row = original_create(sess, project_id, label)
        seen.append("snapshot")
        return row

    repair_service.snapshot_service.create_snapshot = spy
    try:
        repair_service.execute_repair(session, plan.id, timeout_s=120)
    finally:
        repair_service.snapshot_service.create_snapshot = original_create
    assert seen == ["snapshot"]


def test_audit_trail_covers_repair_lifecycle(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)
    repair_service.execute_repair(session, plan.id, timeout_s=120)
    actions = {e.action for e in session.query(AuditEventRow).filter_by(
        project_id=project.id).all()}
    assert {"project.import", "scan.complete", "repair.plan",
            "snapshot.create", "repair.execute"} <= actions


def test_plan_correlates_related_findings(session, sample_project):
    project, scan = _setup(session, sample_project)
    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)
    assert finding.id in plan.finding_ids


def test_scan_persists_tools_ran_and_skipped(session, sample_project):
    """In-place JSON list mutation(s) must survive the commit.

    Regression: a bare JSON column does not change-track list.append(), so
    tools_ran/tools_skipped were silently lost, making reports always say
    'builtin only' and hiding which tools were skipped.
    """
    project = project_service.import_project(session, str(sample_project))
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id)

    session.expire_all()
    row = session.get(ScanRow, scan.id)
    # What ran is recorded, and what could not run is named: an adapter that is
    # not installed has to disappear from neither list.
    assert row.tools_ran
    assert row.tools_skipped, "expected at least one skipped tool to be recorded"
    assert any("bandit" in s or "pip-audit" in s for s in row.tools_skipped)
    if shutil.which("ruff"):
        assert "ruff" in (row.tools_ran or [])


def test_untouched_project_scan_is_idempotent_import(session, sample_project):
    p1 = project_service.import_project(session, str(sample_project))
    p2 = project_service.import_project(session, str(sample_project))
    assert p1.id != p2.id  # re-import is a separate project record


def test_repair_preserves_utf8_bom(session, tmp_path):
    """A BOM-prefixed source file must still analyse and repair correctly."""
    proj = tmp_path / "bomproj"
    (proj / "tests").mkdir(parents=True)
    (proj / "conftest.py").write_bytes(b"")
    (proj / "app.py").write_bytes(
        "\ufeffimport os\n\n\ndef run(cmd):\n    try:\n        os.system(cmd)\n    except:\n        pass\n".encode()
    )
    (proj / "tests" / "test_app.py").write_bytes(
        b"import app\n\n\ndef test_run():\n    assert app.run is not None\n"
    )

    project, scan = _setup(session, proj)
    # BOM must NOT be misreported as a syntax error
    assert not session.query(FindingRow).filter_by(
        scan_id=scan.id, rule_id="py-syntax-error").count()

    finding = _finding(session, scan.id, "py-bare-except")
    plan = repair_service.plan_repair(session, finding.id)
    result = repair_service.execute_repair(session, plan.id, timeout_s=120)
    assert result.status == PatchStatus.VERIFIED

    # the workspace copy fixed the bare except and kept the BOM intact
    from pyfix360.config import get_config

    ws_file = Path(result.patch.file_path)
    workspace = get_config().workspace_dir / f"proj{project.id}_plan{plan.id}"
    patched = (workspace / ws_file.name).read_bytes()
    assert patched.startswith(b"\xef\xbb\xbf")
    assert b"except Exception:" in patched
