"""Tests for settings persistence."""

from pyfix360.models import AppSettings, PrivacyMode
from pyfix360.services import settings_service


def test_defaults_on_fresh_db(session, data_dir):
    s = settings_service.get_settings(session)
    assert s.isolate_execution is True
    assert s.execution_timeout_s == 300
    assert s.privacy_mode == PrivacyMode.LOCAL_ONLY
    assert s.data_dir == str(data_dir)


def test_save_and_reload(session):
    s = settings_service.get_settings(session)
    s.execution_timeout_s = 42
    s.isolate_execution = False
    s.privacy_mode = PrivacyMode.REDACTED_REMOTE
    settings_service.save_settings(session, s)

    reloaded = settings_service.get_settings(session)
    assert reloaded.execution_timeout_s == 42
    assert reloaded.isolate_execution is False
    assert reloaded.privacy_mode == PrivacyMode.REDACTED_REMOTE


def test_settings_create_audit_event(session):
    from pyfix360.db.models import AuditEventRow

    s = settings_service.get_settings(session)
    settings_service.save_settings(session, s)
    events = session.query(AuditEventRow).filter_by(action="settings.save").all()
    assert len(events) == 1
    assert events[0].risk == "warn"


def test_settings_applied_to_runtime_config(session, data_dir):
    from pyfix360.config import get_config

    s = settings_service.get_settings(session)
    s.execution_timeout_s = 77
    settings_service.save_settings(session, s)
    assert get_config().execution_timeout_s == 77


def test_appsettings_model_defaults():
    s = AppSettings()
    assert s.privacy_mode == PrivacyMode.LOCAL_ONLY
    assert s.redact_before_ai is True
