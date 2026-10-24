"""Tests for the P1 fixes: the gates crash, settings coercion, schema upgrade,
browser redaction and the discovery policy.

Each of these was a way for the app to fail in a way that looked like a
connectivity problem, a data problem, or a broken install.
"""

from __future__ import annotations

import json

import pytest

from pyfix360.ai import discovery
from pyfix360.cli import main
from pyfix360.db import init_db, session_scope
from pyfix360.db.models import AIProviderRow
from pyfix360.models import AccessLevel, PrivacyMode, ProviderType
from pyfix360.services import platform_service, settings_service

# ---------------------------------------------------------------------------
# P1-1: `pyfix360 gates` with no sub-command must not crash
# ---------------------------------------------------------------------------


def test_bare_gates_lists_without_a_project_id(capsys):
    """The bare form must print a table, not raise for a missing argument."""
    assert main(["gates"]) == 0
    capsys.readouterr()


def test_bare_gates_with_a_project_id_also_works(capsys):
    assert main(["gates", "list", "1"]) == 0
    capsys.readouterr()


def test_gates_list_and_eval_are_both_reachable(capsys, data_dir, sample_project):
    assert main(["import", str(sample_project)]) == 0
    capsys.readouterr()
    assert main(["scan", "1"]) == 0
    capsys.readouterr()
    assert main(["gates", "eval", "1"]) == 0
    out = capsys.readouterr().out
    assert "No gates configured" in out


# ---------------------------------------------------------------------------
# P1-2: `settings set` must parse a list or a dict, not store the string
# ---------------------------------------------------------------------------


def test_settings_set_stores_a_json_list_as_a_list(capsys, data_dir):
    assert main(["settings", "set", "scan_include_globs", '["**/*.py","**/*.js"]']) == 0
    capsys.readouterr()
    with session_scope() as s:
        st = settings_service.get_settings(s)
    assert st.scan_include_globs == ["**/*.py", "**/*.js"]


def test_settings_set_stores_a_json_dict_as_a_dict(capsys, data_dir):
    payload = json.dumps({"ruff": r"C:\tools\ruff.exe"})
    assert main(["settings", "set", "custom_tool_paths", payload]) == 0
    capsys.readouterr()
    with session_scope() as s:
        st = settings_service.get_settings(s)
    assert st.custom_tool_paths == {"ruff": r"C:\tools\ruff.exe"}


def test_settings_set_refuses_a_bare_string_for_a_list(capsys, data_dir):
    assert main(["settings", "set", "scan_include_globs", "*.py"]) == 1
    err = capsys.readouterr().err
    assert "JSON list" in err


def test_settings_set_refuses_a_list_for_a_dict(capsys, data_dir):
    assert main(["settings", "set", "custom_tool_paths", "[1, 2]"]) == 1
    err = capsys.readouterr().err
    assert "JSON" in err


def test_settings_set_refuses_an_unknown_key(capsys, data_dir):
    assert main(["settings", "set", "no_such_setting", "1"]) == 1
    assert "unknown setting" in capsys.readouterr().err


def test_settings_get_returns_json_for_a_list(capsys, data_dir):
    main(["settings", "set", "scan_include_globs", '["a/**"]'])
    capsys.readouterr()
    assert main(["settings", "get", "scan_include_globs"]) == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == ["a/**"]


# ---------------------------------------------------------------------------
# P1-3: `pyfix360 init` and Alembic must agree
# ---------------------------------------------------------------------------


def test_init_stamps_the_alembic_revision_at_head(data_dir):
    from sqlalchemy import inspect, text

    from pyfix360.db.session import get_engine

    engine = get_engine()
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        pytest.skip("alembic is not installed in this environment")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).all()
    assert rows, "init_db() must stamp the revision table"


def test_init_is_idempotent_and_keeps_the_data(data_dir):
    from pyfix360.db.models import SettingRow

    with session_scope() as s:
        s.add(SettingRow(key="probe", value="kept"))
        s.flush()
    init_db()
    init_db()
    with session_scope() as s:
        assert s.query(SettingRow).filter_by(key="probe").one().value == "kept"


def test_init_adds_a_missing_column_without_dropping_anything(data_dir):
    from sqlalchemy import text

    from pyfix360.db.session import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE settings DROP COLUMN value"))
    init_db()
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(settings)"))}
    assert "value" in columns


# ---------------------------------------------------------------------------
# P1-4: the browser path is redacted and gated like the API path
# ---------------------------------------------------------------------------


SECRET = "sk-live-0123456789abcdefghijklmnop"


def _full_remote(session) -> None:
    settings = settings_service.get_settings(session)
    settings.privacy_mode = PrivacyMode.FULL_REMOTE_APPROVED
    settings_service.save_settings(session, settings)


def test_browser_payload_redacts_the_prompt(session):
    _full_remote(session)
    payload = platform_service.browser_payload(session, "trickle",
                                               f"my key is {SECRET}", confirmed=True)
    assert SECRET not in payload
    assert "[REDACTED" in payload


def test_browser_payload_refuses_in_local_only_mode(session):
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.browser_payload(session, "trickle", "hello", confirmed=True)


def test_browser_payload_refuses_when_the_browser_capability_is_blocked(session):
    _full_remote(session)
    settings = settings_service.get_settings(session)
    settings.access_plugin_tools = AccessLevel.BLOCKED
    settings_service.save_settings(session, settings)
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.browser_payload(session, "trickle", "hello", confirmed=True)


def test_browser_payload_refuses_an_api_only_platform(session):
    _full_remote(session)
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.browser_payload(session, "v0", "hello", confirmed=True)


def test_browser_payload_is_audited(session):
    from pyfix360.db.models import AuditEventRow

    _full_remote(session)
    platform_service.browser_payload(session, "trickle", "hello", confirmed=True)
    events = session.query(AuditEventRow).filter_by(
        action="platform.prompt.browser").all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# P1-5: model discovery obeys the same policy as sending a prompt
# ---------------------------------------------------------------------------


def _remote_provider(session, name: str = "p") -> AIProviderRow:
    row = AIProviderRow(
        name=name, provider_type=ProviderType.CUSTOM_OPENAI_COMPAT.value,
        base_url="https://api.example.com/v1", model="m",
        privacy_mode=PrivacyMode.FULL_REMOTE_APPROVED.value, enabled=True,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def no_network(monkeypatch):
    """Fail loudly if a request is made; the policy must stop it first."""
    def boom(*args, **kwargs):
        raise AssertionError("a request left the machine")

    monkeypatch.setattr(discovery.urllib.request, "urlopen", boom)


def test_discovery_is_refused_in_local_only_mode(session, no_network):
    provider = _remote_provider(session)
    with pytest.raises(discovery.DiscoveryError) as exc:
        discovery.discover(session, provider.id)
    assert "privacy" in str(exc.value).lower()


def test_discovery_is_refused_when_remote_ai_is_blocked(session, no_network):
    provider = _remote_provider(session)
    settings = settings_service.get_settings(session)
    settings.privacy_mode = PrivacyMode.FULL_REMOTE_APPROVED
    settings.access_remote_ai = AccessLevel.BLOCKED
    settings_service.save_settings(session, settings)
    with pytest.raises(discovery.DiscoveryError):
        discovery.discover(session, provider.id)


def test_discovery_is_refused_when_remote_ai_is_ask_and_unconfirmed(
        session, no_network):
    provider = _remote_provider(session)
    settings = settings_service.get_settings(session)
    settings.privacy_mode = PrivacyMode.FULL_REMOTE_APPROVED
    settings.access_remote_ai = AccessLevel.ASK
    settings_service.save_settings(session, settings)
    with pytest.raises(discovery.DiscoveryError):
        discovery.discover(session, provider.id)


def test_discovery_proceeds_when_every_gate_is_open(session, monkeypatch):
    class FakeResponse:
        def read(self):
            return json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    provider = _remote_provider(session)
    settings = settings_service.get_settings(session)
    settings.privacy_mode = PrivacyMode.FULL_REMOTE_APPROVED
    settings.access_remote_ai = AccessLevel.FULL
    settings_service.save_settings(session, settings)
    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse())
    result = discovery.discover(session, provider.id)
    assert result.ok
    assert [m.id for m in result.models] == ["m1", "m2"]
