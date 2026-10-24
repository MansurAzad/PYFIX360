"""Live model discovery.

The catalogue in :mod:`pyfix360.ai.catalog` is a hand-curated starting point.
Providers rename and retire models constantly, so a static list is always a
little stale. This module lets the user ask a provider *directly*, with their
own key, what it currently offers.

Two hard rules:

1. Discovery is **opt-in and explicit**. Nothing here runs on startup, on a
   timer, or in the background. The user presses a button.
2. Discovery only ever calls ``GET {base_url}/models`` — the documented
   OpenAI-compatible metadata route. No source code, no prompts and no
   repository content is ever sent. Only the credential travels, and only to
   the endpoint the user configured.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from pyfix360.credentials import store as cred_store
from pyfix360.db.models import AIProviderRow


@dataclass
class DiscoveredModel:
    id: str
    owned_by: str = ""
    context_k: int = 0


@dataclass
class DiscoveryResult:
    ok: bool
    models: list[DiscoveredModel] = field(default_factory=list)
    detail: str = ""
    latency_ms: int = 0
    endpoint: str = ""


class DiscoveryError(Exception):
    pass


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def _check_policy(session: Session, provider: AIProviderRow, confirmed: bool) -> None:
    """Discovery is a remote call, so it needs the same consent as any other.

    No source code is sent — only a metadata request and the credential — but
    the credential still travels to a remote endpoint. A disabled provider, a
    provider whose own privacy mode forbids remote requests, a global privacy
    mode that is not ``full_remote_approved``, and the access matrix all say
    the same thing: the user has not agreed to this.
    """
    from pyfix360.ai import gateway
    from pyfix360.models import PrivacyMode
    from pyfix360.services import settings_service

    try:
        gateway.check_permission(provider)
    except gateway.PrivacyRefused as exc:
        raise DiscoveryError(str(exc)) from exc

    _, is_remote = gateway.provider_destination(provider)
    if not is_remote:
        return
    settings = settings_service.get_settings(session)
    if settings.privacy_mode is not PrivacyMode.FULL_REMOTE_APPROVED:
        raise DiscoveryError(
            "Discovery contacts a remote endpoint. Change the privacy mode to "
            "'full_remote_approved' in Settings → Privacy first.")
    try:
        settings_service.check_access(session, "remote_ai", confirmed=confirmed)
    except settings_service.AccessDenied as exc:
        raise DiscoveryError(str(exc)) from exc


def discover(session: Session, provider_id: int, *, timeout_s: int = 20,
             confirmed: bool = False) -> DiscoveryResult:
    """Ask a provider what models it currently serves.

    This is a metadata-only call. It never transmits source code, but it does
    contact the provider, so it obeys the same access and privacy rules as any
    other outgoing call.
    """
    provider = session.get(AIProviderRow, provider_id)
    if provider is None:
        raise DiscoveryError(f"provider {provider_id} not found")
    if not provider.base_url:
        raise DiscoveryError(f"provider '{provider.name}' has no endpoint configured")
    _check_policy(session, provider, confirmed)

    url = _models_url(provider.base_url)
    api_key = cred_store.get_credential(f"provider-{provider.id}") or ""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="GET",
    )

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return DiscoveryResult(
            False, detail=f"HTTP {exc.code}: {detail}", endpoint=url,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return DiscoveryResult(
            False, detail=f"{type(exc).__name__}: {exc}", endpoint=url,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
    latency = int((time.monotonic() - start) * 1000)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return DiscoveryResult(False, detail=f"not JSON: {exc}", endpoint=url,
                               latency_ms=latency)

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return DiscoveryResult(
            False, endpoint=url, latency_ms=latency,
            detail="endpoint did not return an OpenAI-style {'data': [...]} list",
        )

    models: list[DiscoveredModel] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        mid = item.get("id") or item.get("name")
        if not mid:
            continue
        models.append(DiscoveredModel(
            id=str(mid),
            owned_by=str(item.get("owned_by") or item.get("provider") or ""),
            context_k=_guess_context_k(item),
        ))
    models.sort(key=lambda m: m.id)
    return DiscoveryResult(True, models=models, endpoint=url, latency_ms=latency,
                           detail=f"{len(models)} model(s) reported")


def _guess_context_k(item: dict) -> int:
    for key in ("context_length", "context_window", "max_context_length",
                "max_model_len"):
        value = item.get(key)
        if isinstance(value, int) and value > 0:
            return value // 1000
    for key in ("top_provider", "meta"):
        sub = item.get(key)
        if isinstance(sub, dict):
            guess = _guess_context_k(sub)
            if guess:
                return guess
    return 0


def merge_with_catalog(discovered: list[DiscoveredModel],
                       known_free_ids: set[str]) -> list[tuple[str, str]]:
    """Annotate discovered ids with a 'free' marker where we have prior knowledge.

    Returns ``(label, annotation)`` pairs ready for a picker. We deliberately
    do not invent pricing: if we do not know, the annotation says so.
    """
    out = []
    for m in discovered:
        note = ("known free / low cost" if m.id in known_free_ids
                else m.owned_by or "available")
        out.append((m.id, note))
    return out
