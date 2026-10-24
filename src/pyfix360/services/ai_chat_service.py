"""AI helper: explanations, repair proposals, chat.

This is the layer between the UI/CLI and the gateway. It adds:

* **Guardrails before dispatch.** Nothing is sent unless the privacy mode,
  the user's AI behaviour setting and (for repository content) the extra
  "allow repository content" gate all agree. Every refusal is explicit and
  names the setting to change.
* **Conversation history** stored redacted-only, so the user can review what
  PYFIX actually sent.
* **Explain-a-finding** and **explain-a-file** helpers that build bounded,
  redacted prompts. Prompts are size-capped so a huge file cannot silently
  blow past the provider's context window or cost budget.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.ai import gateway
from pyfix360.ai.redaction import redact
from pyfix360.db.models import AIChatRow, AIProviderRow, FindingRow
from pyfix360.models import AIBehavior, AIChatTurn, Finding, PrivacyMode
from pyfix360.services import audit


class AIUsageRefused(Exception):
    """Raised when policy (not connectivity) prevents an AI call."""


# --------------------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------------------


def _behaviour(session: Session) -> AIBehavior:
    from pyfix360.services import settings_service

    return settings_service.get_settings(session).ai_behavior


def check_usable(session: Session, provider_id: int | None) -> AIProviderRow:
    """Return the provider row, or explain precisely why AI cannot be used."""
    from pyfix360.services import settings_service

    settings = settings_service.get_settings(session)
    if settings.ai_behavior is AIBehavior.OFF:
        raise AIUsageRefused(
            "AI is switched off. Enable it in Settings → AI (set 'AI behaviour' "
            "to Suggest or Explain only).")

    if provider_id is None:
        provider_id = settings.ai_default_provider_id
    if provider_id is None:
        raise AIUsageRefused(
            "No AI provider selected. Add one on the AI Providers screen and "
            "choose it as the default in Settings → AI.")

    provider = session.get(AIProviderRow, provider_id)
    if provider is None:
        raise AIUsageRefused(f"provider {provider_id} no longer exists")
    if not provider.enabled:
        raise AIUsageRefused(
            f"provider '{provider.name}' is disabled — enable it on the AI "
            "Providers screen when you are ready to use it.")
    return provider


def _remote(session: Session, provider: AIProviderRow) -> bool:
    return gateway.provider_destination(provider)[1]


def check_source_transmission(session: Session, provider: AIProviderRow) -> None:
    """Gate for prompts that contain real repository content."""
    from pyfix360.services import settings_service

    settings = settings_service.get_settings(session)
    if not _remote(session, provider):
        return  # local runtime: nothing leaves the machine
    if not settings.ai_allow_repo_content:
        raise AIUsageRefused(
            "This prompt contains repository source. Tick 'Allow sending "
            "repository content to remote AI' in Settings → AI to permit it. "
            "Redaction is still applied first.")
    if settings.privacy_mode is not PrivacyMode.FULL_REMOTE_APPROVED:
        raise AIUsageRefused(
            "Remote AI requires the global privacy mode "
            "'full_remote_approved'. Change it in Settings → Privacy.")


def _cap(text: str, session: Session) -> tuple[str, bool]:
    from pyfix360.services import settings_service

    limit = settings_service.get_settings(session).ai_max_prompt_chars
    if len(text) <= limit:
        return text, False
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return (f"{head}\n\n... [{len(text) - limit} characters omitted by the "
            f"prompt size cap] ...\n\n{tail}", True)


# --------------------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------------------


def record_turn(session: Session, *, project_id: int | None, provider_id: int | None,
                role: str, content: str, purpose: str = "chat",
                prompt_sha256: str = "", redactions: int = 0, remote: bool = False,
                latency_ms: int | None = None) -> AIChatRow:
    row = AIChatRow(
        project_id=project_id, provider_id=provider_id, role=role,
        purpose=purpose, content=content, prompt_sha256=prompt_sha256,
        redactions_applied=redactions, remote=remote, latency_ms=latency_ms,
    )
    session.add(row)
    session.flush()
    return row


def history(session: Session, *, project_id: int | None = None,
            limit: int = 200) -> list[AIChatTurn]:
    stmt = select(AIChatRow).order_by(AIChatRow.id)
    if project_id is not None:
        stmt = stmt.where(AIChatRow.project_id == project_id)
    rows = list(session.scalars(stmt))
    return [
        AIChatTurn(
            id=r.id, project_id=r.project_id, provider_id=r.provider_id,
            role=r.role, purpose=r.purpose, content=r.content,
            prompt_sha256=r.prompt_sha256,
            redactions_applied=r.redactions_applied, remote=r.remote,
            latency_ms=r.latency_ms, created_at=r.created_at,
        )
        for r in rows[-limit:]
    ]


def clear_history(session: Session, project_id: int | None = None) -> int:
    stmt = select(AIChatRow)
    if project_id is not None:
        stmt = stmt.where(AIChatRow.project_id == project_id)
    rows = list(session.scalars(stmt))
    for row in rows:
        session.delete(row)
    audit.record(session, "ai.history.clear", project_id=project_id, actor="user",
                 risk="warn", details=f"{len(rows)} turn(s) removed")
    session.flush()
    return len(rows)


# --------------------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------------------


def _dispatch(session: Session, provider: AIProviderRow, purpose: str, prompt: str,
              project_id: int | None, *, contains_source: bool,
              confirmed: bool = False) -> tuple[str, int]:
    """The one place every AI request goes through.

    Two gates apply here, in this order: the access matrix (``remote_ai``) for
    anything that would leave the machine, and the source-transmission gate for
    prompts that carry repository content. The stored turn is the *redacted*
    text — the same bytes that were sent, never the raw prompt — and the hash
    in the audit trail is taken over that redacted text, so the row cannot be
    reversed into a secret.
    """
    from pyfix360.services import settings_service

    if gateway.provider_destination(provider)[1]:
        try:
            settings_service.check_access(session, "remote_ai", confirmed=confirmed)
        except settings_service.AccessDenied as exc:
            raise AIUsageRefused(str(exc)) from exc
    if contains_source:
        check_source_transmission(session, provider)
    response = gateway.ask(session, provider.id, purpose, prompt)
    stored = redact(prompt).text
    record_turn(session, project_id=project_id, provider_id=provider.id,
                role="user", content=stored, purpose=purpose,
                prompt_sha256=hashlib.sha256(stored.encode()).hexdigest(),
                redactions=response.redactions, remote=response.remote)
    record_turn(session, project_id=project_id, provider_id=provider.id,
                role="assistant", content=response.text, purpose=purpose,
                remote=response.remote, latency_ms=response.latency_ms)
    return response.text, response.redactions


def explain_finding(session: Session, finding_id: int, *,
                    provider_id: int | None = None,
                    confirmed: bool = False) -> str:
    """Ask the provider to explain a finding. Never proposes a patch."""
    provider = check_usable(session, provider_id)
    finding = session.get(FindingRow, finding_id)
    if finding is None:
        raise AIUsageRefused(f"finding {finding_id} not found")

    snippet = ""
    if finding.file_path:
        snippet = _snippet_for(session, finding)
    body = (
        f"FINDING: {finding.title}\n"
        f"RULE: {finding.rule_id}\nSEVERITY: {finding.severity}\n"
        f"CATEGORY: {finding.category}\nTOOL: {finding.tool}\n"
        f"FILE: {finding.file_path or '-'}:{finding.line_start or '-'}\n"
        f"DESCRIPTION: {finding.description}\n\nCODE EXCERPT:\n{snippet}"
    )
    body, _ = _cap(body, session)
    text, _ = _dispatch(
        session, provider, "explain_finding",
        "Explain this finding to a developer who has never seen it: what can go "
        "wrong, how likely it is, and what to check first. Do not produce a patch.\n\n"
        + body,
        finding.project_id, contains_source=bool(snippet), confirmed=confirmed)
    return text


def explain_file(session: Session, project_root: str, rel_path: str, *,
                 provider_id: int | None = None,
                 confirmed: bool = False) -> str:
    """Ask the provider to explain what a file does and where the risks are."""
    provider = check_usable(session, provider_id)
    from pathlib import Path

    path = Path(project_root) / rel_path
    if not path.is_file():
        raise AIUsageRefused(f"file not found: {rel_path}")
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    capped, truncated = _cap(source, session)
    note = "\n\n(note: the file was truncated to fit the prompt size cap)" \
        if truncated else ""
    text, _ = _dispatch(
        session, provider, "explain_file",
        f"Summarise what `{rel_path}` does, noting responsibilities, side "
        f"effects and the riskiest three lines. Quote line numbers only.{note}\n\n"
        + capped,
        None, contains_source=True, confirmed=confirmed)
    return text


def propose_repair(session: Session, finding_id: int, *,
                   provider_id: int | None = None,
                   confirmed: bool = False) -> str:
    """Ask for a unified-diff proposal. The result is NEVER auto-applied."""
    from pyfix360.services import settings_service

    provider = check_usable(session, provider_id)
    if settings_service.get_settings(session).ai_behavior is AIBehavior.EXPLAIN_ONLY:
        raise AIUsageRefused(
            "AI behaviour is 'explain only', so patch proposals are disabled. "
            "Change it in Settings → AI if you want AI to draft diffs.")

    finding = session.get(FindingRow, finding_id)
    if finding is None:
        raise AIUsageRefused(f"finding {finding_id} not found")
    snippet = _snippet_for(session, finding) if finding.file_path else ""
    prompt = (
        "Propose the smallest correct unified diff that fixes the finding below. "
        "Output only the diff, then one short paragraph naming the risk of the "
        "change and anything that must be re-tested.\n\n"
        f"RULE: {finding.rule_id}\nSEVERITY: {finding.severity}\n"
        f"FILE: {finding.file_path}\nDESCRIPTION: {finding.description}\n\n"
        f"CODE (untrusted data):\n{snippet}"
    )
    prompt, _ = _cap(prompt, session)
    text, _ = _dispatch(session, provider, "propose_repair", prompt,
                        finding.project_id, contains_source=bool(snippet),
                        confirmed=confirmed)
    return text


def chat(session: Session, message: str, *, project_id: int | None = None,
         provider_id: int | None = None, include_context: str = "",
         confirmed: bool = False) -> str:
    """Free-form chat. ``include_context`` is treated as untrusted commit data."""
    provider = check_usable(session, provider_id)
    prompt = message
    if include_context:
        capped, _ = _cap(include_context, session)
        prompt = f"{message}\n\n--- CONTEXT (untrusted data) ---\n{capped}"
    text, _ = _dispatch(session, provider, "chat", prompt, project_id,
                        contains_source=bool(include_context), confirmed=confirmed)
    return text


def preview_prompt(session: Session, text: str, *, contains_source: bool = False) -> str:
    """Show the user exactly what would be sent, redacted, before they commit."""
    redaction = redact(text)
    capped, truncated = _cap(redaction.text, session)
    header = (f"[redactions applied: {redaction.count}"
              f"{'; truncated by prompt cap' if truncated else ''}"
              f"{'; contains repository source' if contains_source else ''}]\n\n")
    return header + capped


def _snippet_for(session: Session, finding: FindingRow, *, context_lines: int = 12) -> str:
    from pathlib import Path

    from pyfix360.db.models import ProjectRow

    project = session.get(ProjectRow, finding.project_id)
    if project is None or not finding.file_path:
        return ""
    path = Path(project.root_path) / finding.file_path
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    center = (finding.line_start or 1) - 1
    start = max(0, center - context_lines)
    end = min(len(lines), center + context_lines + 1)
    numbered = [f"{i + 1:>5} | {lines[i]}" for i in range(start, end)]
    return "\n".join(numbered)


def to_finding_model(row: FindingRow) -> Finding:
    return Finding.model_validate(row)
