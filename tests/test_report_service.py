"""Tests for report generation."""

import json

from pyfix360.models import ProjectOrigin
from pyfix360.services import project_service, repair_service, report_service, scan_service


def _scanned_project(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    return project, scan


def test_markdown_report_sections(session, sample_project, tmp_path):
    project, scan = _scanned_project(session, sample_project)
    path = report_service.generate_report(session, project.id, fmt="markdown",
                                          out_path=tmp_path / "r.md")
    text = path.read_text(encoding="utf-8")
    assert "# PYFIX 360 Audit Report" in text
    for section in ("Project", "Scan Coverage", "Findings", "Repairs & Verification",
                    "Test Runs", "Audit Trail", "Limitations"):
        assert section in text
    assert "py-bare-except" in text


def test_json_report_is_valid_and_complete(session, sample_project, tmp_path):
    project, scan = _scanned_project(session, sample_project)
    # run one repair so the report includes it
    from pyfix360.db.models import FindingRow

    finding = session.query(FindingRow).filter_by(
        scan_id=scan.id, rule_id="py-bare-except").one()
    plan = repair_service.plan_repair(session, finding.id)
    repair_service.execute_repair(session, plan.id, timeout_s=120)

    path = report_service.generate_report(session, project.id, fmt="json",
                                          out_path=tmp_path / "r.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["scan"]["id"] == scan.id
    assert any(f["rule_id"] == "py-bare-except" for f in data["findings"])
    assert data["patches"], "repair history must be in the report"
    assert data["audit_events"]
    # evidence included
    bare = next(f for f in data["findings"] if f["rule_id"] == "py-bare-except")
    assert bare["evidence"] and bare["evidence"][0]["snippet"]


def test_html_report_escapes_content(session, sample_project, tmp_path):
    project, scan = _scanned_project(session, sample_project)
    path = report_service.generate_report(session, project.id, fmt="html",
                                          out_path=tmp_path / "r.html")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "<script>" not in text.lower()


def test_report_requires_scan(session, sample_project, tmp_path):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    try:
        report_service.generate_report(session, project.id, out_path=tmp_path / "x.md")
        raised = False
    except ValueError as exc:
        raised = True
        assert "no scans" in str(exc)
    assert raised


def test_tools_skipped_are_disclosed(session, sample_project, tmp_path):
    from pyfix360.analysis.tool_adapters import ToolAdapter

    class Missing(ToolAdapter):
        name = "mystery-linter"

        def is_available(self, tool_paths=None):
            return False, "not installed"

        def run(self, root, *, timeout_s=300, tool_paths=None):  # pragma: no cover
            raise AssertionError

    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[Missing()])
    path = report_service.generate_report(session, project.id, fmt="markdown",
                                          out_path=tmp_path / "r.md")
    text = path.read_text(encoding="utf-8")
    assert "mystery-linter" in text
    assert "NOT available" in text
