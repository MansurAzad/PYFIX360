"""Repair workflow orchestration.

Implements the 13-step repair contract from the master specification:

1. record finding + evidence          (scan phase already did)
2. determine root cause / related     (root_cause_group correlation)
3. create repair plan                 (plan_repair)
4. snapshot / isolated working copy   (create_snapshot + workspace copy)
5. generate minimal patch             (builtin fixer or AI proposal)
6. present diff + risk                (Patch row + RepairResult)
7. validate syntax/types/static rules (compile + optional ruff)
8. apply in the isolated workspace    (never the original tree)
9. run targeted + regression tests    (sandboxed runner)
10. compare against baseline           (compare_runs)
11. stage/commit on success           (only with explicit approval)
12. roll back on failure              (workspace discarded, snapshot kept)
13. persist result, logs, limits      (rows + audit events)

An untested patch is NEVER reported as verified.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.config import get_config
from pyfix360.db.models import (
    FindingRow,
    PatchRow,
    ProjectRow,
    RepairPlanRow,
    SnapshotRow,
    TestRunRow,
)
from pyfix360.models import (
    Finding,
    Patch,
    PatchStatus,
    RepairPlan,
    RepairPlanStep,
    RepairResult,
    TestOutcome,
)
from pyfix360.patching import fixers
from pyfix360.patching.diff import diff_stats, unified_diff
from pyfix360.services import audit, settings_service, snapshot_service, test_runner

logger = logging.getLogger(__name__)

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "node_modules", "*.pyc", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "*.tar.gz",
)


class RepairError(Exception):
    pass


class ApprovalRequired(RepairError):
    """The change needs an approval the caller has not given yet.

    ``categories`` names the approval switches that are still missing, so the
    interface can ask the user about exactly those and retry.
    """

    def __init__(self, categories: list[str]) -> None:
        self.categories = list(categories)
        pretty = ", ".join(self.categories)
        super().__init__(
            f"this change needs explicit approval for: {pretty} "
            "(Settings → Repair & Safety)")


#: Which switch in Settings governs which kind of change.
APPROVAL_KEYS: dict[str, str] = {
    "destructive": "require_approval_destructive",
    "security": "require_approval_security",
    "dependency": "require_approval_dependency",
    "migration": "require_approval_migration",
    "production": "require_approval_production",
}

#: Path fragments that mark a file as part of a production deployment.
_PRODUCTION_MARKERS = ("production", "prod/", "deploy", "live/")


def approval_categories(finding: Finding, *, destructive: bool = False) -> list[str]:
    """Which approval categories a repair of this finding falls into.

    ``destructive`` is decided by the caller because it depends on the diff:
    a change that deletes more code than it adds is destructive.
    """
    categories: list[str] = []
    category = (finding.category.value if hasattr(finding.category, "value")
                else str(finding.category))
    if category == "security":
        categories.append("security")
    elif category == "dependency":
        categories.append("dependency")
    path = (finding.file_path or "").lower().replace("\\", "/")
    if "migrat" in path or "migrat" in (finding.rule_id or "").lower():
        categories.append("migration")
    if any(marker in path for marker in _PRODUCTION_MARKERS):
        categories.append("production")
    if destructive:
        categories.append("destructive")
    return categories


def required_approvals(session: Session, plan_id: int) -> list[str]:
    """The approvals this repair needs, knowable before it starts.

    The destructive check depends on the diff and is therefore *not* included
    here; it is raised later by :func:`execute_repair` if the patch turns out
    to remove code.
    """
    plan = session.get(RepairPlanRow, plan_id)
    if plan is None:
        raise RepairError(f"repair plan {plan_id} not found")
    if not plan.finding_ids:
        raise RepairError("plan has no findings")
    primary = session.get(FindingRow, plan.finding_ids[0])
    if primary is None:
        raise RepairError("primary finding missing")
    settings = settings_service.get_settings(session)
    return [category for category in approval_categories(_finding_model(primary))
            if getattr(settings, APPROVAL_KEYS[category])]


# ------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------


def _finding_model(row: FindingRow) -> Finding:
    from pyfix360.models import Confidence
    from pyfix360.models import FindingCategory as FC
    from pyfix360.models import Severity as SV

    return Finding(
        id=row.id, scan_id=row.scan_id, project_id=row.project_id,
        stable_key=row.stable_key, rule_id=row.rule_id,
        category=FC(row.category), severity=SV(row.severity),
        confidence=Confidence(row.confidence), title=row.title,
        description=row.description, file_path=row.file_path,
        line_start=row.line_start, line_end=row.line_end, symbol=row.symbol,
        tool=row.tool, root_cause_group=row.root_cause_group,
    )


def _make_workspace(project: ProjectRow, plan_id: int) -> Path:
    cfg = get_config()
    ws = cfg.workspace_dir / f"proj{project.id}_plan{plan_id}"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project.root_path, ws, ignore=_COPY_IGNORE, dirs_exist_ok=False)
    return ws


# ------------------------------------------------------------------------------------
# planning
# ------------------------------------------------------------------------------------


def plan_repair(session: Session, finding_id: int) -> RepairPlanRow:
    """Create an explainable repair plan for a finding."""
    row = session.get(FindingRow, finding_id)
    if row is None:
        raise RepairError(f"finding {finding_id} not found")
    finding = _finding_model(row)
    steps = fixers.build_plan_steps(finding)
    auto = fixers.can_auto_fix(finding.rule_id)
    related = session.scalars(
        select(FindingRow).where(
            FindingRow.root_cause_group == row.root_cause_group,
            FindingRow.id != row.id,
        )
    ).all()
    rationale = (
        f"Rule {finding.rule_id} at {finding.file_path}:{finding.line_start}. "
        + ("A builtin minimal fixer is available and will be validated before "
           "anything is applied. " if auto else "No builtin fixer; manual or AI-assisted "
           "repair required. ")
        + (f"{len(related)} related finding(s) share this root cause."
           if related else "")
    )
    plan = RepairPlanRow(
        project_id=row.project_id, scan_id=row.scan_id,
        finding_ids=[row.id] + [r.id for r in related],
        title=f"Repair {finding.rule_id} in {finding.file_path}:{finding.line_start}",
        rationale=rationale,
        steps=steps,
        estimated_risk="low" if auto else "medium",
    )
    session.add(plan)
    row.status = "planned"
    session.flush()
    audit.record(session, "repair.plan", project_id=row.project_id,
                 target=f"finding#{row.id}", details=plan.title, actor="user")
    return plan


# ------------------------------------------------------------------------------------
# execution
# ------------------------------------------------------------------------------------


def execute_repair(
    session: Session,
    plan_id: int,
    *,
    timeout_s: int | None = None,
    memory_limit_mb: int | None = None,
    max_retries: int | None = None,
    apply_to_original: bool = False,
    confirmed: bool = False,
    approvals: set[str] | None = None,
) -> RepairResult:
    """Execute a repair plan end-to-end in an isolated workspace.

    ``apply_to_original`` copies a *verified* patch back into the user's own
    tree, so it is the one act in this module that leaves the workspace. It is
    gated by the ``apply_to_original`` capability at the service boundary: a
    caller that has the user's agreement passes ``confirmed=True``, and a
    policy that says *blocked* refuses no matter who asks. The gate is checked
    before any work starts, so a refused apply costs nothing.

    ``approvals`` names the extra approvals the caller already has (for
    example ``{"security"}``). A change that needs one it does not carry
    raises :class:`ApprovalRequired` before anything is written.
    """
    settings = settings_service.get_settings(session)
    if timeout_s is None:
        timeout_s = settings.execution_timeout_s
    if memory_limit_mb is None:
        memory_limit_mb = settings.memory_limit_mb
    if max_retries is None:
        max_retries = settings.max_repair_retries
    granted = set(approvals or ())

    if not settings.isolate_execution:
        raise RepairError(
            "execution isolation is disabled in Settings → Repair & Safety; "
            "refusing to run the project's own code without an isolated copy."
        )
    if apply_to_original:
        settings_service.check_access(session, "apply_to_original",
                                      confirmed=confirmed)
    plan = session.get(RepairPlanRow, plan_id)
    if plan is None:
        raise RepairError(f"repair plan {plan_id} not found")
    project = session.get(ProjectRow, plan.project_id)
    if project is None:
        raise RepairError(f"project {plan.project_id} not found")
    if not plan.finding_ids:
        raise RepairError("plan has no findings")

    primary = session.get(FindingRow, plan.finding_ids[0])
    if primary is None:
        raise RepairError("primary finding missing")
    finding = _finding_model(primary)

    missing = [category for category in approval_categories(finding)
               if getattr(settings, APPROVAL_KEYS[category]) and category not in granted]
    if missing:
        raise ApprovalRequired(missing)

    result = RepairResult(plan=_plan_model(plan), status=PatchStatus.PROPOSED)

    # 4. recoverable snapshot BEFORE anything is touched
    if settings.require_snapshot_before_write:
        snap = snapshot_service.create_snapshot(session, project.id,
                                                f"pre-repair plan#{plan.id}")
        result.snapshot = _snapshot_model(snap)
    else:
        audit.record(session, "repair.snapshot_skipped", project_id=project.id,
                     target=f"plan#{plan.id}",
                     details="snapshot requirement disabled in Settings → Repair & Safety",
                     actor="system", risk="warn")

    # 4b. isolated working copy
    ws = _make_workspace(project, plan.id)

    try:
        # 5. minimal patch
        if not finding.file_path:
            raise RepairError("finding has no file; cannot auto-repair")
        target = ws / finding.file_path
        if not target.is_file():
            raise RepairError(f"file missing in workspace: {finding.file_path}")
        original = target.read_text(encoding="utf-8-sig", errors="replace")
        had_bom = target.read_bytes().startswith(b"\xef\xbb\xbf")
        outcome = fixers.propose_fix(original, finding)
        if outcome is None:
            raise RepairError(
                f"no safe builtin fix for {finding.rule_id}; "
                "review the manual guidance in the plan"
            )
        diff = unified_diff(finding.file_path, original, outcome.new_source)
        added, removed = diff_stats(diff)
        if removed > added and settings.require_approval_destructive \
                and "destructive" not in granted:
            raise ApprovalRequired(["destructive"])
        patch_row = PatchRow(
            repair_plan_id=plan.id, project_id=project.id, finding_id=primary.id,
            status=PatchStatus.PROPOSED.value, file_path=finding.file_path,
            diff=diff, added_lines=added, removed_lines=removed, risk=outcome.risk,
            origin="builtin", explanation=outcome.explanation,
        )
        session.add(patch_row)
        session.flush()
        result.patch = _patch_model(patch_row)
        primary.status = "fix_proposed"

        # 7. validate syntax before applying anywhere
        compile(outcome.new_source, str(target), "exec")

        # 9. baseline test run on the PRISTINE workspace state (pre-patch)
        baseline = _run_baseline(project, plan, ws, timeout_s, memory_limit_mb, session)
        result.baseline_test_run = _test_run_model(baseline) if baseline else None

        # 8. apply inside the isolated workspace only
        # preserve a leading BOM so we never silently corrupt an encoding the
        # project's editor/toolchain relies on.
        if had_bom:
            target.write_text("\ufeff" + outcome.new_source, encoding="utf-8")
        else:
            target.write_text(outcome.new_source, encoding="utf-8")
        patch_row.status = PatchStatus.APPLIED.value

        # 9b/10. post-patch tests, retried on transient infrastructure errors
        post = None
        attempt = 0
        while attempt <= max_retries:
            post = test_runner.run_tests(
                session, project.id, ws, phase="post_patch", patch_id=patch_row.id,
                scan_id=plan.scan_id, timeout_s=timeout_s,
                memory_limit_mb=memory_limit_mb, confirmed=True,
            )
            if post.outcome != TestOutcome.ERROR.value or not post.log_excerpt:
                break
            attempt += 1
        assert post is not None
        result.post_test_run = _test_run_model(post)

        # 11. compare against baseline
        verdict = test_runner.compare_runs(baseline, post)
        if verdict == "passed":
            patch_row.status = PatchStatus.VERIFIED.value
            primary.status = "verified"
            result.status = PatchStatus.VERIFIED
            result.verification = "passed"
            result.message = ("Patch applied in isolated workspace and verified by tests. "
                              + ("Changes applied to the original project."
                                 if apply_to_original else
                                 "Original project untouched; approve to apply."))
        elif verdict == "improved_not_clean":
            patch_row.status = PatchStatus.APPLIED.value
            primary.status = "fix_applied"
            result.status = PatchStatus.APPLIED
            result.verification = "inconclusive"
            result.message = ("Post-repair test results improved but the suite is not "
                              "clean; NOT labelled verified.")
        else:
            raise RepairError(f"verification failed ({verdict}); rolling back")

    except ApprovalRequired:
        # Not a failure of the repair: the change needs an approval the
        # caller did not bring. Nothing was written to the workspace.
        shutil.rmtree(ws, ignore_errors=True)
        session.flush()
        raise
    except (RepairError, SyntaxError, OSError) as exc:
        # 12. rollback: discard the workspace; original tree was never modified
        shutil.rmtree(ws, ignore_errors=True)
        if "patch_row" in locals() and patch_row is not None:
            patch_row.status = PatchStatus.ROLLED_BACK.value
            result.patch = _patch_model(patch_row)
        primary.status = "failed"
        result.status = PatchStatus.ROLLED_BACK
        result.verification = "failed"
        result.message = str(exc)
        audit.record(session, "repair.rollback", project_id=project.id,
                     target=f"plan#{plan.id}", details=str(exc), risk="warn",
                     actor="system")
        session.flush()
        return result

    # 11b. optionally apply verified changes to the original project
    if apply_to_original and result.status == PatchStatus.VERIFIED and result.patch:
        src = ws / result.patch.file_path
        root = Path(project.root_path).resolve()
        dst = (root / result.patch.file_path).resolve()
        if settings.block_write_outside_project and not dst.is_relative_to(root):
            result.message += (
                f" Not copied to {dst}: the path is outside the project and "
                "'block writes outside the project' is enabled.")
            audit.record(session, "repair.apply_blocked", project_id=project.id,
                         target=result.patch.file_path,
                         details=f"outside project root {root}",
                         actor="system", risk="warn")
        else:
            shutil.copy2(src, dst)
            result.message += f" Copied patched file to {dst}."
            audit.record(session, "repair.apply_to_project", project_id=project.id,
                         target=result.patch.file_path, details=f"plan#{plan.id}",
                         actor="user", risk="warn")
            if settings.auto_commit_verified:
                staged, note = snapshot_service.stage_paths(root, [result.patch.file_path])
                result.message += (" Staged in git." if staged else f" Not staged: {note}")
                audit.record(session, "repair.stage", project_id=project.id,
                             target=result.patch.file_path,
                             details=note if not staged else "git add",
                             actor="system")

    # keep workspace for inspection; log where it is
    result.message += f" Workspace: {ws}"
    audit.record(
        session, "repair.execute", project_id=project.id, target=f"plan#{plan.id}",
        details=f"status={result.status.value} verification={result.verification} "
                f"patch={result.patch.file_path if result.patch else None}",
        actor="system",
    )
    session.flush()
    return result


def _run_baseline(project, plan, ws: Path, timeout_s, memory_limit_mb, session) -> TestRunRow | None:
    """Run tests on the pristine workspace copy before patching."""
    pristine = ws.parent / (ws.name + "_baseline")
    shutil.rmtree(pristine, ignore_errors=True)
    shutil.copytree(ws, pristine, ignore=_COPY_IGNORE)
    run = test_runner.run_tests(
        session, project.id, pristine, phase="baseline", scan_id=plan.scan_id,
        timeout_s=timeout_s, memory_limit_mb=memory_limit_mb, confirmed=True,
    )
    shutil.rmtree(pristine, ignore_errors=True)
    return run


# ------------------------------------------------------------------------------------
# model conversions (row -> domain)
# ------------------------------------------------------------------------------------


def _plan_model(row: RepairPlanRow) -> RepairPlan:
    return RepairPlan(
        id=row.id, project_id=row.project_id, scan_id=row.scan_id,
        finding_ids=list(row.finding_ids or []), title=row.title,
        rationale=row.rationale,
        steps=[RepairPlanStep(**s) for s in (row.steps or [])],
        estimated_risk=row.estimated_risk, created_at=row.created_at,
    )


def _patch_model(row: PatchRow) -> Patch:
    return Patch(
        id=row.id, repair_plan_id=row.repair_plan_id, project_id=row.project_id,
        finding_id=row.finding_id, status=PatchStatus(row.status),
        file_path=row.file_path, diff=row.diff, added_lines=row.added_lines,
        removed_lines=row.removed_lines, risk=row.risk, origin=row.origin,
        explanation=row.explanation, created_at=row.created_at,
    )


def _snapshot_model(row: SnapshotRow):
    from pyfix360.models import Snapshot

    return Snapshot(
        id=row.id, project_id=row.project_id, label=row.label, kind=row.kind,
        path=row.path, git_commit=row.git_commit, created_at=row.created_at,
        restored_at=row.restored_at,
    )


def _test_run_model(row: TestRunRow):
    from pyfix360.models import TestCaseResult, TestRun

    return TestRun(
        id=row.id, project_id=row.project_id, patch_id=row.patch_id, phase=row.phase,
        command=row.command, passed=row.passed, failed=row.failed, errors=row.errors,
        skipped=row.skipped, duration_s=row.duration_s,
        outcome=TestOutcome(row.outcome), log_excerpt=row.log_excerpt,
        results=[TestCaseResult(node_id=r.node_id, outcome=TestOutcome(r.outcome),
                                duration_s=r.duration_s, message=r.message)
                 for r in row.results],
    )


# ------------------------------------------------------------------------------------
# rollback of already-applied patches
# ------------------------------------------------------------------------------------


def rollback_patch(session: Session, patch_id: int, *,
                   confirmed: bool = False) -> SnapshotRow:
    """Roll an applied patch back by restoring its pre-repair snapshot.

    A rollback rewrites the user's own files, so it is gated by the
    ``rollback`` capability: *ask* wants the user's agreement (``confirmed``),
    *blocked* refuses outright.
    """
    settings_service.check_access(session, "rollback", confirmed=confirmed)
    patch = session.get(PatchRow, patch_id)
    if patch is None:
        raise RepairError(f"patch {patch_id} not found")
    if patch.repair_plan_id is None:
        raise RepairError("patch has no repair plan / snapshot link")
    plan = session.get(RepairPlanRow, patch.repair_plan_id)
    snaps = snapshot_service.list_snapshots(session, patch.project_id)
    pre = next((s for s in snaps if s.label == f"pre-repair plan#{plan.id}"), None) if plan else None
    if pre is None:
        raise RepairError("no snapshot found for this patch")
    restored = snapshot_service.restore_snapshot(session, pre.id)
    patch.status = PatchStatus.ROLLED_BACK.value
    if patch.finding_id:
        finding = session.get(FindingRow, patch.finding_id)
        if finding:
            finding.status = "rolled_back"
    audit.record(session, "patch.rollback", project_id=patch.project_id,
                 target=f"patch#{patch.id}", details=f"snapshot#{restored.id}",
                 actor="user", risk="warn")
    session.flush()
    return restored


def list_patches(session: Session, project_id: int) -> list[PatchRow]:
    return list(session.scalars(
        select(PatchRow).where(PatchRow.project_id == project_id)
        .order_by(PatchRow.id.desc())
    ))
