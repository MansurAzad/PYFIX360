"""Tests for the integrations knowledge catalogue."""

from __future__ import annotations

from pyfix360.integrations.catalog import ALL, CATALOG, by_key, detect_markers
from pyfix360.services import integration_service


def test_catalog_non_empty_and_unique_keys():
    keys = [it.key for it in CATALOG]
    assert len(keys) == len(set(keys))
    assert len(keys) >= 20


def test_required_integrations_present():
    for k in ("github", "supabase", "vercel", "docker", "git", "aws", "npm", "uv"):
        assert by_key(k) is not None, k


def test_each_integration_has_commands():
    for it in CATALOG:
        assert it.commands, it.key
        assert it.summary, it.key


def test_by_key_unknown():
    assert by_key("does-not-exist") is None


def test_search():
    res = integration_service.search("supabase")
    assert any(it.key == "supabase" for it in res)
    res2 = integration_service.search("webhook")
    assert isinstance(res2, list)


def test_list_by_category():
    hosting = integration_service.list_integrations("hosting")
    assert all(it.category == "hosting" for it in hosting)
    assert len(hosting) >= 5


def test_detection_runs_on_repo():
    # The repo itself uses git + uv (pyproject) at minimum.
    res = integration_service.detect_for_project(".")
    detected = {r.key for r in res if r.detected}
    assert "git" in detected


def test_detection_result_shape():
    res = integration_service.detect_for_project(".")
    # Detection covers the tools and the app-builder platforms alike.
    assert len(res) == len(ALL)
    for r in res:
        assert isinstance(r.detected, bool)
        assert isinstance(r.markers_found, list)


def test_detect_markers_helper():
    # smoke: helper returns list of keys
    out = detect_markers(".")
    assert isinstance(out, list)
    assert "git" in out
