"""The access policy is enforced at the service boundary, not only in the UI.

These tests pin the behaviour the security model depends on: a capability set
to BLOCKED is refused even when the caller claims to have confirmation, and a
capability set to ASK is refused until the caller passes ``confirmed=True``.
The caller that passes that flag is the UI or the CLI, which obtains the
human's agreement first.
"""

from __future__ import annotations

import pytest

from pyfix360.db.models import AuditEventRow, ProjectRow
from pyfix360.models import AccessLevel, ProjectOrigin, QualityGate, Severity
from pyfix360.services import (
    audit,
    gate_service,
    project_service,
    repair_service,
    report_service,
    settings_service,
    test_runner,
)


def _set(session, capability: str, level: AccessLevel) -> None:
    settings = settings_service.get_settings(session)
    setattr(settings, f"access_{capability}", level)
    settings_service.save_settings(session, settings)


# --------------------------------------------------------------------------------------
# apply_to_original
# --------------------------------------------------------------------------------------


def test_blocked_apply_to_original_is_refused_even_with_confirmation(
        session, sample_project):
    """The policy is the source of truth — ``confirmed`` cannot override it."""
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    _set(session, "apply_to_original", AccessLevel.BLOCKED)
    before = (sample_project / "app.py").read_text(encoding="utf-8")

    with pytest.raises(settings_service.AccessDenied):
        repair_service.execute_repair(session, 1, apply_to_original=True,
                                      confirmed=True)

    assert (sample_project / "app.py").read_text(encoding="utf-8") == before
    assert session.get(ProjectRow, project.id) is not None


def test_ask_apply_to_original_needs_confirmation(session, sample_project):
    project_service.import_project(session, str(sample_project),
                                   origin=ProjectOrigin.FOLDER)
    _set(session, "apply_to_original", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        repair_service.execute_repair(session, 1, apply_to_original=True,
                                      confirmed=False)


# --------------------------------------------------------------------------------------
# the other capabilities
# --------------------------------------------------------------------------------------


def test_purge_history_is_blocked_by_default(session):
    audit.record(session, "test.event", actor="user")
    with pytest.raises(settings_service.AccessDenied):
        audit.purge_history(session)
    assert session.query(AuditEventRow).count() >= 1


def test_purge_history_runs_once_confirmed(session):
    audit.record(session, "test.event", actor="user")
    _set(session, "purge_history", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        audit.purge_history(session, confirmed=False)
    count = audit.purge_history(session, confirmed=True)
    assert count >= 1
    # the purge itself is recorded, so the trail is not left silently broken
    assert audit.purge_history(session, confirmed=True) == 1


def test_delete_project_is_gated(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    _set(session, "delete_project", AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        project_service.delete_project(session, project.id, confirmed=True)
    assert session.get(ProjectRow, project.id) is not None

    _set(session, "delete_project", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        project_service.delete_project(session, project.id, confirmed=False)
    assert project_service.delete_project(session, project.id, confirmed=True)
    assert session.get(ProjectRow, project.id) is None


def test_gate_crud_is_gated(session):
    gate = QualityGate(project_id=None, name="g", metric="finding_count",
                       operator="<=", threshold=0, severity=Severity.CRITICAL)
    _set(session, "edit_quality_gates", AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        gate_service.create_gate(session, gate, confirmed=True)

    _set(session, "edit_quality_gates", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        gate_service.create_gate(session, gate, confirmed=False)
    row = gate_service.create_gate(session, gate, confirmed=True)
    with pytest.raises(settings_service.AccessDenied):
        gate_service.update_gate(session, row.id, name="x", confirmed=False)
    with pytest.raises(settings_service.AccessDenied):
        gate_service.delete_gate(session, row.id, confirmed=False)
    gate_service.delete_gate(session, row.id, confirmed=True)


def test_export_reports_is_gated(session):
    _set(session, "export_reports", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        report_service.generate_report(session, 1, confirmed=False)
    _set(session, "export_reports", AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        report_service.generate_report(session, 1, confirmed=True)


def test_run_tests_is_gated(session, tmp_path):
    _set(session, "run_tests", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        test_runner.run_tests(session, 1, tmp_path, confirmed=False)
    _set(session, "run_tests", AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        test_runner.run_tests(session, 1, tmp_path, confirmed=True)


def test_rollback_is_gated(session):
    _set(session, "rollback", AccessLevel.ASK)
    with pytest.raises(settings_service.AccessDenied):
        repair_service.rollback_patch(session, 1, confirmed=False)
    _set(session, "rollback", AccessLevel.BLOCKED)
    with pytest.raises(settings_service.AccessDenied):
        repair_service.rollback_patch(session, 1, confirmed=True)


# --------------------------------------------------------------------------------------
# the policy table itself
# --------------------------------------------------------------------------------------


def test_unknown_capability_defaults_to_ask(session):
    assert settings_service.access_level(session, "no_such_capability") \
        is AccessLevel.ASK


def test_every_declared_capability_is_checkable(session):
    for key in settings_service.DEFAULTS:
        if key.startswith("access_"):
            capability = key[len("access_"):]
            assert settings_service.access_level(session, capability) \
                in set(AccessLevel)
