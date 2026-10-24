"""Tests for P2: the settings that promised behaviour they did not have.

The Settings Center is the only way a user reaches most of the app, so a
switch that does nothing is worse than no switch at all — it teaches the user
a rule that is not true. This file pins down the corrected behaviour:

* the four settings that promised an ability the app does not have are gone
* the redaction switch is locked on, because the gateway redacts anyway
* every remaining setting is actually read by the code that claims to honour it
"""

from __future__ import annotations

import json

import pytest

from pyfix360.models import AppSettings, Severity
from pyfix360.services import settings_service

# ---------------------------------------------------------------------------
# the removed settings
# ---------------------------------------------------------------------------

REMOVED = (
    "telemetry_enabled",
    "reduce_transparency",
    "lint_command_override",
    "format_command_override",
)


@pytest.mark.parametrize("key", REMOVED)
def test_removed_setting_is_not_in_the_defaults(key):
    assert key not in settings_service.DEFAULTS


@pytest.mark.parametrize("key", REMOVED)
def test_removed_setting_is_not_on_the_model(key):
    assert key not in AppSettings.model_fields


def test_the_defaults_and_the_model_still_agree():
    fields = set(AppSettings.model_fields)
    missing = set(settings_service.DEFAULTS) - fields - {"data_dir"}
    assert not missing, f"DEFAULTS keys with no AppSettings field: {missing}"


# ---------------------------------------------------------------------------
# the locked redaction switch
# ---------------------------------------------------------------------------


def test_redaction_is_declared_locked():
    assert "redact_before_ai" in settings_service.LOCKED_KEYS
    assert settings_service.LOCKED_KEYS["redact_before_ai"] == "true"


def test_coercing_redaction_off_is_refused():
    with pytest.raises(ValueError) as exc:
        settings_service.coerce("redact_before_ai", "false")
    assert "redact_before_ai" in str(exc.value)
    assert "locked" in str(exc.value)


def test_saving_redaction_off_is_ignored_and_the_value_stays_true(session):
    st = settings_service.get_settings(session)
    st.redact_before_ai = False
    settings_service.save_settings(session, st)
    reloaded = settings_service.get_settings(session)
    assert reloaded.redact_before_ai is True


def test_importing_a_file_that_turns_redaction_off_still_keeps_it_on(session):
    payload = json.dumps({"redact_before_ai": "false"})
    settings_service.import_settings(session, payload)
    assert settings_service.get_settings(session).redact_before_ai is True


def test_a_stored_false_cannot_be_read_back(session):
    """Even a row that somehow says false is reported as true."""
    from pyfix360.db.models import SettingRow

    session.add(SettingRow(key="redact_before_ai", value="false"))
    session.flush()
    assert settings_service.get_settings(session).redact_before_ai is True


def test_coercing_redaction_on_is_allowed():
    assert settings_service.coerce("redact_before_ai", "true") is True


# ---------------------------------------------------------------------------
# every remaining setting is actually read
# ---------------------------------------------------------------------------


def test_every_setting_is_read_by_the_code_that_claims_to_honour_it():
    """A setting that is declared but never read is a promise nobody keeps.

    The check is deliberately blunt: the name must appear somewhere in the
    source outside the declaration and the settings store itself. A stricter
    check would need to understand every call site; this one still catches a
    setting that was never wired to anything.
    """
    import pathlib

    import pyfix360

    root = pathlib.Path(pyfix360.__file__).resolve().parent
    # The declaration files are excluded: they *name* every setting, so
    # including them would make the check pass for any setting at all.
    declaration_files = {"settings_service.py", "models.py", "config.py"}
    sources = [p for p in root.rglob("*.py") if p.name not in declaration_files]
    blob = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    unread = [f for f in AppSettings.model_fields if f not in blob]
    assert not unread, f"settings nothing reads: {unread}"


# ---------------------------------------------------------------------------
# the settings the Findings tab owns
# ---------------------------------------------------------------------------


def test_min_severity_default_is_the_lowest_floor(session):
    assert settings_service.get_settings(session).min_severity is Severity.INFO


def test_severity_floor_is_read_from_the_settings(session):
    from pyfix360.services import findings_service

    st = settings_service.get_settings(session)
    st.min_severity = Severity.HIGH
    settings_service.save_settings(session, st)
    assert findings_service.severity_floor(session) is Severity.HIGH
