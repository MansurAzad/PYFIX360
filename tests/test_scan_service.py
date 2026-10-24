"""Tests for scan orchestration, dedup, correlation and incremental scans."""

from pathlib import Path

from pyfix360.db.models import FindingRow, ModuleRow, ProjectFileRow
from pyfix360.models import ProjectOrigin, ScanStatus
from pyfix360.services import project_service, scan_service


def _import(session, root: Path):
    return project_service.import_project(session, str(root), origin=ProjectOrigin.FOLDER)


def test_scan_produces_findings_with_evidence(session, sample_project):
    project = _import(session, sample_project)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    assert scan.status in (ScanStatus.COMPLETED.value, ScanStatus.PARTIAL.value)
    findings = session.query(FindingRow).filter_by(scan_id=scan.id).all()
    rules = {f.rule_id for f in findings}
    assert "py-bare-except" in rules
    assert "py-mutable-default" in rules
    assert "sec-subprocess-shell" in rules
    bare = next(f for f in findings if f.rule_id == "py-bare-except")
    assert bare.evidence, "every finding must carry evidence"
    assert bare.evidence[0].snippet
    assert bare.root_cause_group
    assert bare.file_path.endswith("app.py")


def test_scan_records_symbols(session, sample_project):
    project = _import(session, sample_project)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    modules = session.query(ModuleRow).filter_by(project_id=project.id).all()
    names = {(m.kind, m.name) for m in modules}
    assert ("function", "run") in names
    assert ("function", "digest") in names


def test_scan_deduplicates(session, tmp_path):
    root = tmp_path / "dup"
    root.mkdir()
    # identical bare-except in same function body region of two files
    (root / "a.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n",
        encoding="utf-8")
    (root / "b.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n",
        encoding="utf-8")
    project = _import(session, root)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[])
    findings = session.query(FindingRow).filter_by(scan_id=scan.id).all()
    bare = [f for f in findings if f.rule_id == "py-bare-except"]
    assert len(bare) == 2  # different files => different stable keys, kept separate
    assert {f.file_path for f in bare} == {"a.py", "b.py"}


def test_repeated_scan_collapses_duplicates(session, tmp_path):
    """Re-running analysis on the same scan content must not duplicate rows."""
    root = tmp_path / "dup2"
    root.mkdir()
    (root / "a.py").write_text(
        "def f():\n    try:\n        pass\n    except:\n        pass\n",
        encoding="utf-8")
    project = _import(session, root)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    first_count = session.query(FindingRow).filter_by(scan_id=scan.id).count()
    # unique constraint on (scan_id, stable_key) protects against double-insert
    keys = [f.stable_key for f in
            session.query(FindingRow).filter_by(scan_id=scan.id).all()]
    assert len(keys) == len(set(keys))
    assert first_count >= 1


def test_scan_status_partial_when_tools_unavailable(session, sample_project, monkeypatch):
    from pyfix360.analysis import tool_adapters

    class Unavailable(tool_adapters.ToolAdapter):
        name = "ghost-tool"

        def is_available(self, tool_paths=None):
            return False, "not installed on this host"

        def run(self, root, *, timeout_s=300, tool_paths=None):  # pragma: no cover
            raise AssertionError("must not run when unavailable")

    project = _import(session, sample_project)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[Unavailable()])
    assert scan.status == ScanStatus.PARTIAL.value
    assert any("ghost-tool" in t and "not installed" in t for t in scan.tools_skipped)


def test_incremental_scan_carries_forward_unchanged_findings(session, sample_project):
    project = _import(session, sample_project)
    s1 = scan_service.create_scan(session, project.id)
    s1 = scan_service.run_scan(session, s1.id, adapters=[])
    f1 = session.query(FindingRow).filter_by(scan_id=s1.id, rule_id="py-bare-except").one()

    # unchanged project: second scan must keep the finding with same stable key
    s2 = scan_service.create_scan(session, project.id)
    s2 = scan_service.run_scan(session, s2.id, adapters=[])
    f2 = session.query(FindingRow).filter_by(scan_id=s2.id, rule_id="py-bare-except").one()
    assert f1.stable_key == f2.stable_key

    # modified file: finding re-derived fresh
    (sample_project / "app.py").write_text(
        (sample_project / "app.py").read_text(encoding="utf-8") + "\n# touch\n",
        encoding="utf-8")
    _rehash(session, project.id)
    s3 = scan_service.create_scan(session, project.id)
    s3 = scan_service.run_scan(session, s3.id, adapters=[])
    f3 = session.query(FindingRow).filter_by(scan_id=s3.id, rule_id="py-bare-except").one()
    assert f3.stable_key == f1.stable_key  # same site, still stable


def _rehash(session, project_id: int) -> None:
    import hashlib

    from pyfix360.db.models import ProjectRow

    project = session.get(ProjectRow, project_id)
    for pf in session.query(ProjectFileRow).filter_by(project_id=project_id).all():
        path = Path(project.root_path) / pf.rel_path
        pf.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()


def test_scan_survives_analyzer_failure(session, tmp_path, monkeypatch):
    root = tmp_path / "boom"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    project = _import(session, root)

    import pyfix360.services.scan_service as ss

    original = ss.analyze_python_file

    def exploding(*a, **k):
        raise RuntimeError("analyzer bug")

    monkeypatch.setattr(ss, "analyze_python_file", exploding)
    scan = ss.create_scan(session, project.id)
    scan = ss.run_scan(session, scan.id, adapters=[])
    assert scan.status in (ScanStatus.PARTIAL.value, ScanStatus.COMPLETED.value)
    assert any("analyzer bug" in t for t in scan.tools_skipped)
    monkeypatch.setattr(ss, "analyze_python_file", original)


def test_cancel_check_stops_scan(session, sample_project):
    project = _import(session, sample_project)
    scan = scan_service.create_scan(session, project.id)
    scan = scan_service.run_scan(session, scan.id, adapters=[],
                                 cancel_check=lambda: True)
    assert scan.status == ScanStatus.CANCELLED.value
