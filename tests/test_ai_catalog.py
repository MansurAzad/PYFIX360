"""Tests for the curated AI provider/model catalogue.

The catalogue is data, so these tests guard its *integrity*: no duplicate
provider keys, every model id is non-empty, remote presets are not silently
marked local, and the honesty rules hold (nothing claims to be live-verified
unless a real call succeeded).
"""

from pyfix360.ai import catalog


def test_no_duplicate_provider_keys():
    keys = [p.key for p in catalog.all_presets()]
    assert len(keys) == len(set(keys)), f"duplicate provider keys: {keys}"
    assert "*" not in keys


def test_no_duplicate_labels():
    labels = [p.label for p in catalog.all_presets()]
    assert len(labels) == len(set(labels))


def test_every_model_has_required_fields():
    for preset, model in catalog.all_models():
        assert model.id, f"{preset.key} has a model with an empty id"
        assert model.label, f"{model.id} has no label"
        assert model.vendor, f"{model.id} has no vendor"
        assert model.cost in ("free", "cheap", "paid", "local"), model.cost


def test_local_presets_are_actually_local():
    for preset in catalog.all_presets():
        if preset.provider_type.value == "local_runtime":
            assert not preset.remote, f"{preset.key} is local but marked remote"
            assert not preset.requires_key, f"{preset.key} needs no key"
            assert ("localhost" in preset.base_url
                    or "127.0.0.1" in preset.base_url), preset.base_url
            for model in preset.models:
                assert model.cost == "local"


def test_remote_presets_are_marked_remote():
    for preset in catalog.all_presets():
        if preset.provider_type.value == "local_runtime":
            continue
        if preset.key == "custom":
            continue  # user-supplied; may point anywhere
        assert preset.remote, f"{preset.key} is remote but not marked so"


def test_nothing_is_claimed_verified_by_default():
    """We must never ship a model claiming a live check we did not perform."""
    for _preset, model in catalog.all_models():
        assert model.verified_live is False, (
            f"{model.id} claims verified_live without a recorded call")


def test_free_and_local_models_exist():
    free = catalog.free_models()
    assert free, "expected at least one free model entry"
    assert any(m.cost == "local" for _p, m in catalog.all_models())


def test_cost_values_are_annotated_not_invented():
    """Every model must carry a cost bucket rather than an implied price."""
    buckets = {m.cost for _p, m in catalog.all_models()}
    assert buckets <= {"free", "cheap", "paid", "local"}


def test_lookup_helpers():
    assert catalog.preset("openrouter") is not None
    assert catalog.preset("does-not-exist") is None
    assert catalog.preset_by_label("Ollama (local, offline)").key == "ollama"
    assert catalog.models_for("openrouter")
    assert catalog.models_for("nope") == ()


def test_find_model_by_id_and_label():
    hits = catalog.find_model("z-ai/glm-4.6")
    assert hits and hits[0][1].id == "z-ai/glm-4.6"
    label_hits = catalog.find_model("GLM 4.6")
    assert label_hits


def test_aliases_explain_where_names_map():
    aliases = catalog.aliases()
    assert "Big Pickle" in aliases
    assert "GLM Flash 5.3" in aliases
    # aggregator-only names must be described as such, not invented
    for name, targets in aliases.items():
        assert targets, f"{name} has no mapping"


def test_catalog_has_review_date():
    assert catalog.CATALOG_REVIEWED
    assert len(catalog.CATALOG_REVIEWED) >= 8


def test_aggregator_note_is_actionable():
    note = catalog.aggregator_note()
    assert "OpenRouter" in note or "aggregator" in note.lower()


def test_the_named_models_are_represented():
    """The user explicitly asked about these; each must resolve somehow."""
    ids = {m.id for _p, m in catalog.all_models()}
    labels = {m.label for _p, m in catalog.all_models()}
    # present directly
    assert any("glm" in i.lower() for i in ids)
    assert any("minimax" in i.lower() for i in ids)
    # present via a documented alias when they are aggregator-only
    aliases = catalog.aliases()
    assert "LongCat 2.5 Preview" in aliases
    assert "MiMo V2.6" in aliases
    assert labels  # sanity
