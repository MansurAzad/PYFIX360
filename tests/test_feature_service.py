"""Tests for feature completeness analysis."""

from pyfix360.models import ProjectOrigin
from pyfix360.services import feature_service, project_service, scan_service


def test_feature_matrix_from_symbols(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])

    feature_service.analyze_completeness(session, project.id)
    matrix = feature_service.completeness_matrix(session, project.id)
    names = {m["name"]: m for m in matrix}

    assert "digest" in names
    assert "run" in names
    # digest has a test referencing it => complete
    assert names["digest"]["status"] in ("complete", "partial")


def test_doc_only_feature_marked_missing(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    feature_service.analyze_completeness(session, project.id)
    matrix = feature_service.completeness_matrix(session, project.id)
    missing = [m for m in matrix if m["status"] == "missing"]
    # README documents '## load' but no load() function exists in the sample
    assert any(m["name"] == "load" for m in missing)


def test_acceptance_criteria_flag_missing(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    feature_service.analyze_completeness(
        session, project.id, ["batch uploads must be supported"])
    matrix = feature_service.completeness_matrix(session, project.id)
    crit_rows = [m for m in matrix if "batch uploads" in m["name"]]
    assert crit_rows and crit_rows[0]["status"] == "missing"
    assert any(c["kind"] == "criteria" and not c["passed"]
               for c in crit_rows[0]["checks"])


def test_reanalysis_is_idempotent(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    scan = scan_service.create_scan(session, project.id)
    scan_service.run_scan(session, scan.id, adapters=[])
    feature_service.analyze_completeness(session, project.id)
    n1 = len(feature_service.completeness_matrix(session, project.id))
    feature_service.analyze_completeness(session, project.id)
    n2 = len(feature_service.completeness_matrix(session, project.id))
    assert n1 == n2
