"""Application settings persistence and access-control enforcement.

Settings live as key/value rows in SQLite so a single table carries every
option. Complex values (lists, dicts) are stored as JSON strings. Reading is
tolerant: an unrecognised or corrupt value falls back to the documented
default rather than crashing the UI.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from sqlalchemy.orm import Session

from pyfix360.config import get_config
from pyfix360.db.models import SettingRow
from pyfix360.models import (
    AccessLevel,
    AIBehavior,
    AppSettings,
    Density,
    PrivacyMode,
    Severity,
    ThemeKey,
)
from pyfix360.services import audit


class AccessDenied(Exception):
    """Raised when an action is refused by the current access policy."""


#: default value for every setting, keyed exactly as stored.
DEFAULTS: dict[str, str] = {
    # execution & sandbox
    "isolate_execution": "true",
    "execution_timeout_s": "300",
    "memory_limit_mb": "2048",
    "allow_network_in_sandbox": "false",
    "max_repair_retries": "2",
    "auto_commit_verified": "false",
    # scanning
    "scan_include_globs": "[]",
    "scan_exclude_globs": "[]",
    "scan_skip_generated": "true",
    "scan_max_file_kb": "2048",
    "scan_max_files": "20000",
    "scan_parallel_workers": "0",
    "scan_incremental": "true",
    "enabled_analyzers": '["builtin", "ruff", "bandit", "pip-audit"]',
    # findings & display
    "min_severity": "info",
    "auto_acknowledge_info": "false",
    "group_by_root_cause": "true",
    "show_false_positive_rules": "false",
    # AI
    "redact_before_ai": "true",
    "privacy_mode": "local_only",
    "ai_behavior": "suggest",
    "ai_default_provider_id": "",
    "ai_auto_explain_findings": "false",
    "ai_max_prompt_chars": "24000",
    "ai_allow_repo_content": "false",
    # repair & safety
    "require_snapshot_before_write": "true",
    "require_approval_destructive": "true",
    "require_approval_security": "true",
    "require_approval_dependency": "true",
    "require_approval_migration": "true",
    "require_approval_production": "true",
    "block_write_outside_project": "true",
    "backup_retention_days": "30",
    "dry_run_by_default": "true",
    # access control
    "access_apply_to_original": "ask",
    "access_rollback": "full",
    "access_delete_project": "ask",
    "access_purge_history": "blocked",
    "access_run_tests": "full",
    "access_edit_quality_gates": "full",
    "access_remote_ai": "ask",
    "access_export_reports": "full",
    "access_plugin_tools": "ask",
    "access_autopilot": "ask",
    "access_danger_zone": "blocked",
    "access_check_for_updates": "ask",
    # autopilot
    "autopilot_gesture_window_ms": "500",
    # background / headless
    "tray_enabled": "true",
    "close_hides_to_tray": "true",
    "service_poll_ms": "2000",
    # custom commands
    "test_command_override": "",
    "custom_tool_paths": "{}",
    # appearance
    "theme": "dark",
    "accent_override": "",
    "font_size": "13",
    "font_family": "",
    "density": "comfortable",
    "corner_radius": "6",
    "motion_enabled": "true",
    "show_status_bar": "true",
    "sidebar_width": "210",
    "start_screen": "Overview",
    # notifications & logs
    "notify_on_scan_complete": "true",
    "notify_on_repair_verified": "true",
    "log_level": "INFO",
    "log_retention_days": "14",
    # updates
    "update_auto_install": "false",
}

#: Settings that exist for documentation value but cannot be switched off.
#: Redaction before AI is a hard guarantee of the privacy model, not a
#: preference: the gateway redacts unconditionally, so a stored ``false``
#: would be a lie. The value is pinned to ``true`` and a write attempt to
#: disable it is refused.
LOCKED_KEYS: dict[str, str] = {
    "redact_before_ai": "true",
}

_BOOL_KEYS = {k for k, v in DEFAULTS.items() if v in ("true", "false")}
_INT_KEYS = {
    "execution_timeout_s", "memory_limit_mb", "max_repair_retries",
    "scan_max_file_kb", "scan_max_files", "scan_parallel_workers",
    "ai_max_prompt_chars", "backup_retention_days", "font_size",
    "corner_radius", "sidebar_width", "log_retention_days",
    "autopilot_gesture_window_ms", "service_poll_ms",
}
_JSON_LIST_KEYS = {"scan_include_globs", "scan_exclude_globs", "enabled_analyzers"}
_JSON_DICT_KEYS = {"custom_tool_paths"}
_ENUM_KEYS = {
    "min_severity": Severity,
    "privacy_mode": PrivacyMode,
    "theme": ThemeKey,
    "density": Density,
    "ai_behavior": AIBehavior,
    "access_apply_to_original": AccessLevel,
    "access_rollback": AccessLevel,
    "access_delete_project": AccessLevel,
    "access_purge_history": AccessLevel,
    "access_run_tests": AccessLevel,
    "access_edit_quality_gates": AccessLevel,
    "access_remote_ai": AccessLevel,
    "access_export_reports": AccessLevel,
    "access_plugin_tools": AccessLevel,
    "access_autopilot": AccessLevel,
    "access_danger_zone": AccessLevel,
    "access_check_for_updates": AccessLevel,
}


# --------------------------------------------------------------------------------------
# raw values
# --------------------------------------------------------------------------------------


def raw_values(session: Session) -> dict[str, str]:
    stored = {r.key: r.value for r in session.query(SettingRow).all()}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def _decode(key: str, raw: str) -> Any:
    if key in LOCKED_KEYS:
        return LOCKED_KEYS[key] == "true"
    if key in _BOOL_KEYS:
        return raw == "true"
    if key in _INT_KEYS:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(DEFAULTS[key])
    if key in _JSON_LIST_KEYS:
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []
    if key in _JSON_DICT_KEYS:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    if key in _ENUM_KEYS:
        try:
            return _ENUM_KEYS[key](raw)
        except ValueError:
            return _ENUM_KEYS[key](DEFAULTS[key])
    return raw


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if isinstance(value, AccessLevel | Severity | PrivacyMode | ThemeKey
                  | Density | AIBehavior):
        return value.value
    if value is None:
        return ""
    return str(value)


def coerce(key: str, raw: str) -> Any:
    """Parse a raw string into the declared type of ``key``.

    The declared type comes from the same table the reader uses, so the CLI
    and the interface cannot drift apart. Lists and dicts are given as JSON;
    a value that does not fit the declared type raises ``ValueError`` with a
    message that names what was expected.
    """
    if key == "ai_default_provider_id":
        text = raw.strip()
        if text == "":
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(
                f"'{key}' expects a provider id or an empty value, not {raw!r}"
            ) from exc
    if key in LOCKED_KEYS:
        lowered = raw.strip().lower()
        if lowered in ("0", "false", "no", "off"):
            raise ValueError(
                f"'{key}' is locked on: redaction before AI is a hard guarantee "
                "of the privacy model and cannot be switched off."
            )
        if lowered in ("1", "true", "yes", "on"):
            return True
        raise ValueError(f"'{key}' expects true or false, not {raw!r}")
    if key in _BOOL_KEYS:
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"'{key}' expects true or false, not {raw!r}")
    if key in _INT_KEYS:
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ValueError(
                f"'{key}' expects a whole number, not {raw!r}") from exc
    if key in _JSON_LIST_KEYS:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'{key}' expects a JSON list, for example '[\"*.py\"]'") from exc
        if not isinstance(value, list):
            raise ValueError(f"'{key}' expects a JSON list, not {raw!r}")
        return value
    if key in _JSON_DICT_KEYS:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'{key}' expects a JSON object, for example '{{\"ruff\": \"ruff\"}}'"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"'{key}' expects a JSON object, not {raw!r}")
        return value
    if key in _ENUM_KEYS:
        try:
            return _ENUM_KEYS[key](raw.strip())
        except ValueError as exc:
            allowed = ", ".join(v.value for v in _ENUM_KEYS[key])
            raise ValueError(f"'{key}' expects one of: {allowed}") from exc
    return raw


def get_settings(session: Session) -> AppSettings:
    values = raw_values(session)
    decoded = {k: _decode(k, v) for k, v in values.items()}
    default_id = decoded.pop("ai_default_provider_id", "")
    try:
        provider_id = int(default_id) if default_id != "" else None
    except (TypeError, ValueError):
        provider_id = None
    return AppSettings(
        data_dir=str(get_config().data_dir),
        ai_default_provider_id=provider_id,
        **decoded,
    )


def save_settings(session: Session, settings: AppSettings, *,
                  actor: str = "user", keys: list[str] | None = None) -> AppSettings:
    """Persist settings. ``keys`` limits the write to specific fields."""
    values = {k: _encode(getattr(settings, k)) for k in DEFAULTS}
    changed: dict[str, str] = {}
    for key, value in values.items():
        if keys is not None and key not in keys:
            continue
        if key in LOCKED_KEYS:
            value = LOCKED_KEYS[key]
        row = session.get(SettingRow, key)
        if row is None:
            session.add(SettingRow(key=key, value=value))
            changed[key] = value
        elif row.value != value:
            row.value = value
            changed[key] = value
    if changed:
        audit.record(session, "settings.save", details=json.dumps(sorted(changed)),
                     actor=actor, risk="warn")
    session.flush()
    apply_to_config(settings)
    return settings


def reset_settings(session: Session, *, actor: str = "user") -> AppSettings:
    for key in DEFAULTS:
        row = session.get(SettingRow, key)
        if row is not None:
            session.delete(row)
    audit.record(session, "settings.reset", actor=actor, risk="warn",
                 details=f"{len(DEFAULTS)} keys")
    session.flush()
    return get_settings(session)


def export_settings(session: Session) -> str:
    """Serialise settings to JSON for backup/transfer (never includes secrets)."""
    return json.dumps(raw_values(session), indent=2, sort_keys=True)


def import_settings(session: Session, payload: str, *, actor: str = "user") -> AppSettings:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("settings payload must be a JSON object")
    for key, value in data.items():
        if key not in DEFAULTS:
            continue    # ignore unknown keys rather than corrupting the store
        if key in LOCKED_KEYS:
            value = LOCKED_KEYS[key]
        row = session.get(SettingRow, key)
        text = _encode(value) if not isinstance(value, str) else value
        if row is None:
            session.add(SettingRow(key=key, value=text))
        else:
            row.value = text
    audit.record(session, "settings.import", actor=actor, risk="warn",
                 details=f"{len(data)} keys offered")
    session.flush()
    return get_settings(session)


# --------------------------------------------------------------------------------------
# access control
# --------------------------------------------------------------------------------------


def access_level(session: Session, capability: str) -> AccessLevel:
    values = raw_values(session)
    raw = values.get(f"access_{capability}", AccessLevel.ASK.value)
    try:
        return AccessLevel(raw)
    except ValueError:
        return AccessLevel.ASK


def check_access(session: Session, capability: str, *, confirmed: bool = False) -> None:
    """Gate an action. Raises :class:`AccessDenied` when it must not proceed.

    ``ASK`` means "allowed only with confirmation" — callers pass
    ``confirmed=True`` once the user has agreed.
    """
    level = access_level(session, capability)
    if level is AccessLevel.BLOCKED:
        raise AccessDenied(
            f"'{capability}' is set to BLOCKED in Settings → Access Control.")
    if level is AccessLevel.ASK and not confirmed:
        raise AccessDenied(
            f"'{capability}' requires confirmation (Settings → Access Control).")


def apply_to_config(settings: AppSettings) -> None:
    """Mirror persisted settings into the runtime AppConfig."""
    cfg = get_config()
    cfg.execution_timeout_s = settings.execution_timeout_s
    cfg.memory_limit_mb = settings.memory_limit_mb
    cfg.isolate_execution = settings.isolate_execution
    cfg.allow_network_in_sandbox = settings.allow_network_in_sandbox
    cfg.auto_commit_verified = settings.auto_commit_verified
    cfg.max_repair_retries = settings.max_repair_retries
    cfg.redact_before_ai = settings.redact_before_ai
    cfg.theme = settings.theme.value
    cfg.accent_override = settings.accent_override
    cfg.font_size = settings.font_size
    cfg.font_family = settings.font_family
    cfg.density = settings.density.value
    cfg.corner_radius = settings.corner_radius
    cfg.motion_enabled = settings.motion_enabled
    cfg.sidebar_width = settings.sidebar_width
    cfg.log_level = settings.log_level
    cfg.tray_enabled = settings.tray_enabled
    cfg.close_hides_to_tray = settings.close_hides_to_tray
    cfg.service_poll_ms = settings.service_poll_ms
    # Startup screen is read straight from the config by the main window, so it
    # has to be mirrored here too — otherwise the setting is silently ignored.
    if hasattr(cfg, "start_screen"):
        cfg.start_screen = settings.start_screen
    if hasattr(cfg, "show_status_bar"):
        cfg.show_status_bar = settings.show_status_bar


def apply_from_config(session: Session) -> None:
    """Load persisted settings into the runtime AppConfig at startup."""
    with contextlib.suppress(Exception):
        apply_to_config(get_settings(session))  # first run / fresh DB
