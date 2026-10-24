"""AI provider management: catalogue, CRUD, credentials, testing, discovery.

The provider list is the durable record of every AI endpoint the user has
configured. A provider can be created from a catalogue preset (one click) or
by hand for an endpoint we do not know about.

Credentials never touch SQLite — they go to the OS keyring via the credential
store. Only the presence of a key is mirrored on the row (``has_api_key``) so
the UI can show state without reading the secret.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.ai import catalog, discovery, gateway
from pyfix360.credentials import store as cred_store
from pyfix360.db.models import AIProviderRow
from pyfix360.models import (
    AIProviderConfig,
    DiscoveredModelInfo,
    PrivacyMode,
    ProviderType,
)
from pyfix360.services import audit


class ProviderError(Exception):
    pass


def _cred_key(provider_id: int) -> str:
    return f"provider-{provider_id}"


# --------------------------------------------------------------------------------------
# catalogue helpers
# --------------------------------------------------------------------------------------


def catalogue() -> tuple[catalog.ProviderPreset, ...]:
    return catalog.all_presets()


def presets_by_label() -> list[tuple[str, catalog.ProviderPreset]]:
    return [(p.label, p) for p in catalog.all_presets()]


def preset_for_label(label: str) -> catalog.ProviderPreset | None:
    return catalog.preset_by_label(label)


def models_for_preset(key: str) -> tuple[catalog.ModelEntry, ...]:
    return catalog.models_for(key)


def free_model_choices() -> list[tuple[str, str, str]]:
    """(provider label, model id, 'cost — label') for the model picker."""
    out = []
    for preset, model in catalog.free_models():
        out.append((preset.label, model.id, f"{model.cost} — {model.label}"))
    return out


def model_notes(provider_key: str, model_id: str) -> str:
    for model in catalog.models_for(provider_key):
        if model.id == model_id:
            parts = [model.label, f"by {model.vendor}"]
            if model.context_k:
                parts.append(f"~{model.context_k}k context")
            parts.append(model.cost)
            if model.good_for:
                parts.append("good for: " + ", ".join(model.good_for))
            if model.notes:
                parts.append(model.notes)
            return " — ".join(parts)
    return ""


def create_from_preset(session: Session, preset_key: str, *,
                       model: str | None = None,
                       name: str | None = None,
                       api_key: str | None = None,
                       enable: bool = False) -> AIProviderRow:
    """Create (or update) a provider from a catalogue preset."""
    preset = catalog.preset(preset_key)
    if preset is None:
        raise ProviderError(f"unknown preset '{preset_key}'")
    chosen = model or (preset.models[0].id if preset.models else "")
    config = AIProviderConfig(
        name=name or preset.label,
        provider_type=preset.provider_type,
        base_url=preset.base_url,
        model=chosen,
        # Remote providers always start locked down; enabling is deliberate.
        privacy_mode=PrivacyMode.LOCAL_ONLY,
        enabled=enable and not preset.remote,
    )
    row = upsert_provider(session, config, api_key=api_key)
    row.catalog_key = preset.key
    session.flush()
    return row


def apply_preset_defaults(session: Session, provider_id: int) -> AIProviderRow:
    """Fill an existing row's blanks from its catalogue preset."""
    row = session.get(AIProviderRow, provider_id)
    if row is None:
        raise ProviderError(f"provider {provider_id} not found")
    preset = catalog.preset(row.catalog_key) if row.catalog_key else None
    if preset is None:
        return row
    if not row.base_url:
        row.base_url = preset.base_url
    if not row.model and preset.models:
        row.model = preset.models[0].id
    session.flush()
    return row


# --------------------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------------------


def upsert_provider(
    session: Session,
    config: AIProviderConfig,
    *,
    api_key: str | None = None,
) -> AIProviderRow:
    row = None
    if config.id:
        row = session.get(AIProviderRow, config.id)
    if row is None:
        row = session.scalars(
            select(AIProviderRow).where(AIProviderRow.name == config.name)
        ).first()
    if row is None:
        row = AIProviderRow(name=config.name)
        session.add(row)
    row.provider_type = config.provider_type.value
    row.base_url = config.base_url
    row.model = config.model
    row.timeout_s = config.timeout_s
    row.retry_limit = config.retry_limit
    row.privacy_mode = config.privacy_mode.value
    row.enabled = config.enabled
    if config.catalog_key:
        row.catalog_key = config.catalog_key
    session.flush()
    if api_key is not None:
        if api_key == "":
            cred_store.delete_credential(_cred_key(row.id))
        else:
            cred_store.set_credential(_cred_key(row.id), api_key)
    audit.record(session, "ai.provider.upsert", target=f"provider#{row.id}",
                 details=f"{config.name} type={config.provider_type.value} "
                         f"model={config.model or '-'} "
                         f"remote={config.privacy_mode.value != PrivacyMode.LOCAL_ONLY.value}",
                 actor="user")
    return row


def list_providers(session: Session) -> list[AIProviderRow]:
    return list(session.scalars(select(AIProviderRow).order_by(AIProviderRow.id)))


def to_model(row: AIProviderRow) -> AIProviderConfig:
    return AIProviderConfig(
        id=row.id, name=row.name, provider_type=ProviderType(row.provider_type),
        base_url=row.base_url, model=row.model, timeout_s=row.timeout_s,
        retry_limit=row.retry_limit, privacy_mode=PrivacyMode(row.privacy_mode),
        enabled=row.enabled,
        has_api_key=cred_store.get_credential(_cred_key(row.id)) is not None,
        catalog_key=row.catalog_key or "",
        discovered_models=list(row.discovered_models or []),
        last_verify_ok=row.last_verify_ok,
        last_verified_at=row.last_verified_at,
    )


def set_enabled(session: Session, provider_id: int, enabled: bool) -> AIProviderRow | None:
    row = session.get(AIProviderRow, provider_id)
    if row is None:
        return None
    row.enabled = enabled
    audit.record(session, "ai.provider.toggle", target=f"provider#{provider_id}",
                 actor="user", risk="warn" if enabled else "info",
                 details="enabled" if enabled else "disabled")
    session.flush()
    return row


def set_privacy(session: Session, provider_id: int, mode: PrivacyMode) -> AIProviderRow | None:
    row = session.get(AIProviderRow, provider_id)
    if row is None:
        return None
    row.privacy_mode = mode.value
    audit.record(session, "ai.provider.privacy", target=f"provider#{provider_id}",
                 actor="user", risk="warn", details=mode.value)
    session.flush()
    return row


def remove_provider(session: Session, provider_id: int) -> None:
    row = session.get(AIProviderRow, provider_id)
    if row is None:
        return
    cred_store.delete_credential(_cred_key(row.id))
    session.delete(row)
    audit.record(session, "ai.provider.delete", target=f"provider#{provider_id}",
                 actor="user", risk="warn")


# --------------------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------------------


def test_provider(session: Session, provider_id: int) -> tuple[bool, str]:
    """Send a one-word prompt to prove the endpoint really answers.

    A success here is the *only* thing that sets ``last_verify_ok = True`` and
    is what lets the UI label a model 'live-verified'.
    """
    from pyfix360.db.models import utcnow

    row = session.get(AIProviderRow, provider_id)
    try:
        resp = gateway.ask(session, provider_id, "connectivity_check",
                           "Reply with the single word: ok")
        if row is not None:
            row.last_verify_ok = True
            row.last_verified_at = utcnow()
            session.flush()
        return True, (f"OK — model '{resp.model}' replied in {resp.latency_ms} ms "
                      f"({'remote' if resp.remote else 'local'}), "
                      f"{resp.redactions} redaction(s) applied.")
    except gateway.PrivacyRefused as exc:
        if row is not None:
            row.last_verify_ok = False
            session.flush()
        return False, f"PRIVACY REFUSED: {exc}"
    except gateway.AIGatewayError as exc:
        if row is not None:
            row.last_verify_ok = False
            session.flush()
        return False, f"FAILED: {exc}"


def refresh_models(session: Session, provider_id: int, *,
                   confirmed: bool = False) -> tuple[bool, str, list[str]]:
    """Ask the provider itself which models it serves (metadata call only)."""
    try:
        result = discovery.discover(session, provider_id, confirmed=confirmed)
    except discovery.DiscoveryError as exc:
        return False, str(exc), []
    row = session.get(AIProviderRow, provider_id)
    if not result.ok:
        return False, result.detail, list(row.discovered_models or []) if row else []
    ids = [m.id for m in result.models]
    if row is not None:
        row.discovered_models = ids
        session.flush()
    audit.record(session, "ai.provider.discover", target=f"provider#{provider_id}",
                 actor="user",
                 details=f"{len(ids)} model(s) from {result.endpoint}")
    return True, result.detail, ids


def discovered_annotated(session: Session, provider_id: int) -> list[DiscoveredModelInfo]:
    """Discovered ids with an honest 'free / unknown' annotation."""
    row = session.get(AIProviderRow, provider_id)
    if row is None:
        return []
    preset = catalog.preset(row.catalog_key) if row.catalog_key else None
    known_free = {m.id for m in (preset.models if preset else ()) if m.cost == "free"}
    out = []
    for mid in row.discovered_models or []:
        if mid in known_free:
            annotation = "known free / low cost"
        elif preset and any(m.id == mid for m in preset.models):
            annotation = "in catalogue"
        else:
            annotation = "unknown price — check with the provider"
        out.append(DiscoveredModelInfo(id=mid, annotation=annotation))
    return out


def aliases() -> dict[str, list[str]]:
    return catalog.aliases()


def aggregator_note() -> str:
    return catalog.aggregator_note()


def catalog_reviewed() -> str:
    return catalog.CATALOG_REVIEWED
