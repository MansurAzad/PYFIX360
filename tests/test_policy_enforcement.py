"""Tests for the P0 and P1 fixes: policy, host classification, stored prompts.

Three groups live here:

1. **Policy is enforced at the service boundary.** A service called directly from
   Python must refuse exactly as it would when called from the UI — otherwise the
   access matrix is decoration and the UI is the only thing enforcing it.
2. **The host is compared exactly.** ``localhost`` inside a URL is not the host
   ``localhost``, so ``http://localhost.evil.com/v1`` is remote.
3. **The database holds redacted text only.** The prompt that is stored is the
   prompt that was sent, and it has been through the redactor.
"""

from __future__ import annotations

import pytest

from pyfix360.ai import gateway
from pyfix360.ai.gateway import _is_local_url, provider_destination
from pyfix360.db.models import AIChatRow, AIProviderRow, AIRequestRow
from pyfix360.models import AccessLevel, PrivacyMode, ProviderType
from pyfix360.services import (
    ai_chat_service,
    audit,
    gate_service,
    project_service,
    report_service,
    settings_service,
    test_runner,
)
from pyfix360.services.settings_service import AccessDenied

# ---------------------------------------------------------------------------
# 1. policy at the service boundary
# ---------------------------------------------------------------------------


def _block(session, capability: str) -> None:
    settings = settings_service.get_settings(session)
    setattr(settings, f"access_{capability}", AccessLevel.BLOCKED)
    settings_service.save_settings(session, settings)


def test_unknown_capability_is_asked_not_allowed(session):
    """A capability nobody declared must not be a free pass."""
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "no_such_capability")
    settings_service.check_access(session, "no_such_capability", confirmed=True)


def test_blocked_capability_refuses_even_when_confirmed(session):
    _block(session, "export_reports")
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "export_reports", confirmed=True)


def test_ask_capability_refuses_without_confirmation(session):
    settings = settings_service.get_settings(session)
    settings.access_export_reports = AccessLevel.ASK
    settings_service.save_settings(session, settings)
    with pytest.raises(AccessDenied):
        settings_service.check_access(session, "export_reports")
    settings_service.check_access(session, "export_reports", confirmed=True)


def test_report_export_service_refuses_when_blocked(session, sample_project, tmp_path):
    """The service refuses on its own, with no UI in the loop."""
    project = project_service.import_project(session, str(sample_project), name="p")
    _block(session, "export_reports")
    with pytest.raises(AccessDenied):
        report_service.generate_report(session, project.id, fmt="markdown",
                                       out_path=tmp_path / "r.md")


def test_report_export_service_refuses_when_ask_and_unconfirmed(
        session, sample_project, tmp_path):
    project = project_service.import_project(session, str(sample_project), name="p")
    settings = settings_service.get_settings(session)
    settings.access_export_reports = AccessLevel.ASK
    settings_service.save_settings(session, settings)
    with pytest.raises(AccessDenied):
        report_service.generate_report(session, project.id, fmt="markdown",
                                       out_path=tmp_path / "r.md")


def test_gate_service_refuses_edits_when_blocked(session):
    from pyfix360.models import QualityGate

    _block(session, "edit_quality_gates")
    gate = QualityGate(name="g", metric="max_severity_count", operator="<=",
                       threshold=0, severity="critical", blocking=True)
    with pytest.raises(AccessDenied):
        gate_service.create_gate(session, gate)


def test_purge_history_service_refuses_when_blocked(session):
    _block(session, "purge_history")
    with pytest.raises(AccessDenied):
        audit.purge_history(session)


def test_delete_project_service_refuses_when_blocked(session, sample_project):
    project = project_service.import_project(session, str(sample_project), name="p")
    _block(session, "delete_project")
    with pytest.raises(AccessDenied):
        project_service.delete_project(session, project.id)


def test_run_tests_service_refuses_when_blocked(session, sample_project, tmp_path):
    project = project_service.import_project(session, str(sample_project), name="p")
    _block(session, "run_tests")
    with pytest.raises(AccessDenied):
        test_runner.run_tests(session, project.id, tmp_path)


# ---------------------------------------------------------------------------
# 2. the host is compared exactly, never as a substring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://LOCALHOST:1234/v1",
    "http://127.0.0.1:8000/v1",
    "http://127.0.0.1/v1",
    "http://[::1]:8000/v1",
])
def test_loopback_urls_are_local(url):
    assert _is_local_url(url) is True


@pytest.mark.parametrize("url", [
    "http://localhost.evil.com/v1",
    "https://notlocalhost.com/v1",
    "http://localhost.com/v1",
    "http://127.0.0.1.evil.com/v1",
    "http://127.0.0.1.evil.com:8000/v1",
    "http://my-localhost/v1",
    "http://[::1].evil.com/v1",
    "http://example.com/localhost/v1",
    "",
    "not a url at all",
])
def test_lookalike_urls_are_not_local(url):
    assert _is_local_url(url) is False


def test_local_lookalike_provider_is_treated_as_remote(session):
    """A provider typed 'local' but pointed at a lookalike host is remote."""
    provider = AIProviderRow(
        name="lookalike", provider_type=ProviderType.LOCAL_RUNTIME.value,
        base_url="http://localhost.evil.com/v1", model="m",
        privacy_mode=PrivacyMode.LOCAL_ONLY.value, enabled=True,
    )
    _base, is_remote = provider_destination(provider)
    assert is_remote is True


def test_lookalike_provider_is_refused_in_local_only_mode(session):
    """The stricter classification is what actually refuses the call."""
    provider = AIProviderRow(
        name="lookalike", provider_type=ProviderType.LOCAL_RUNTIME.value,
        base_url="http://localhost.evil.com/v1", model="m",
        privacy_mode=PrivacyMode.LOCAL_ONLY.value, enabled=True,
    )
    with pytest.raises(gateway.PrivacyRefused):
        gateway.check_permission(provider)


# ---------------------------------------------------------------------------
# 3. the database stores redacted text only
# ---------------------------------------------------------------------------


SECRET = "sk-live-0123456789abcdefghijklmnop"


def _provider(session, **kwargs) -> AIProviderRow:
    row = AIProviderRow(
        name=kwargs.get("name", "p"),
        provider_type=ProviderType.CUSTOM_OPENAI_COMPAT.value,
        base_url=kwargs.get("base_url", "http://127.0.0.1:9/v1"),
        model=kwargs.get("model", "m"),
        privacy_mode=kwargs.get("privacy_mode", PrivacyMode.LOCAL_ONLY.value),
        enabled=kwargs.get("enabled", True),
    )
    session.add(row)
    session.flush()
    return row


def test_stored_turn_is_the_redacted_prompt_not_the_raw_one(
        session, monkeypatch, no_keyring):
    """The user turn in the database is what was sent — already redacted."""
    provider = _provider(session)

    def fake_ask(session_, provider_id, purpose, content, **kwargs):
        return gateway.AIResponse(text="ok", model="m", latency_ms=1,
                                  redactions=1, remote=False)

    monkeypatch.setattr(gateway, "ask", fake_ask)
    ai_chat_service.chat(session, f"my key is {SECRET}", provider_id=provider.id)

    turn = session.query(AIChatRow).filter_by(role="user").one()
    assert SECRET not in turn.content
    assert "[REDACTED" in turn.content
    assert turn.prompt_sha256


def test_ai_request_row_holds_a_hash_and_never_the_prompt(
        session, monkeypatch, no_keyring):
    provider = _provider(session)

    def fake_dispatch(req, prov):
        return "ok", "fine", ""

    monkeypatch.setattr(gateway, "_dispatch", fake_dispatch)
    gateway.ask(session, provider.id, "chat", f"token {SECRET}")

    row = session.query(AIRequestRow).one()
    assert row.prompt_sha256
    assert len(row.prompt_sha256) == 64
    assert row.redactions_applied >= 1
    # No column of the row may contain the secret.
    dumped = " ".join(str(v) for v in row.__dict__.values())
    assert SECRET not in dumped


def test_no_raw_prompt_column_exists_on_the_request_row():
    """The schema has no place to put a prompt, so none can leak into it."""
    columns = {c.name for c in AIRequestRow.__table__.columns}
    assert "prompt" not in columns
    assert "content" not in columns
    assert "prompt_text" not in columns
    assert "prompt_sha256" in columns
