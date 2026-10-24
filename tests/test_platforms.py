"""Tests for the AI app-builder platform playbooks and their operation paths.

Nothing here touches the network: the REST clients are exercised by
intercepting their request method, and the browser layer is only probed for
its availability and its selector data.
"""

from __future__ import annotations

import pytest

from pyfix360.db.models import AuditEventRow
from pyfix360.integrations import api_clients, browser
from pyfix360.integrations.catalog import (
    ALL,
    PLATFORMS,
    Integration,
    by_key,
    by_kind,
)
from pyfix360.models import AccessLevel, PrivacyMode
from pyfix360.services import integration_service, platform_service, settings_service

EXPECTED_PLATFORMS = ("lovable", "trickle", "replit", "emergent", "v0",
                       "builder", "ai-studio")


# --------------------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------------------


def test_all_seven_platforms_present():
    keys = {p.key for p in PLATFORMS}
    for key in EXPECTED_PLATFORMS:
        assert key in keys, key


def test_platforms_are_marked_as_platforms():
    for p in PLATFORMS:
        assert p.kind == "platform", p.key
        assert p.category in ("app-builder", "ai-workbench"), p.key
        assert by_kind("platform")


def test_every_platform_carries_a_full_playbook():
    for p in PLATFORMS:
        assert p.prompt_patterns, p.key
        assert p.command_dialect, p.key
        assert p.qa_flow, p.key
        assert p.situations, p.key
        assert p.summary, p.key
        assert p.auth, p.key


def test_platforms_carry_a_verification_note():
    for p in PLATFORMS:
        assert p.reviewed_on, p.key
        assert p.verified_live is False, p.key
        assert any("verify" in pit.lower() or "confirm" in pit.lower()
                   for pit in p.pitfalls), p.key


def test_schema_exposes_the_new_fields():
    fields = Integration.__dataclass_fields__
    for name in ("prompt_patterns", "command_dialect", "qa_flow", "situations",
                 "has_cli", "verified_live", "reviewed_on"):
        assert name in fields, name


def test_tools_keep_their_old_shape():
    # The plain CLI tools must not have acquired platform fields by accident.
    for it in ALL:
        if it.kind == "tool":
            assert it.prompt_patterns == ()
            assert it.qa_flow == ()
            assert it.verified_live is False


def test_by_key_resolves_a_platform():
    assert by_key("lovable") is not None
    assert by_key("lovable").kind == "platform"


def test_cli_flag_matches_reality():
    assert by_key("builder").has_cli is True
    assert by_key("builder").cli == "builder"
    assert by_key("lovable").has_cli is False


# --------------------------------------------------------------------------------------
# service layer
# --------------------------------------------------------------------------------------


def test_playbook_catalog_has_one_entry_per_platform():
    books = integration_service.playbook_catalog()
    assert len(books) == len(PLATFORMS)
    for book in books:
        assert book.prompt_patterns
        assert book.qa_flow
        assert book.situations


def test_playbook_carries_the_verification_warning():
    book = integration_service.platform_playbook("lovable")
    assert book is not None
    assert book.verified_live is False
    assert any("verify" in w.lower() for w in book.warnings)


def test_playbook_warns_about_browser_only_platforms():
    book = integration_service.platform_playbook("trickle")
    assert book.operation_mode == "browser"
    assert any("web session" in w for w in book.warnings)


def test_search_reaches_the_new_fields():
    # "credit" only appears in the playbook prose, never in the old fields.
    hits = integration_service.search("credit limit")
    assert any(it.key == "lovable" for it in hits)
    hits2 = integration_service.search("row level security")
    assert any(it.key == "lovable" for it in hits2)


def test_operation_modes_grouping():
    grouped = integration_service.operation_modes()
    # Every platform declares how it can be driven, and no platform is left
    # unclassified.
    assert set(grouped) >= {"browser", "both"}
    grouped_count = sum(len(v) for v in grouped.values())
    assert grouped_count == len(PLATFORMS)
    assert all(it.operation_mode for it in PLATFORMS)


# --------------------------------------------------------------------------------------
# API clients
# --------------------------------------------------------------------------------------


def test_client_for_rejects_browser_only_platforms():
    with pytest.raises(api_clients.UnsupportedOperation):
        api_clients.client_for("trickle", "key")


def test_client_for_requires_a_key():
    with pytest.raises(api_clients.MissingCredential):
        api_clients.client_for("v0", "")


def test_client_for_builds_a_client():
    client = api_clients.client_for("v0", "secret")
    assert isinstance(client, api_clients.V0Client)
    assert client.api_key == "secret"


def test_credential_name_is_namespaced():
    assert api_clients.credential_name("v0") == "platform-v0"


def test_v0_submit_prompt_posts_the_message(monkeypatch):
    seen = {}

    def fake_request(self, method, path, **kwargs):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = kwargs.get("body")
        return {"id": "chat_123", "status": "finished"}

    monkeypatch.setattr(api_clients.V0Client, "_request", fake_request)
    result = api_clients.V0Client("secret").submit_prompt("Build a todo app")
    assert seen["method"] == "POST"
    assert seen["path"] == "/chats"
    assert seen["body"]["message"] == "Build a todo app"
    assert result.handle == "chat_123"


def test_v0_system_instruction_is_kept(monkeypatch):
    seen = {}

    def fake_request(self, method, path, **kwargs):
        seen.update(kwargs.get("body") or {})
        return {"id": "chat_1"}

    monkeypatch.setattr(api_clients.V0Client, "_request", fake_request)
    api_clients.V0Client("secret").submit_prompt("go", system="be brief")
    assert seen["message"] == "be brief\n\ngo"


def test_lovable_refuses_to_build_from_a_prompt():
    client = api_clients.LovableClient("secret")
    with pytest.raises(api_clients.UnsupportedOperation):
        client.submit_prompt("build me an app")


def test_lovable_lists_projects(monkeypatch):
    monkeypatch.setattr(
        api_clients.LovableClient, "_request",
        lambda self, m, p, **kw: {"projects": [{"id": "p1"}]})
    result = api_clients.LovableClient("secret").list_projects()
    assert result.output


def test_replit_graphql_builds_the_request(monkeypatch):
    seen = {}

    def fake_request(self, method, path, **kwargs):
        seen.update(kwargs.get("body") or {})
        return {"data": {"createWithHost": {"id": "repl_1", "url": "https://r"}}}

    monkeypatch.setattr(api_clients.ReplitClient, "_request", fake_request)
    result = api_clients.ReplitClient("token").create_workspace("My app")
    assert "createWithHost" in seen["query"]
    assert seen["variables"]["title"] == "My app"
    assert result.handle == "repl_1"


def test_replit_graphql_errors_are_surfaced(monkeypatch):
    monkeypatch.setattr(
        api_clients.ReplitClient, "_request",
        lambda self, m, p, **kw: {"errors": [{"message": "nope"}]})
    with pytest.raises(api_clients.RemoteCallFailed):
        api_clients.ReplitClient("token").create_workspace("x")


def test_ai_studio_chat_completions(monkeypatch):
    seen = {}

    def fake_request(self, method, path, **kwargs):
        seen["path"] = path
        seen["body"] = kwargs.get("body")
        return {"choices": [{"message": {"content": "hello"}}]}

    monkeypatch.setattr(api_clients.AIStudioClient, "_request", fake_request)
    result = api_clients.AIStudioClient("key").submit_prompt("hi")
    assert seen["path"] == "/chat/completions"
    assert seen["body"]["messages"][-1]["content"] == "hi"
    assert result.output == "hello"


def test_builder_key_travels_in_the_query_string(monkeypatch):
    seen = {}

    def fake_request(self, method, path, **kwargs):
        seen.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(api_clients.BuilderClient, "_request", fake_request)
    api_clients.BuilderClient("bkey").get_content("page", url="/home")
    assert seen["query"]["url"] == "/home"


# --------------------------------------------------------------------------------------
# browser layer
# --------------------------------------------------------------------------------------


def test_playwright_availability_is_reported_not_raised():
    available, reason = browser.playwright_available()
    assert isinstance(available, bool)
    if not available:
        assert reason


def test_every_platform_has_a_landing_page_and_selectors():
    for key in EXPECTED_PLATFORMS:
        assert key in browser.LANDING_URLS, key
        assert key in browser.SELECTORS, key
        assert browser.SELECTORS[key].prompt_input
        assert browser.SELECTORS[key].send_button


def test_deep_links_exist_only_where_they_are_documented():
    assert set(browser.DEEP_LINKS) == {"lovable", "ai-studio"}
    assert "{prompt}" in browser.DEEP_LINKS["lovable"]
    assert "{prompt}" in browser.DEEP_LINKS["ai-studio"]


def test_browser_session_without_playwright_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(browser, "playwright_available", lambda: (False, "not installed"))
    with pytest.raises(browser.AutomationUnavailable) as excinfo, browser.BrowserSession():
        pass
    assert "pip install" in str(excinfo.value)


def test_unknown_platform_has_no_strategy():
    session = browser.BrowserSession()
    with pytest.raises(browser.AutomationFailed):
        session.selectors_for("not-a-platform")


# --------------------------------------------------------------------------------------
# policy gates
# --------------------------------------------------------------------------------------


def _allow(session, *, privacy=PrivacyMode.FULL_REMOTE_APPROVED,
           access=AccessLevel.FULL) -> None:
    settings = settings_service.get_settings(session)
    settings.privacy_mode = privacy
    settings.access_remote_ai = access
    settings_service.save_settings(session, settings,
                                   keys=["privacy_mode", "access_remote_ai"])


def test_readiness_lists_every_platform(session):
    rows = platform_service.readiness()
    assert {r.key for r in rows} == set(EXPECTED_PLATFORMS)
    assert all(isinstance(r.ready, bool) for r in rows)


def test_remote_call_is_refused_in_local_only_mode(session):
    _allow(session, privacy=PrivacyMode.LOCAL_ONLY)
    with pytest.raises(platform_service.PlatformRefused) as excinfo:
        platform_service.submit_prompt(session, "v0", "build an app")
    assert "privacy mode" in str(excinfo.value)


def test_remote_call_is_refused_when_access_is_blocked(session):
    _allow(session, access=AccessLevel.BLOCKED)
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.submit_prompt(session, "v0", "build an app")


def test_preview_redacts_before_sending(session):
    text = "here is my key sk-abcdefghijklmnopqrstuvwx"
    preview = platform_service.preview_prompt(session, "v0", text)
    assert "sk-abcdefghijklmnopqrstuvwx" not in preview
    assert "REDACTED" in preview


def test_unknown_platform_is_refused(session):
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.submit_prompt(session, "not-a-platform", "x")


# --------------------------------------------------------------------------------------
# the question → answer path
# --------------------------------------------------------------------------------------


def test_followup_answers_a_question_on_an_existing_job(session, monkeypatch):
    _allow(session)
    monkeypatch.setattr(platform_service.cred_store, "get_credential",
                        lambda name: "sk-test")
    seen: dict = {}

    def fake_request(self, method, path, **kwargs):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = kwargs.get("body")
        return {"id": "chat_1", "status": "finished"}

    monkeypatch.setattr(api_clients.V0Client, "_request", fake_request)
    result = platform_service.send_followup(session, "v0", "chat_1",
                                            "use TypeScript and Tailwind")
    assert seen["method"] == "POST"
    assert seen["path"] == "/chats/chat_1/messages"
    assert "TypeScript" in seen["body"]["message"]
    assert result.handle == "chat_1"


def test_followup_redacts_the_answer_before_it_leaves(session, monkeypatch):
    _allow(session)
    monkeypatch.setattr(platform_service.cred_store, "get_credential",
                        lambda name: "sk-test")
    seen: dict = {}

    def fake_request(self, method, path, **kwargs):
        seen["body"] = kwargs.get("body")
        return {"id": "chat_1"}

    monkeypatch.setattr(api_clients.V0Client, "_request", fake_request)
    platform_service.send_followup(session, "v0", "chat_1",
                                   "the key is sk-abcdefghijklmnopqrstuvwx")
    assert "sk-abcdefghijklmnopqrstuvwx" not in str(seen["body"])


def test_followup_is_audited(session, monkeypatch):
    _allow(session)
    monkeypatch.setattr(platform_service.cred_store, "get_credential",
                        lambda name: "sk-test")
    monkeypatch.setattr(api_clients.V0Client, "_request",
                        lambda self, m, p, **kw: {"id": "chat_1"})
    platform_service.send_followup(session, "v0", "chat_1", "use Tailwind")
    session.flush()
    actions = [row.action for row in session.query(AuditEventRow).all()]
    assert "platform.prompt.followup" in actions


def test_followup_without_a_message_is_refused(session, monkeypatch):
    _allow(session)
    monkeypatch.setattr(platform_service.cred_store, "get_credential",
                        lambda name: "sk-test")
    with pytest.raises(platform_service.PlatformRefused):
        platform_service.send_followup(session, "v0", "chat_1", "   ")


def test_every_platform_documents_the_questions_it_asks():
    for p in PLATFORMS:
        assert p.qa_flow, p.key
        for qa in p.qa_flow:
            assert qa.question.strip(), p.key
            assert qa.answer.strip(), p.key
