"""Operating the AI app-builder platforms, with the project's usual gates.

This is the only layer that may talk to a third-party app builder. It applies
the same rules as the rest of PYFIX 360:

* **Access control.** The capability ``remote_ai`` (Settings → Access Control)
  decides whether an API call may happen at all, and whether the user has to
  confirm it first. Browser automation uses the ``plugin_tools`` capability.
* **Privacy mode.** Every one of these platforms is remote, so a call is
  refused unless the global privacy mode is ``full_remote_approved``.
* **Redaction.** The text is redacted before it leaves the machine, and the
  number of redactions is recorded.
* **Audit trail.** Every call is recorded with the platform, the operation, the
  redaction count and the handle the vendor returned.

The client itself never checks any of this, so a caller that goes around this
module still has to open the door deliberately — but only this module knows
where the door is.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from pyfix360.ai.redaction import redact
from pyfix360.credentials import store as cred_store
from pyfix360.integrations import api_clients
from pyfix360.integrations.api_clients import (
    CallResult,
    PlatformError,
    UnsupportedOperation,
)
from pyfix360.models import PrivacyMode
from pyfix360.services import audit, settings_service
from pyfix360.services.settings_service import AccessDenied

#: Access-control capability that guards a remote API call.
CAPABILITY_API = "remote_ai"
#: Access-control capability that guards browser automation.
CAPABILITY_BROWSER = "plugin_tools"


class PlatformRefused(Exception):
    """Raised when policy — not connectivity — prevents a platform call."""


@dataclass
class Readiness:
    """Whether one platform can be operated right now, and why not if it cannot."""

    key: str
    label: str
    operation_mode: str
    has_api: bool
    has_credential: bool
    browser_available: bool
    ready: bool
    note: str = ""


# --------------------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------------------


def credential_name(platform_key: str) -> str:
    return api_clients.credential_name(platform_key)


def has_credential(platform_key: str) -> bool:
    return bool(cred_store.get_credential(credential_name(platform_key)))


def store_credential(platform_key: str, secret: str) -> str:
    """Save an API key in the OS keyring (never in the database)."""
    info = cred_store.set_credential(credential_name(platform_key), secret)
    return info.backend


def delete_credential(platform_key: str) -> None:
    cred_store.delete_credential(credential_name(platform_key))


# --------------------------------------------------------------------------------------
# readiness
# --------------------------------------------------------------------------------------


def _browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def readiness() -> list[Readiness]:
    """One row per known platform, describing how it can be driven."""
    from pyfix360.services import integration_service

    browser_ok = _browser_available()
    out: list[Readiness] = []
    for platform in integration_service.list_platforms():
        has_api = bool(platform.api) and platform.key in api_clients.CLIENTS
        cred = has_credential(platform.key) if has_api else False
        ready = has_api and cred
        note = ""
        if platform.operation_mode == "browser":
            note = ("no public API — drive the signed-in web session"
                    + ("" if browser_ok else
                       " (install the [automation] extra to enable this)"))
        elif not has_api:
            note = "no API client is implemented in this build"
        elif not cred:
            note = "no API key stored"
        out.append(Readiness(
            key=platform.key, label=platform.label,
            operation_mode=platform.operation_mode,
            has_api=has_api, has_credential=cred,
            browser_available=browser_ok, ready=ready, note=note,
        ))
    return out


# --------------------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------------------


def _require_platform(platform_key: str):
    from pyfix360.services import integration_service

    platform = integration_service.get_platform(platform_key)
    if platform is None:
        raise PlatformRefused(
            f"'{platform_key}' is not a platform PYFIX knows. Add it to the "
            "integrations catalogue first.")
    return platform


def _check_remote_policy(session: Session, capability: str,
                         confirmed: bool) -> None:
    """Apply the access-control and privacy gates for a remote call."""
    settings = settings_service.get_settings(session)
    try:
        settings_service.check_access(session, capability, confirmed=confirmed)
    except AccessDenied as exc:
        raise PlatformRefused(str(exc)) from exc

    if settings.privacy_mode is not PrivacyMode.FULL_REMOTE_APPROVED:
        raise PlatformRefused(
            "Every app-builder platform is a remote service. Change the "
            "privacy mode to 'full_remote_approved' in Settings → Privacy "
            "before sending anything to one."
        )


def preview_prompt(session: Session, platform_key: str, prompt: str) -> str:
    """Show exactly what would be sent, redacted, before it is sent."""
    _require_platform(platform_key)
    redaction = redact(prompt)
    header = (f"[platform: {platform_key}; redactions applied: "
              f"{redaction.count}]\n\n")
    return header + redaction.text


def browser_payload(session: Session, platform_key: str, prompt: str, *,
                    confirmed: bool = False,
                    project_id: int | None = None) -> str:
    """The text the browser path would send, after the gates.

    The browser drives a signed-in web session to a *remote* platform, so it
    gets the same treatment as an API call: the remote capability, the privacy
    mode, the plugin capability, and redaction. Playwright is an optional
    extra and stays out of this module, so the payload is returned rather than
    sent — the caller types it into the page.
    """
    platform = _require_platform(platform_key)
    if platform.operation_mode != "browser":
        raise PlatformRefused(
            f"'{platform_key}' is not driven through the browser — send it "
            "through the API path instead.")
    _check_remote_policy(session, CAPABILITY_API, confirmed)
    try:
        settings_service.check_access(session, CAPABILITY_BROWSER, confirmed=confirmed)
    except AccessDenied as exc:
        raise PlatformRefused(str(exc)) from exc
    redaction = redact(prompt)
    audit.record(session, "platform.prompt.browser", project_id=project_id,
                 target=platform_key,
                 details=f"redactions={redaction.count}", actor="user", risk="warn")
    return redaction.text


# --------------------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------------------


def _client(session: Session, platform_key: str, confirmed: bool):
    _require_platform(platform_key)
    _check_remote_policy(session, CAPABILITY_API, confirmed)
    key = cred_store.get_credential(credential_name(platform_key)) or ""
    if not key:
        raise PlatformRefused(
            f"no API key is stored for '{platform_key}'. Add it on the "
            "Integrations screen first.")
    try:
        return api_clients.client_for(platform_key, key)
    except PlatformError as exc:
        raise PlatformRefused(str(exc)) from exc


def submit_prompt(session: Session, platform_key: str, prompt: str, *,
                  confirmed: bool = False, project_id: int | None = None,
                  system: str = "") -> CallResult:
    """Send a build prompt to a platform and return the handle it produced."""
    if not prompt.strip():
        raise PlatformRefused("the prompt is empty.")
    client = _client(session, platform_key, confirmed)

    redaction = redact(prompt)
    payload = redaction.text

    try:
        result = client.submit_prompt(payload, system=system)
    except UnsupportedOperation as exc:
        raise PlatformRefused(str(exc)) from exc
    except PlatformError as exc:
        raise PlatformRefused(str(exc)) from exc

    audit.record(session, "platform.prompt.submit", project_id=project_id,
                 target=platform_key,
                 details=f"redactions={redaction.count} handle={result.handle}",
                 actor="user", risk="warn")
    return result


def send_followup(session: Session, platform_key: str, handle: str,
                  message: str, *, confirmed: bool = False,
                  project_id: int | None = None) -> CallResult:
    """Answer a question the platform asked, or continue an existing job."""
    if not message.strip():
        raise PlatformRefused("the message is empty.")
    client = _client(session, platform_key, confirmed)
    redaction = redact(message)
    try:
        result = client.send_followup(handle, redaction.text)
    except UnsupportedOperation as exc:
        raise PlatformRefused(str(exc)) from exc
    except PlatformError as exc:
        raise PlatformRefused(str(exc)) from exc

    audit.record(session, "platform.prompt.followup", project_id=project_id,
                 target=f"{platform_key}:{handle}",
                 details=f"redactions={redaction.count}", actor="user",
                 risk="warn")
    return result


def status(session: Session, platform_key: str, handle: str, *,
           confirmed: bool = False, project_id: int | None = None) -> CallResult:
    """Read the current state of work that is already running."""
    client = _client(session, platform_key, confirmed)
    try:
        result = client.status(handle)
    except UnsupportedOperation as exc:
        raise PlatformRefused(str(exc)) from exc
    except PlatformError as exc:
        raise PlatformRefused(str(exc)) from exc

    audit.record(session, "platform.status", project_id=project_id,
                 target=f"{platform_key}:{handle}",
                 details=f"status={result.status}", actor="user")
    return result
