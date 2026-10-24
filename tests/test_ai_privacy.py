"""Tests for AI gateway privacy enforcement and redaction-before-send."""

import pytest

from pyfix360.ai import gateway
from pyfix360.db.models import AIRequestRow
from pyfix360.models import AIProviderConfig, PrivacyMode, ProviderType
from pyfix360.services import ai_provider_service


def _provider(session, **kwargs) -> int:
    cfg = AIProviderConfig(
        name=kwargs.get("name", "local-ollama"),
        provider_type=kwargs.get("provider_type", ProviderType.LOCAL_RUNTIME),
        base_url=kwargs.get("base_url", "http://localhost:11434/v1"),
        model=kwargs.get("model", "qwen2.5-coder"),
        privacy_mode=kwargs.get("privacy_mode", PrivacyMode.LOCAL_ONLY),
        enabled=kwargs.get("enabled", True),
    )
    row = ai_provider_service.upsert_provider(session, cfg)
    return row.id


def test_disabled_provider_refused(session, data_dir):
    _provider(session, enabled=False)
    row = ai_provider_service.list_providers(session)[0]
    with pytest.raises(gateway.PrivacyRefused, match="disabled"):
        gateway.check_permission(row)


def test_remote_refused_in_local_only_mode(session):
    _provider(session, name="openrouter", provider_type=ProviderType.OPENROUTER,
              privacy_mode=PrivacyMode.LOCAL_ONLY)
    row = ai_provider_service.list_providers(session)[0]
    with pytest.raises(gateway.PrivacyRefused, match="LOCAL_ONLY"):
        gateway.check_permission(row)


def test_remote_requires_full_approval(session):
    _provider(session, name="openrouter", provider_type=ProviderType.OPENROUTER,
              privacy_mode=PrivacyMode.REDACTED_REMOTE)
    row = ai_provider_service.list_providers(session)[0]
    with pytest.raises(gateway.PrivacyRefused, match="FULL_REMOTE_APPROVED"):
        gateway.check_permission(row)


def test_remote_allowed_with_full_approval(session):
    _provider(session, name="openrouter", provider_type=ProviderType.OPENROUTER,
              privacy_mode=PrivacyMode.FULL_REMOTE_APPROVED)
    row = ai_provider_service.list_providers(session)[0]
    assert gateway.check_permission(row) is True
    base, is_remote = gateway.provider_destination(row)
    assert is_remote and "openrouter.ai" in base


def test_local_endpoint_not_remote(session):
    _provider(session)
    row = ai_provider_service.list_providers(session)[0]
    base, is_remote = gateway.provider_destination(row)
    assert not is_remote


def test_custom_url_classified_remote(session):
    _provider(session, name="corp", provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
              base_url="https://ai.company.internal/v1",
              privacy_mode=PrivacyMode.FULL_REMOTE_APPROVED)
    row = [p for p in ai_provider_service.list_providers(session) if p.name == "corp"][0]
    _, is_remote = gateway.provider_destination(row)
    assert is_remote


def test_unreachable_local_provider_logs_failed_request(session, data_dir):
    pid = _provider(session, base_url="http://localhost:1/v1")
    with pytest.raises(gateway.AIGatewayError):
        gateway.ask(session, pid, "connectivity_check", "hello")
    req = session.query(AIRequestRow).filter_by(provider_id=pid).one()
    assert req.status == "error"
    assert req.remote_allowed is False


def test_prompt_hash_recorded_not_content(session, data_dir):
    pid = _provider(session, base_url="http://localhost:1/v1")
    secret_source = 'password = "hunter2hunter2"'
    with pytest.raises(gateway.AIGatewayError):
        gateway.ask(session, pid, "explain", secret_source)
    req = session.query(AIRequestRow).one()
    assert req.prompt_sha256 and len(req.prompt_sha256) == 64
    # metadata table never stores the raw prompt or the secret
    dump = str(req.__dict__)
    assert "hunter2hunter2" not in dump


def test_upsert_and_to_model_roundtrip(session, no_keyring):
    cfg = AIProviderConfig(
        name="prov", provider_type=ProviderType.OPENROUTER,
        base_url="https://openrouter.ai/api/v1", model="gpt-x",
        privacy_mode=PrivacyMode.FULL_REMOTE_APPROVED, enabled=True,
    )
    row = ai_provider_service.upsert_provider(session, cfg, api_key="sk-secret-123456")
    model = ai_provider_service.to_model(row)
    assert model.has_api_key is True
    # key is NOT in the database row
    assert "sk-secret-123456" not in str(row.__dict__)


def test_remove_provider_deletes_credential(session, no_keyring):
    from pyfix360.credentials import store as cred_store

    cfg = AIProviderConfig(name="prov2", provider_type=ProviderType.LOCAL_RUNTIME,
                           base_url="http://localhost:11434/v1", model="m")
    row = ai_provider_service.upsert_provider(session, cfg, api_key="abc123xyz789")
    assert cred_store.get_credential(f"provider-{row.id}") is not None
    ai_provider_service.remove_provider(session, row.id)
    assert cred_store.get_credential(f"provider-{row.id}") is None


# ---------------------------------------------------------------------------
# classification fails safe
# ---------------------------------------------------------------------------


def test_unknown_aggregator_url_is_treated_as_remote(session):
    """A provider we have never heard of must not be assumed local."""
    _provider(session, name="mystery", provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
              base_url="https://some.new.aggregator.example/v1",
              privacy_mode=PrivacyMode.LOCAL_ONLY)
    row = [p for p in ai_provider_service.list_providers(session)
           if p.name == "mystery"][0]
    _, is_remote = gateway.provider_destination(row)
    assert is_remote, "unknown hosts must be treated as remote"
    with pytest.raises(gateway.PrivacyRefused):
        gateway.check_permission(row)


def test_known_remote_hosts_are_flagged(session):
    for host in ("https://api.groq.com/openai/v1",
                 "https://api.deepseek.com/v1",
                 "https://api.mistral.ai/v1",
                 "https://api.together.xyz/v1"):
        row = type("P", (), {
            "provider_type": ProviderType.CUSTOM_OPENAI_COMPAT.value,
            "base_url": host, "name": "x", "enabled": True,
            "privacy_mode": PrivacyMode.LOCAL_ONLY.value,
        })()
        _, is_remote = gateway.provider_destination(row)
        assert is_remote, host


def test_local_runtime_with_remote_url_is_still_remote(session):
    """A 'local_runtime' type pointing at a public URL must not be trusted."""
    _provider(session, name="mislabeled",
              provider_type=ProviderType.LOCAL_RUNTIME,
              base_url="https://api.groq.com/openai/v1",
              privacy_mode=PrivacyMode.LOCAL_ONLY)
    row = [p for p in ai_provider_service.list_providers(session)
           if p.name == "mislabeled"][0]
    _, is_remote = gateway.provider_destination(row)
    assert is_remote


def test_loopback_variants_are_local():
    for host in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1"):
        row = type("P", (), {
            "provider_type": ProviderType.LOCAL_RUNTIME.value,
            "base_url": host, "name": "x", "enabled": True,
            "privacy_mode": PrivacyMode.LOCAL_ONLY.value,
        })()
        _, is_remote = gateway.provider_destination(row)
        assert not is_remote, host


def test_provider_destination_survives_empty_url():
    row = type("P", (), {
        "provider_type": ProviderType.CUSTOM_OPENAI_COMPAT.value,
        "base_url": "", "name": "x", "enabled": True,
        "privacy_mode": PrivacyMode.LOCAL_ONLY.value,
    })()
    base, is_remote = gateway.provider_destination(row)
    assert base == ""
    assert is_remote is True, "an empty endpoint must not be assumed local"


# ---------------------------------------------------------------------------
# catalogue presets respect privacy defaults
# ---------------------------------------------------------------------------


def test_preset_local_provider_needs_no_key(session):
    from pyfix360.services import ai_provider_service

    row = ai_provider_service.create_from_preset(session, "ollama")
    assert row.enabled is False, "a new provider starts disabled until asked for"
    assert row.privacy_mode == PrivacyMode.LOCAL_ONLY.value
    assert row.base_url.startswith("http://localhost")


def test_preset_remote_provider_is_locked_down(session):
    from pyfix360.services import ai_provider_service

    row = ai_provider_service.create_from_preset(session, "openrouter", enable=True)
    # Even when explicitly enabled, a remote preset stays LOCAL_ONLY, so it is
    # still refused until the user changes the privacy mode deliberately.
    assert row.privacy_mode == PrivacyMode.LOCAL_ONLY.value
    with pytest.raises(gateway.PrivacyRefused):
        gateway.check_permission(row)


def test_create_from_preset_rejects_unknown_key(session):
    from pyfix360.services import ai_provider_service

    with pytest.raises(ai_provider_service.ProviderError):
        ai_provider_service.create_from_preset(session, "no-such-provider")


def test_verification_defaults_to_unverified(session):
    from pyfix360.services import ai_provider_service

    row = ai_provider_service.create_from_preset(session, "ollama")
    model = ai_provider_service.to_model(row)
    assert model.last_verify_ok is None, (
        "a provider must not claim verification before any call succeeded")


# ---------------------------------------------------------------------------
# chat service refuses honestly
# ---------------------------------------------------------------------------


def test_chat_refused_when_ai_is_off(session):
    from pyfix360.models import AIBehavior
    from pyfix360.services import ai_chat_service, settings_service

    s = settings_service.get_settings(session)
    s.ai_behavior = AIBehavior.OFF
    settings_service.save_settings(session, s)
    with pytest.raises(ai_chat_service.AIUsageRefused, match="switched off"):
        ai_chat_service.chat(session, "hello")


def test_chat_refused_without_provider(session):
    from pyfix360.services import ai_chat_service

    with pytest.raises(ai_chat_service.AIUsageRefused, match="No AI provider"):
        ai_chat_service.chat(session, "hello")


def test_chat_refused_for_disabled_provider(session):
    from pyfix360.services import ai_chat_service

    pid = _provider(session, enabled=False)
    with pytest.raises(ai_chat_service.AIUsageRefused, match="disabled"):
        ai_chat_service.chat(session, "hello", provider_id=pid)


def test_repo_content_refused_for_remote_without_the_gate(session):
    from pyfix360.models import AIBehavior
    from pyfix360.services import ai_chat_service, settings_service

    _provider(session, name="remote", provider_type=ProviderType.OPENROUTER,
              privacy_mode=PrivacyMode.FULL_REMOTE_APPROVED, enabled=True)
    s = settings_service.get_settings(session)
    s.ai_behavior = AIBehavior.SUGGEST
    s.ai_allow_repo_content = False
    settings_service.save_settings(session, s)

    row = [p for p in ai_provider_service.list_providers(session)
           if p.name == "remote"][0]
    with pytest.raises(ai_chat_service.AIUsageRefused, match="repository content"):
        ai_chat_service.check_source_transmission(session, row)


def test_repo_content_refused_for_remote_with_wrong_privacy_mode(session):
    from pyfix360.services import ai_chat_service, settings_service

    _provider(session, name="remote2",
              provider_type=ProviderType.OPENROUTER,
              privacy_mode=PrivacyMode.REDACTED_REMOTE, enabled=True)
    s = settings_service.get_settings(session)
    s.ai_allow_repo_content = True
    settings_service.save_settings(session, s)
    row = [p for p in ai_provider_service.list_providers(session)
           if p.name == "remote2"][0]
    with pytest.raises(ai_chat_service.AIUsageRefused, match="full_remote_approved"):
        ai_chat_service.check_source_transmission(session, row)


def test_local_provider_never_needs_the_repo_gate(session):
    from pyfix360.services import ai_chat_service

    _provider(session, name="localok")
    row = [p for p in ai_provider_service.list_providers(session)
           if p.name == "localok"][0]
    # must not raise: a local runtime never transmits anything
    ai_chat_service.check_source_transmission(session, row)


def test_preview_prompt_redacts_and_labels(session):
    from pyfix360.services import ai_chat_service

    preview = ai_chat_service.preview_prompt(
        session, 'api_key = "sk-abcdefghijklmnopqrstuvwx"')
    assert "sk-abcdefghijklmnopqrstuvwx" not in preview
    assert "redactions applied" in preview


def test_preview_prompt_caps_size(session):
    from pyfix360.services import ai_chat_service, settings_service

    s = settings_service.get_settings(session)
    s.ai_max_prompt_chars = 1000
    settings_service.save_settings(session, s)
    preview = ai_chat_service.preview_prompt(session, "x" * 5000)
    assert "omitted by the prompt size cap" in preview
    assert len(preview) < 5000


def test_history_stores_redacted_only(session):
    from pyfix360.services import ai_chat_service

    ai_chat_service.record_turn(
        session, project_id=None, provider_id=None, role="user",
        content="redacted text", purpose="chat")
    rows = ai_chat_service.history(session)
    assert rows and rows[0].content == "redacted text"
    assert len(rows[0].prompt_sha256) <= 64
