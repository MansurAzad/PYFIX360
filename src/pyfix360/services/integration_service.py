"""Service layer for integration knowledge + project detection.

Two kinds of knowledge live here:

* **tool knowledge** — CLIs and services (git, GitHub, Vercel, Docker, ...).
  Static: what the tool is, which commands exist, how it shows up in a
  project.
* **platform playbooks** — AI app builders and prompt workbenches (Lovable,
  Trickle, Replit, Emergent, v0, Builder.io, Google AI Studio). These are
  driven by natural language, so a playbook carries the prompt patterns, the
  command dialect, the questions the platform asks and the situations that
  come up mid-build.

Nothing here performs network I/O. The playbook is knowledge; *driving* a
platform lives in :mod:`pyfix360.integrations.api_clients` (REST) and
:mod:`pyfix360.integrations.browser` (web session), both of which require an
explicit user action and their own credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyfix360.integrations.catalog import (
    ALL,
    PLATFORMS,
    Integration,
    by_key,
    marker_names,
)


@dataclass
class DetectionResult:
    key: str
    label: str
    category: str
    detected: bool
    markers_found: list[str]


@dataclass
class Playbook:
    """A platform's operating knowledge, ready for display."""

    key: str
    label: str
    summary: str
    homepage: str
    docs_url: str
    operation_mode: str          # "api" | "browser" | "both" | ""
    api: str = ""
    api_key_header: str = ""
    api_key_url: str = ""
    api_capabilities: tuple[str, ...] = ()
    deep_link: str = ""
    cli: str = ""
    install: str = ""
    auth: str = ""
    prompt_patterns: tuple[str, ...] = ()
    command_dialect: tuple[str, ...] = ()
    qa_flow: tuple[str, ...] = ()
    situations: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()
    verified_live: bool = False
    reviewed_on: str = ""
    warnings: list[str] = field(default_factory=list)


def list_integrations(category: str | None = None) -> list[Integration]:
    if category is None:
        return list(ALL)
    return [it for it in ALL if it.category == category]


def get_integration(key: str) -> Integration | None:
    return by_key(key)


def detect_for_project(project_root: str) -> list[DetectionResult]:
    """Static detection of which integrations a project uses."""
    from pathlib import Path

    root = Path(project_root)
    names = marker_names(project_root)
    results: list[DetectionResult] = []
    for it in ALL:
        markers_found: list[str] = []
        for marker in it.detection:
            if "*" in marker:
                if any(root.glob(marker)):
                    markers_found.append(marker)
            elif marker in names or (root / marker).exists():
                markers_found.append(marker)
        results.append(DetectionResult(
            key=it.key, label=it.label, category=it.category,
            detected=bool(markers_found), markers_found=markers_found,
        ))
    return results


def search(query: str) -> list[Integration]:
    q = query.strip().lower()
    if not q:
        return list(ALL)
    out = []
    for it in ALL:
        haystack = " ".join([
            it.key, it.label, it.category, it.summary, it.cli, it.auth,
            it.api, it.deep_link, it.operation_mode,
            *(c.cmd for c in it.commands),
            *(c.purpose for c in it.commands),
            *(p.title for p in it.prompt_patterns),
            *(p.prompt for p in it.prompt_patterns),
            *(d for d in it.command_dialect),
            *(q_.question for q_ in it.qa_flow),
            *(q_.answer for q_ in it.qa_flow),
            *(s.situation for s in it.situations),
            *(s.response for s in it.situations),
            *(it.pitfalls),
            *(it.api_capabilities),
        ]).lower()
        if q in haystack:
            out.append(it)
    return out


# ======================================================================================
# platform playbooks
# ======================================================================================


def list_platforms() -> list[Integration]:
    """The AI app builders and prompt workbenches PYFIX knows how to drive."""
    return list(PLATFORMS)


def get_platform(key: str) -> Integration | None:
    for it in PLATFORMS:
        if it.key == key:
            return it
    return None


def platform_categories() -> list[str]:
    return sorted({it.category for it in PLATFORMS})


def _verify_warning(it: Integration) -> list[str]:
    warnings: list[str] = []
    if not it.verified_live:
        warnings.append(
            "PYFIX has never driven this platform from inside this build. "
            "The playbook was written from the vendor's own documentation on "
            f"{it.reviewed_on or 'an unrecorded date'} — verify it before "
            "you rely on it."
        )
    if it.operation_mode == "browser":
        warnings.append(
            "No public API is published for this platform. Operating it "
            "means driving a signed-in web session, which breaks whenever "
            "the user interface changes."
        )
    if it.operation_mode == "both":
        warnings.append(
            "The API and the web UI expose different capabilities. The API "
            "covers what is documented; anything else needs the browser."
        )
    if not it.api_capabilities and it.operation_mode:
        warnings.append(
            "No API capability is claimed for this platform in this build."
        )
    return warnings


def build_playbook(it: Integration) -> Playbook:
    """Turn a catalogue entry into the flat, display-ready playbook."""
    return Playbook(
        key=it.key,
        label=it.label,
        summary=it.summary,
        homepage=it.homepage,
        docs_url=it.docs_url,
        operation_mode=it.operation_mode,
        api=it.api,
        api_key_header=it.api_key_header,
        api_key_url=it.api_key_url,
        api_capabilities=it.api_capabilities,
        deep_link=it.deep_link,
        cli=it.cli,
        install=it.install,
        auth=it.auth,
        prompt_patterns=tuple(
            f"{p.title}\n    {p.prompt}"
            + (f"\n    ({p.notes})" if p.notes else "")
            for p in it.prompt_patterns
        ),
        command_dialect=it.command_dialect,
        qa_flow=tuple(
            f"{q_.question}\n    -> {q_.answer}"
            + (f"\n    ({q_.notes})" if q_.notes else "")
            for q_ in it.qa_flow
        ),
        situations=tuple(
            f"[{s.severity}] {s.situation}\n    -> {s.response}"
            for s in it.situations
        ),
        pitfalls=it.pitfalls,
        verified_live=it.verified_live,
        reviewed_on=it.reviewed_on,
        warnings=_verify_warning(it),
    )


def platform_playbook(key: str) -> Playbook | None:
    it = get_platform(key)
    return build_playbook(it) if it is not None else None


def playbook_catalog() -> list[Playbook]:
    return [build_playbook(it) for it in PLATFORMS]


def operation_modes() -> dict[str, list[Integration]]:
    """Platforms grouped by how they can be driven."""
    grouped: dict[str, list[Integration]] = {}
    for it in PLATFORMS:
        grouped.setdefault(it.operation_mode or "unknown", []).append(it)
    return grouped
