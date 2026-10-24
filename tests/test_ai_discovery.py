"""Tests for opt-in model discovery.

Discovery answers "what does this provider serve right now?". The guarantees
worth protecting are:

* it is **never** automatic (nothing happens unless ``discover`` is called)
* it only ever issues ``GET {base_url}/models``
* no source code or prompt is sent, only the credential to the configured host
* failures are reported honestly rather than papered over with the catalogue
"""

import json

import pytest

from pyfix360.ai import discovery
from pyfix360.models import AIProviderConfig, ProviderType
from pyfix360.services import ai_provider_service


class FakeResponse:
    def __init__(self, payload, *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _provider(session, **kwargs) -> int:
    cfg = AIProviderConfig(
        name=kwargs.get("name", "p"),
        provider_type=kwargs.get("provider_type", ProviderType.LOCAL_RUNTIME),
        base_url=kwargs.get("base_url", "http://localhost:11434/v1"),
        model=kwargs.get("model", "m"),
        enabled=kwargs.get("enabled", True),
    )
    return ai_provider_service.upsert_provider(session, cfg).id


def test_discover_uses_the_models_endpoint_only(session, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return FakeResponse({"data": [{"id": "qwen2.5-coder:7b"}]})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)

    assert result.ok
    assert captured["url"].endswith("/models")
    assert captured["method"] == "GET"
    assert captured["body"] is None, "discovery must not send a request body"
    assert "chat/completions" not in captured["url"]


def test_discover_never_sends_source_code(session, monkeypatch):
    """The request carries no content beyond auth — no prompt, no repo text."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return FakeResponse({"data": []})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    discovery.discover(session, pid)

    assert captured["body"] is None
    # only Accept (and possibly Authorization) headers are present
    allowed = {"Accept", "Authorization", "User-agent", "Content-type"}
    assert set(captured["headers"]) <= allowed


def test_discover_returns_sorted_model_ids(session, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse({"data": [{"id": "zeta"}, {"id": "alpha"},
                                      {"id": "mu"}]})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert [m.id for m in result.models] == ["alpha", "mu", "zeta"]


def test_discover_reads_context_length(session, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse({"data": [
            {"id": "big", "context_length": 128000},
            {"id": "small", "context_window": 8192},
        ]})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    by_id = {m.id: m.context_k for m in result.models}
    assert by_id["big"] == 128
    assert by_id["small"] == 8


def test_discover_reads_nested_context(session, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse({"data": [
            {"id": "nested", "top_provider": {"context_length": 200000}},
        ]})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert result.models[0].context_k == 200


def test_discover_skips_entries_without_an_id(session, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse({"data": [{"owned_by": "x"}, {"id": "good"},
                                      "not-a-dict", {"id": ""}]})

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert [m.id for m in result.models] == ["good"]


def test_http_error_is_reported_not_swallowed(session, monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert result.ok is False
    assert "401" in result.detail


def test_connection_error_is_reported(session, monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert result.ok is False
    assert "URLError" in result.detail


def test_non_json_response_is_reported(session, monkeypatch):
    class NotJson(FakeResponse):
        def __init__(self):
            self._body = b"<html>nope</html>"

    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda req, timeout=None: NotJson())
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert result.ok is False
    assert "not JSON" in result.detail


def test_wrong_shape_is_reported(session, monkeypatch):
    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse({"models": []}))
    pid = _provider(session)
    result = discovery.discover(session, pid)
    assert result.ok is False
    assert "data" in result.detail


def test_missing_provider_raises(session):
    with pytest.raises(discovery.DiscoveryError):
        discovery.discover(session, 99999)


def test_provider_without_endpoint_raises(session):
    cfg = AIProviderConfig(name="nolink", provider_type=ProviderType.CUSTOM_OPENAI_COMPAT,
                           base_url="", model="m")
    row = ai_provider_service.upsert_provider(session, cfg)
    with pytest.raises(discovery.DiscoveryError, match="no endpoint"):
        discovery.discover(session, row.id)


# ---------------------------------------------------------------------------
# merging with the catalogue stays honest about price
# ---------------------------------------------------------------------------


def test_merge_marks_known_free_ids():
    models = [discovery.DiscoveredModel(id="glm-4.6-free")]
    merged = discovery.merge_with_catalog(models, {"glm-4.6-free"})
    assert merged == [("glm-4.6-free", "known free / low cost")]


def test_merge_does_not_invent_pricing():
    models = [discovery.DiscoveredModel(id="mystery-model")]
    merged = discovery.merge_with_catalog(models, set())
    assert merged == [("mystery-model", "available")]


def test_merge_uses_owner_when_known():
    models = [discovery.DiscoveredModel(id="x", owned_by="acme")]
    merged = discovery.merge_with_catalog(models, set())
    assert merged == [("x", "acme")]


# ---------------------------------------------------------------------------
# service wrapper persists the result
# ---------------------------------------------------------------------------


def test_refresh_models_persists_discovered_ids(session, monkeypatch):
    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse(
                            {"data": [{"id": "a"}, {"id": "b"}]}))
    pid = _provider(session)
    ok, detail, ids = ai_provider_service.refresh_models(session, pid)
    assert ok
    assert ids == ["a", "b"]
    row = session.get(ai_provider_service.AIProviderRow, pid)
    assert row.discovered_models == ["a", "b"]


def test_discovered_annotations_flag_unknown_prices(session, monkeypatch):
    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse(
                            {"data": [{"id": "never-heard-of-it"}]}))
    pid = _provider(session)
    ai_provider_service.refresh_models(session, pid)
    annotated = ai_provider_service.discovered_annotated(session, pid)
    assert annotated
    assert "unknown price" in annotated[0].annotation


def test_refresh_failure_leaves_previous_list(session, monkeypatch):
    import urllib.error

    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse(
                            {"data": [{"id": "kept"}]}))
    pid = _provider(session)
    ai_provider_service.refresh_models(session, pid)

    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(discovery.urllib.request, "urlopen", boom)
    ok, detail, ids = ai_provider_service.refresh_models(session, pid)
    assert ok is False
    assert ids == ["kept"], "a failed refresh must not wipe the known list"
