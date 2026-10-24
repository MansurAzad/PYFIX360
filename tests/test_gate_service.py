"""Tests for the quality-gate engine."""

from pyfix360.db.models import FindingRow
from pyfix360.models import ProjectOrigin, QualityGate, Severity
from pyfix360.services import gate_service, project_service, scan_service


def _scan(session, root):
    project = project_service.import_project(session, str(root), origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    return project, scan


def test_seed_defaults_is_idempotent(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    first = gate_service.seed_defaults(session, project.id)
    second = gate_service.seed_defaults(session, project.id)
    assert first, "expected starter gates to be created"
    assert second == [], "seeding twice must not duplicate gates"


def test_zero_critical_gate_fails_when_criticals_exist(session, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text(
        "def run(cmd):\n    try:\n        eval(cmd)\n    except:\n        pass\n",
        encoding="utf-8")
    project, scan = _scan(session, root)

    # force a critical finding if the analyzer found none
    if not session.query(FindingRow).filter_by(
            scan_id=scan.id, severity="critical").count():
        session.add(FindingRow(
            scan_id=scan.id, project_id=project.id, stable_key="k1",
            rule_id="test-rule", category="security", severity="critical",
            confidence="high", title="synthetic critical", file_path="app.py"))
        scan.findings_count = (scan.findings_count or 0) + 1
        session.flush()

    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="no crits", metric="max_critical",
        operator="<=", threshold=0, blocking=True))
    report = gate_service.evaluate(session, project.id)
    assert not report.passed
    failed = report.failed_blocking
    assert failed and failed[0].name == "no crits"
    # the failure must name the offending findings
    assert failed[0].finding_ids


def test_threshold_gate_passes_when_below(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="lenient total", metric="max_total_findings",
        operator="<=", threshold=10000, blocking=True))
    report = gate_service.evaluate(session, project.id)
    assert report.passed


def test_non_blocking_gate_does_not_fail_the_project(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="advisory", metric="max_total_findings",
        operator="==", threshold=0, blocking=False))
    report = gate_service.evaluate(session, project.id)
    assert report.passed, "a failing advisory gate must not fail the project"
    assert any(not r.passed for r in report.results)


def test_required_tools_gate_reports_missing_tool(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="must run ghost",
        metric="required_tools", operator="==", threshold=0,
        params={"tools": ["ghost-tool"]}, blocking=True))
    report = gate_service.evaluate(session, project.id)
    bad = [r for r in report.results if r.name == "must run ghost"][0]
    assert not bad.passed
    assert "ghost-tool" in bad.detail


def test_metric_only_gate_uses_available_tools(session, sample_project):
    """A gate requiring the builtin analyzer should not depend on ruff."""
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="builtin ok", metric="required_tools",
        params={"tools": ["builtin"]}, blocking=True))
    report = gate_service.evaluate(session, project.id)
    # the builtin analyzer always runs, so this gate passes regardless of host
    builtin = [r for r in report.results if r.name == "builtin ok"][0]
    assert builtin.passed


def test_no_new_findings_gate_on_first_scan_passes(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="no new", metric="no_new_findings",
        operator="<=", threshold=0, severity=Severity.CRITICAL, blocking=True))
    report = gate_service.evaluate(session, project.id)
    gate = [r for r in report.results if r.name == "no new"][0]
    assert gate.passed
    assert "first scan" in gate.detail


def test_gate_scoping_global_and_project(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=None, name="global gate", metric="max_critical"))
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="project gate", metric="max_critical"))
    names = {g.name for g in gate_service.list_gates(session, project.id)}
    assert {"global gate", "project gate"} <= names
    # a different project sees only the global gate
    only_global = {g.name for g in gate_service.list_gates(session, 99999)}
    assert only_global == {"global gate"}


def test_disabled_gate_is_not_evaluated(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    row = gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="disabled", metric="max_total_findings",
        operator="==", threshold=0))
    row.enabled = False
    session.flush()
    report = gate_service.evaluate(session, project.id)
    assert all(r.name != "disabled" for r in report.results)


def test_update_and_delete_gate(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    row = gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="editme", metric="max_critical"))
    gate_service.update_gate(session, row.id, name="edited", threshold=5)
    assert session.get(gate_service.QualityGateRow, row.id).name == "edited"
    gate_service.delete_gate(session, row.id)
    assert session.get(gate_service.QualityGateRow, row.id) is None


def test_summary_line_mentions_failure(session, sample_project):
    project, scan = _scan(session, sample_project)
    for f in session.query(FindingRow).filter_by(scan_id=scan.id).all():
        f.severity = "critical"
    session.flush()
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="zero crit", metric="max_critical",
        operator="<=", threshold=0, blocking=True))
    report = gate_service.evaluate(session, project.id)
    assert "FAILED" in gate_service.summary_line(report)


def test_unknown_metric_is_reported_not_crashed(session, sample_project):
    project, _scan_row = _scan(session, sample_project)
    gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="weird", metric="does_not_exist"))
    report = gate_service.evaluate(session, project.id)
    weird = [r for r in report.results if r.name == "weird"][0]
    assert not weird.passed
    assert "unsupported metric" in weird.detail


def test_audit_records_gate_lifecycle(session, sample_project):
    from pyfix360.db.models import AuditEventRow

    project, _scan_row = _scan(session, sample_project)
    row = gate_service.create_gate(session, QualityGate(
        project_id=project.id, name="audited", metric="max_critical"))
    gate_service.update_gate(session, row.id, threshold=1)
    gate_service.delete_gate(session, row.id)
    actions = {e.action for e in session.query(AuditEventRow).all()}
    assert {"gate.create", "gate.update", "gate.delete"} <= actions
