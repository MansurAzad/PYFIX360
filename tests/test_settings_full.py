"""Tests for the 360° settings store and the access-control policy.

Two things are being protected here:

1. **Round-tripping.** Every key in ``AppSettings`` must survive save -> reload
   unchanged. A field that silently fails to persist is the classic settings
   bug, so the main test drives the whole object through the store and compares
   it field by field.
2. **Enforcement.** Access levels must be enforced by the service, not just
   displayed. ``check_access`` has to raise for BLOCKED and for unconfirmed
   ASK, and must have no effect on FULL.
"""

import json

import pytest

from pyfix360.models import (
    AccessLevel,
    AIBehavior,
    AppSettings,
    Density,
    PrivacyMode,
    Severity,
    ThemeKey,
)
from pyfix360.services import settings_service
from pyfix360.services.settings_service import AccessDenied

# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def test_every_default_key_has_a_matching_model_field():
    """DEFAULTS and AppSettings must stay in lockstep."""
    fields = set(AppSettings.model_fields)
    # data_dir and ai_default_provider_id are handled specially
    special = {"data_dir"}
    missing = set(settings_service.DEFAULTS) - fields - special
    assert not missing, f"DEFAULTS keys with no AppSettings field: {missing}"


def test_fresh_db_returns_documented_defaults(session):
    s = settings_service.get_settings(session)
    assert s.theme is ThemeKey.DARK
    assert s.density is Density.COMFORTABLE
    assert s.ai_behavior is AIBehavior.SUGGEST
    assert s.privacy_mode is PrivacyMode.LOCAL_ONLY
    assert s.redact_before_ai is True
    assert s.ai_allow_repo_content is False
    assert s.isolate_execution is True
    assert s.dry_run_by_default is True
    assert s.access_apply_to_original is AccessLevel.ASK
    assert s.access_danger_zone is AccessLevel.BLOCKED
    assert s.access_purge_history is AccessLevel.BLOCKED
    assert s.ai_default_provider_id is None


def test_safety_defaults_are_the_cautious_ones(session):
    """A fresh install must not be permissive."""
    s = settings_service.get_settings(session)
    assert s.privacy_mode is PrivacyMode.LOCAL_ONLY
    assert s.allow_network_in_sandbox is False
    assert s.require_snapshot_before_write is True
    assert s.require_approval_destructive is True
    assert s.require_approval_security is True
    assert s.block_write_outside_project is True
    assert s.access_remote_ai is not AccessLevel.FULL
    assert s.access_danger_zone is AccessLevel.BLOCKED


# ---------------------------------------------------------------------------
# round-tripping
# ---------------------------------------------------------------------------


def test_settings_round_trip_every_field(session):
    """Change one value per field of each type, save, reload, compare."""
    s = settings_service.get_settings(session)

    s.theme = ThemeKey.FOREST
    s.density = Density.COMPACT
    s.font_size = 16
    s.font_family = "Cascadia Mono"
    s.accent_override = "#ff8800"
    s.corner_radius = 10
    s.motion_enabled = False
    s.sidebar_width = 260
    s.start_screen = "Quality Gates"

    s.scan_include_globs = ["**/*.py", "**/*.js"]
    s.scan_exclude_globs = ["**/vendor/**"]
    s.scan_incremental = False
    s.scan_max_files = 1234
    s.enabled_analyzers = ["builtin", "ruff"]

    s.min_severity = Severity.MEDIUM
    s.group_by_root_cause = False
    s.auto_acknowledge_info = True

    s.ai_behavior = AIBehavior.EXPLAIN_ONLY
    s.ai_max_prompt_chars = 8000
    s.ai_allow_repo_content = True

    s.privacy_mode = PrivacyMode.REDACTED_REMOTE

    s.execution_timeout_s = 99
    s.memory_limit_mb = 512
    s.allow_network_in_sandbox = True
    s.max_repair_retries = 5
    s.backup_retention_days = 90

    s.access_apply_to_original = AccessLevel.FULL
    s.access_purge_history = AccessLevel.ASK

    s.test_command_override = "pytest -q"
    s.custom_tool_paths = {"ruff": r"C:\tools\ruff.exe"}

    s.notify_on_scan_complete = False
    s.log_level = "DEBUG"
    s.log_retention_days = 3

    settings_service.save_settings(session, s)
    r = settings_service.get_settings(session)

    for field in AppSettings.model_fields:
        if field == "data_dir":
            continue
        assert getattr(r, field) == getattr(s, field), f"field {field} did not persist"


def test_list_settings_persist_as_json(session):
    s = settings_service.get_settings(session)
    s.scan_include_globs = ["a/**", "b/**"]
    settings_service.save_settings(session, s)
    raw = settings_service.raw_values(session)
    assert json.loads(raw["scan_include_globs"]) == ["a/**", "b/**"]


def test_dict_settings_persist_as_json(session):
    s = settings_service.get_settings(session)
    s.custom_tool_paths = {"bandit": "/usr/local/bin/bandit"}
    settings_service.save_settings(session, s)
    raw = settings_service.raw_values(session)
    assert json.loads(raw["custom_tool_paths"]) == {"bandit": "/usr/local/bin/bandit"}


def test_partial_save_only_touches_named_keys(session):
    s = settings_service.get_settings(session)
    s.theme = ThemeKey.LIGHT
    s.font_size = 15
    settings_service.save_settings(session, s)

    # now change two things but save only one
    s.theme = ThemeKey.MIDNIGHT
    s.font_size = 19
    settings_service.save_settings(session, s, keys=["theme"])

    r = settings_service.get_settings(session)
    assert r.theme is ThemeKey.MIDNIGHT
    assert r.font_size == 15, "font_size should not have been written"


# ---------------------------------------------------------------------------
# tolerant reading
# ---------------------------------------------------------------------------


def test_corrupt_int_falls_back_to_default(session):
    from pyfix360.db.models import SettingRow

    session.add(SettingRow(key="font_size", value="not-a-number"))
    session.flush()
    assert settings_service.get_settings(session).font_size == 13


def test_corrupt_json_list_falls_back_to_empty(session):
    from pyfix360.db.models import SettingRow

    session.add(SettingRow(key="scan_include_globs", value="{broken"))
    session.flush()
    assert settings_service.get_settings(session).scan_include_globs == []


def test_unknown_enum_falls_back_to_default(session):
    from pyfix360.db.models import SettingRow

    session.add(SettingRow(key="theme", value="neon-vaporwave"))
    session.flush()
    assert settings_service.get_settings(session).theme is ThemeKey.DARK


def test_unknown_stored_key_is_ignored(session):
    from pyfix360.db.models import SettingRow

    session.add(SettingRow(key="not_a_real_setting", value="123"))
    session.flush()
    # reading must not blow up, and must not expose the bogus key
    values = settings_service.raw_values(session)
    assert "not_a_real_setting" not in values


# ---------------------------------------------------------------------------
# reset / export / import
# ---------------------------------------------------------------------------


def test_reset_restores_defaults(session):
    s = settings_service.get_settings(session)
    s.theme = ThemeKey.HIGH_CONTRAST
    s.font_size = 20
    settings_service.save_settings(session, s)

    settings_service.reset_settings(session)
    r = settings_service.get_settings(session)
    assert r.theme is ThemeKey.DARK
    assert r.font_size == 13


def test_export_import_round_trip(session):
    s = settings_service.get_settings(session)
    s.theme = ThemeKey.FOREST
    s.font_family = "Fira Code"
    settings_service.save_settings(session, s)

    payload = settings_service.export_settings(session)
    settings_service.reset_settings(session)
    assert settings_service.get_settings(session).theme is ThemeKey.DARK

    settings_service.import_settings(session, payload)
    r = settings_service.get_settings(session)
    assert r.theme is ThemeKey.FOREST
    assert r.font_family == "Fira Code"


def test_export_never_contains_credentials(session):
    """Settings export is a plain-text file; it must never leak secrets."""
    payload = settings_service.export_settings(session).lower()
    for forbidden in ("api_key", "apikey", "password", "secret", "token"):
        assert forbidden not in payload


def test_import_ignores_unknown_keys(session):
    payload = json.dumps({"theme": "light", "evil_key": "boom"})
    settings_service.import_settings(session, payload)
    assert settings_service.get_settings(session).theme is ThemeKey.LIGHT
    assert "evil_key" not in settings_service.raw_values(session)


def test_import_rejects_non_object(session):
    with pytest.raises(ValueError):
        settings_service.import_settings(session, "[1, 2, 3]")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_save_records_an_audit_event(session):
    from pyfix360.db.models import AuditEventRow

    s = settings_service.get_settings(session)
    s.font_size = 17
    settings_service.save_settings(session, s)
    actions = [e.action for e in session.query(AuditEventRow).all()]
    assert "settings.save" in actions


def test_reset_records_an_audit_event(session):
    from pyfix360.db.models import AuditEventRow

    settings_service.reset_settings(session)
    actions = [e.action for e in session.query(AuditEventRow).all()]
    assert "settings.reset" in actions


# ---------------------------------------------------------------------------
# access control — enforcement, not decoration
# ---------------------------------------------------------------------------


def test_check_access_full_allows_silently(session):
    s = settings_service.get_settings(session)
    s.access_rollback = AccessLevel.FULL
    settings_service.save_settings(session, s)
    # must not raise
    settings_service.check_access(session, "rollback")
    settings_service.check_access(session, "rollback", confirmed=True)


def test_check_access_ask_requires_confirmation(session):
    s = settings_service.get_settings(session)
    s.access_apply_to_original = AccessLevel.ASK
    settings_service.save_settings(session, s)
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "apply_to_original")
    # with confirmation it goes through
    settings_service.check_access(session, "apply_to_original", confirmed=True)


def test_check_access_blocked_refuses_even_when_confirmed(session):
    s = settings_service.get_settings(session)
    s.access_purge_history = AccessLevel.BLOCKED
    settings_service.save_settings(session, s)
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "purge_history")
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "purge_history", confirmed=True)


def test_blocked_message_names_the_capability(session):
    with pytest.raises(AccessDenied) as exc:
        settings_service.check_access(session, "danger_zone")
    assert "danger_zone" in str(exc.value)


def test_unknown_capability_defaults_to_ask(session):
    """An unknown capability must not silently become permissive."""
    assert settings_service.access_level(session, "made_up") is AccessLevel.ASK
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "made_up")


def test_access_level_round_trips_through_store(session):
    s = settings_service.get_settings(session)
    s.access_export_reports = AccessLevel.BLOCKED
    settings_service.save_settings(session, s)
    assert settings_service.access_level(
        session, "export_reports") is AccessLevel.BLOCKED


# ---------------------------------------------------------------------------
# config mirroring
# ---------------------------------------------------------------------------


def test_apply_to_config_mirrors_appearance(session):
    from pyfix360.config import get_config

    s = settings_service.get_settings(session)
    s.theme = ThemeKey.MIDNIGHT
    s.font_size = 18
    s.density = Density.SPACIOUS
    s.start_screen = "AI Assistant"
    settings_service.save_settings(session, s)

    cfg = get_config()
    assert cfg.theme == "midnight"
    assert cfg.font_size == 18
    assert cfg.density == "spacious"
    assert cfg.start_screen == "AI Assistant"


def test_apply_to_config_mirrors_safety(session):
    from pyfix360.config import get_config

    s = settings_service.get_settings(session)
    s.execution_timeout_s = 77
    s.memory_limit_mb = 999
    s.allow_network_in_sandbox = True
    settings_service.save_settings(session, s)

    cfg = get_config()
    assert cfg.execution_timeout_s == 77
    assert cfg.memory_limit_mb == 999
    assert cfg.allow_network_in_sandbox is True


def test_apply_from_config_does_not_raise_on_fresh_db(session):
    settings_service.apply_from_config(session)


# ---------------------------------------------------------------------------
# the AI honesty switches
# ---------------------------------------------------------------------------


def test_remote_repo_content_is_off_by_default(session):
    s = settings_service.get_settings(session)
    assert s.ai_allow_repo_content is False
    assert s.privacy_mode is PrivacyMode.LOCAL_ONLY


def test_redaction_is_on_by_default(session):
    assert settings_service.get_settings(session).redact_before_ai is True


def test_prompt_cap_has_a_sane_default(session):
    cap = settings_service.get_settings(session).ai_max_prompt_chars
    assert 1000 <= cap <= 200000
