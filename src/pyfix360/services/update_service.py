"""Self-update: find a newer release, fetch it, and hand over to the installer.

The updater is deliberately small and boring:

* it talks to exactly one host — the GitHub repository that publishes the
  releases — and it says nothing about the user's machine beyond the request
  itself;
* the file it downloads is verified against the SHA-256 that the release
  published, so a truncated or tampered download is refused instead of
  installed;
* the update is applied by running the Inno Setup package the release
  carries. That package replaces the installed folder and the shortcuts, which
  is the only way to update an installed copy of a multi-file application
  while it is running.

Policy
------
A check is a remote call, so it goes through the access matrix like every
other remote action. ``access_check_for_updates`` defaults to ASK: nothing
leaves the machine until the user asks for it, and the automatic startup
check only runs when the level is FULL. BLOCKED means the app never phones
home at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from pyfix360 import __version__
from pyfix360.config import get_config
from pyfix360.services import audit, settings_service

#: The repository that publishes the releases. Override with the environment
#: variable when a fork publishes its own builds.
DEFAULT_REPO = "MansurAzad/PYFIX360"
REPO_ENV = "PYFIX360_UPDATE_REPO"

#: The capability that gates every remote call this module makes.
CAPABILITY = "check_for_updates"

API_ROOT = "https://api.github.com"
MANIFEST_NAME = "latest.json"
USER_AGENT = f"PYFIX360/{__version__}"

#: how long a network call may take before it is called a failure
TIMEOUT_S = 20.0


class UpdateError(Exception):
    """An update could not be found, fetched or verified."""


@dataclass(frozen=True)
class UpdateInfo:
    """A newer release that is available."""

    current: str
    version: str
    url: str
    sha256: str
    size: int = 0
    notes: str = ""
    published_at: str = ""
    tag: str = ""

    @property
    def filename(self) -> str:
        return Path(self.url).name or f"PYFIX360-{self.version}.exe"


# --------------------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    """Turn ``v0.1.0.142`` into ``(0, 1, 0, 142)``."""
    core = (text or "").strip().lstrip("vV").split("+")[0]
    parts: list[int] = []
    for chunk in core.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly newer version than ``current``."""
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a > b


def repo_slug(repo: str | None = None) -> str:
    """Which repository to look in."""
    return repo or os.environ.get(REPO_ENV) or DEFAULT_REPO


# --------------------------------------------------------------------------------------
# the network
# --------------------------------------------------------------------------------------


def _get(url: str, *, timeout: float = TIMEOUT_S) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"the update server answered {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"could not reach the update server: {exc.reason}") from exc
    except OSError as exc:  # pragma: no cover - socket level failure
        raise UpdateError(f"could not reach the update server: {exc}") from exc


def fetch_manifest(repo: str | None = None) -> dict:
    """The ``latest.json`` manifest of the newest release."""
    slug = repo_slug(repo)
    try:
        release = json.loads(_get(f"{API_ROOT}/repos/{slug}/releases/latest"))
    except json.JSONDecodeError as exc:
        raise UpdateError("the update server sent something that is not JSON") from exc
    if not isinstance(release, dict):
        raise UpdateError("the update server sent an unexpected answer")

    assets = {a.get("name"): a for a in release.get("assets", []) if isinstance(a, dict)}
    asset = assets.get(MANIFEST_NAME)
    if asset is None:
        raise UpdateError(
            f"the newest release of {slug} carries no {MANIFEST_NAME}, so it was "
            "not published by the release pipeline"
        )
    try:
        manifest = json.loads(_get(asset["browser_download_url"]))
    except (json.JSONDecodeError, KeyError) as exc:
        raise UpdateError(f"the update manifest is not readable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("the update manifest is not readable")
    manifest.setdefault("notes", release.get("body") or "")
    manifest.setdefault("published_at", release.get("published_at") or "")
    manifest.setdefault("tag", release.get("tag_name") or "")
    return manifest


def check(
    session: Session,
    *,
    repo: str | None = None,
    confirmed: bool = False,
) -> UpdateInfo | None:
    """Look for a newer release.

    Returns the update when one is newer than the running version, and None
    when the running version is already the newest one. Raises
    :class:`~pyfix360.services.settings_service.AccessDenied` when the access
    policy does not allow the call.
    """
    settings_service.check_access(session, CAPABILITY, confirmed=confirmed)

    manifest = fetch_manifest(repo)
    version = str(manifest.get("version") or "")
    url = str(manifest.get("url") or "")
    sha256 = str(manifest.get("sha256") or "")
    if not version or not url:
        raise UpdateError("the update manifest is incomplete")
    if not sha256:
        raise UpdateError(
            "the release publishes no checksum, so the download cannot be verified"
        )

    info = UpdateInfo(
        current=__version__,
        version=version,
        url=url,
        sha256=sha256.lower(),
        size=int(manifest.get("size") or 0),
        notes=str(manifest.get("notes") or ""),
        published_at=str(manifest.get("published_at") or ""),
        tag=str(manifest.get("tag") or ""),
    )
    audit.record(
        session,
        "update.checked",
        target=f"v{info.version}",
        details=f"current {info.current}; policy allowed the call",
        actor="system",
        risk="info",
    )
    if not is_newer(info.version, info.current):
        return None
    return info


# --------------------------------------------------------------------------------------
# the download
# --------------------------------------------------------------------------------------


def staging_dir() -> Path:
    """Where a downloaded installer waits before it is run."""
    path = get_config().data_dir / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    info: UpdateInfo,
    *,
    dest_dir: Path | None = None,
    progress=None,
    timeout: float = TIMEOUT_S,
) -> Path:
    """Fetch the installer and verify it.

    ``progress`` is called with ``(downloaded_bytes, total_bytes)`` while the
    file arrives; ``total_bytes`` is 0 when the server does not say.
    """
    folder = Path(dest_dir) if dest_dir else staging_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / info.filename

    request = urllib.request.Request(info.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"the download failed: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"the download failed: {exc.reason}") from exc
    except OSError as exc:  # pragma: no cover - socket level failure
        raise UpdateError(f"the download failed: {exc}") from exc

    actual = _sha256(target)
    if actual.lower() != info.sha256.lower():
        target.unlink(missing_ok=True)
        raise UpdateError(
            "the downloaded file does not match the checksum the release "
            "published, so it was discarded"
        )
    return target


# --------------------------------------------------------------------------------------
# the installation
# --------------------------------------------------------------------------------------


def install(installer: Path) -> subprocess.Popen:
    """Run the installer so it replaces the installed application.

    The package is an Inno Setup program: it closes the running application,
    replaces the installed folder, refreshes the shortcuts and starts the new
    version again. ``/NORESTART`` keeps the *machine* from rebooting, which is
    not what a silent install should ever do.
    """
    command = [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
        "/SP-",
    ]
    return subprocess.Popen(command, cwd=str(installer.parent))


def start_install(session: Session, info: UpdateInfo, installer: Path) -> None:
    """Hand over to the installer and let it replace the installed application.

    The installer restarts the new version through the Windows Restart
    Manager, so the application has to let go of its files afterwards.
    """
    audit.record(
        session,
        "update.installing",
        target=f"v{info.version}",
        details=f"installer {installer.name}; checksum verified",
        actor="user",
        risk="warn",
    )
    install(installer)
