"""Application configuration and filesystem layout.

All application state lives under a single data directory so that the whole
installation can be inspected, backed up or wiped from one place.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def default_data_dir() -> Path:
    """Per-user data directory (respects PYFIX360_DATA_DIR override)."""
    override = os.environ.get("PYFIX360_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return base / "PYFIX360"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "PYFIX360"
    base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / "pyfix360"


@dataclass
class AppConfig:
    """Runtime configuration for the application."""

    data_dir: Path = field(default_factory=default_data_dir)
    execution_timeout_s: int = 300
    memory_limit_mb: int = 2048
    isolate_execution: bool = True
    allow_network_in_sandbox: bool = False
    auto_commit_verified: bool = False
    max_repair_retries: int = 2
    redact_before_ai: bool = True

    # --- appearance (mirrored from persisted settings at startup) -------
    theme: str = "dark"
    accent_override: str = ""
    font_size: int = 13
    font_family: str = ""
    density: str = "comfortable"
    corner_radius: int = 6
    motion_enabled: bool = True
    sidebar_width: int = 210
    start_screen: str = "Overview"
    show_status_bar: bool = True
    log_level: str = "INFO"

    # --- background / headless ---
    tray_enabled: bool = True
    close_hides_to_tray: bool = True
    service_poll_ms: int = 2000

    # --- derived paths -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "pyfix360.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def workspace_dir(self) -> Path:
        """Isolated working copies used for repair dry-runs."""
        return self.data_dir / "workspaces"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def service_dir(self) -> Path:
        """Registration state for the background host, so a session-0 service
        can find the data directory it was installed against."""
        return self.data_dir / "service"

    @property
    def service_state_path(self) -> Path:
        return self.service_dir / "host.json"

    @property
    def service_log_path(self) -> Path:
        return self.logs_dir / "service.log"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.workspace_dir, self.snapshots_dir,
                  self.reports_dir, self.logs_dir, self.service_dir):
            p.mkdir(parents=True, exist_ok=True)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
        _config.ensure_dirs()
    return _config


def set_config(cfg: AppConfig) -> None:
    """Replace the process-wide configuration (used by tests)."""
    global _config
    cfg.ensure_dirs()
    _config = cfg
