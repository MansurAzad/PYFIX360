"""Tests for the self-update path.

Three things have to be true for an update to be trustworthy:

1. the policy decides whether the machine may talk to the update server at all;
2. the manifest is read from the release, not from anything the app carries;
3. the downloaded file is verified against the checksum the release published,
   and a mismatch is discarded rather than installed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pyfix360 import __version__
from pyfix360.models import AccessLevel
from pyfix360.services import settings_service, update_service
from pyfix360.services.settings_service import AccessDenied

# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("0.1.0.142", (0, 1, 0, 142)),
        ("v0.1.0.142", (0, 1, 0, 142)),
        ("", (0,)),
        ("not-a-version", (0,)),
    ],
)
def test_parse_version(text, expected):
    assert update_service.parse_version(text) == expected


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("0.1.0.142", "0.1.0.141", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.1.1", False),
        ("0.2.0", "0.1.9", True),
        ("1.0.0", "0.9.9", True),
        ("0.1.0.2", "0.1.0.10", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert update_service.is_newer(latest, current) is newer


# ---------------------------------------------------------------------------
# the policy
# ---------------------------------------------------------------------------


def _policy(session, level: AccessLevel) -> None:
    st = settings_service.get_settings(session)
    st.access_check_for_updates = level
    settings_service.save_settings(session, st)


def test_a_blocked_policy_refuses_the_check(session):
    _policy(session, AccessLevel.BLOCKED)
    with pytest.raises(AccessDenied):
        update_service.check(session, confirmed=True)


def test_ask_refuses_a_check_that_nobody_confirmed(session):
    with pytest.raises(AccessDenied):
        update_service.check(session)


def test_the_default_policy_asks(session):
    st = settings_service.get_settings(session)
    assert st.access_check_for_updates is AccessLevel.ASK


def test_a_confirmed_check_is_allowed_under_ask(session, monkeypatch):
    monkeypatch.setattr(update_service, "fetch_manifest",
                        lambda repo=None: {"version": "0.0.1", "url": "u",
                                           "sha256": "0" * 64})
    update_service.check(session, confirmed=True)


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------


def _manifest(version="9.9.9", sha="a" * 64, url="https://example.invalid/Setup.exe"):
    return {"version": version, "url": url, "sha256": sha, "size": 42,
            "notes": "notes", "published_at": "2026-01-01T00:00:00Z",
            "tag": f"v{version}"}


def test_a_newer_release_is_reported(session, monkeypatch):
    monkeypatch.setattr(update_service, "fetch_manifest", lambda repo=None: _manifest())
    info = update_service.check(session, confirmed=True)
    assert info is not None
    assert info.version == "9.9.9"
    assert info.current == __version__
    assert info.sha256 == "a" * 64


def test_the_running_version_wins_when_it_is_the_newest(session, monkeypatch):
    monkeypatch.setattr(update_service, "fetch_manifest",
                        lambda repo=None: _manifest(version="0.0.1"))
    assert update_service.check(session, confirmed=True) is None


def test_a_manifest_without_a_checksum_is_refused(session, monkeypatch):
    monkeypatch.setattr(update_service, "fetch_manifest",
                        lambda repo=None: _manifest(sha=""))
    with pytest.raises(update_service.UpdateError) as exc:
        update_service.check(session, confirmed=True)
    assert "checksum" in str(exc.value)


def test_a_manifest_without_a_url_is_refused(session, monkeypatch):
    monkeypatch.setattr(update_service, "fetch_manifest",
                        lambda repo=None: _manifest(url=""))
    with pytest.raises(update_service.UpdateError):
        update_service.check(session, confirmed=True)


def test_the_check_is_written_to_the_audit_trail(session, monkeypatch):
    from pyfix360.db.models import AuditEventRow

    monkeypatch.setattr(update_service, "fetch_manifest", lambda repo=None: _manifest())
    update_service.check(session, confirmed=True)
    actions = [row.action for row in session.query(AuditEventRow).all()]
    assert "update.checked" in actions


def test_the_manifest_comes_from_the_release_asset(monkeypatch):
    """The newest release must carry the manifest the updater reads."""
    release = {
        "tag_name": "v1.2.3",
        "body": "notes",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [
            {"name": "checksums.txt", "browser_download_url": "https://x/checksums.txt"},
            {"name": "latest.json", "browser_download_url": "https://x/latest.json"},
        ],
    }
    asked = []

    def fake_get(url, **kwargs):
        asked.append(url)
        if url.endswith("/releases/latest"):
            return json.dumps(release).encode()
        return json.dumps({"version": "1.2.3", "url": "https://x/setup.exe",
                           "sha256": "b" * 64}).encode()

    monkeypatch.setattr(update_service, "_get", fake_get)
    manifest = update_service.fetch_manifest("owner/repo")
    assert manifest["version"] == "1.2.3"
    assert manifest["notes"] == "notes"
    assert any(url.endswith("latest.json") for url in asked)


def test_a_release_without_a_manifest_is_refused(monkeypatch):
    release = {"tag_name": "v1.2.3", "assets": []}
    monkeypatch.setattr(
        update_service, "_get",
        lambda url, **kwargs: json.dumps(release).encode(),
    )
    with pytest.raises(update_service.UpdateError) as exc:
        update_service.fetch_manifest("owner/repo")
    assert "latest.json" in str(exc.value)


# ---------------------------------------------------------------------------
# the download
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.headers = {"Content-Length": str(len(data))}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data)
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _info(sha: str) -> update_service.UpdateInfo:
    return update_service.UpdateInfo(
        current="0.1.0", version="9.9.9",
        url="https://example.invalid/Setup_PYFIX360.exe", sha256=sha,
    )


def test_a_verified_download_is_kept(monkeypatch, tmp_path):
    payload = b"an installer"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(update_service.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    path = update_service.download(_info(digest), dest_dir=tmp_path)
    assert path.is_file()
    assert path.read_bytes() == payload


def test_a_download_that_does_not_match_is_discarded(monkeypatch, tmp_path):
    payload = b"a tampered installer"
    monkeypatch.setattr(update_service.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    with pytest.raises(update_service.UpdateError) as exc:
        update_service.download(_info("c" * 64), dest_dir=tmp_path)
    assert "checksum" in str(exc.value)
    assert not list(tmp_path.glob("*.exe"))


def test_the_download_reports_progress(monkeypatch, tmp_path):
    payload = b"0123456789" * 100
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(update_service.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    seen: list[tuple[int, int]] = []
    update_service.download(_info(digest), dest_dir=tmp_path,
                            progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert seen[-1][0] == len(payload)


# ---------------------------------------------------------------------------
# the handover to the installer
# ---------------------------------------------------------------------------


def test_the_installer_runs_silently_and_restarts_the_application(monkeypatch):
    started = {}

    class _FakePopen:
        def __init__(self, command, **kwargs):
            started["command"] = command
            started["kwargs"] = kwargs

    monkeypatch.setattr(update_service.subprocess, "Popen", _FakePopen)
    update_service.install(Path("C:/tmp/Setup.exe"))
    command = started["command"]
    assert command[0].endswith("Setup.exe")
    assert "/VERYSILENT" in command
    assert "/NORESTART" in command
    assert "/CLOSEAPPLICATIONS" in command
    assert "/RESTARTAPPLICATIONS" in command


def test_starting_an_install_is_recorded(session, tmp_path):
    from pyfix360.db.models import AuditEventRow

    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"x")
    monkeypatched = {}

    class _FakePopen:
        def __init__(self, command, **kwargs):
            monkeypatched["command"] = command

    original = update_service.subprocess.Popen
    update_service.subprocess.Popen = _FakePopen
    try:
        update_service.start_install(session, _info("d" * 64), installer)
    finally:
        update_service.subprocess.Popen = original
    actions = [row.action for row in session.query(AuditEventRow).all()]
    assert "update.installing" in actions


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_the_cli_reports_when_the_installed_version_is_the_newest(monkeypatch, capsys):
    from pyfix360 import cli

    monkeypatch.setattr(update_service, "check", lambda *a, **k: None)
    assert cli.main(["update", "--check", "--yes"]) == 0
    assert "newest version" in capsys.readouterr().out


def test_the_cli_reports_a_newer_version(monkeypatch, capsys):
    from pyfix360 import cli

    monkeypatch.setattr(update_service, "check", lambda *a, **k: _info("e" * 64))
    assert cli.main(["update", "--check", "--yes"]) == 0
    assert "9.9.9" in capsys.readouterr().out


def test_the_cli_refuses_when_the_policy_blocks_it(monkeypatch, capsys):
    from pyfix360 import cli

    def refuse(*args, **kwargs):
        raise AccessDenied("'check_for_updates' is set to BLOCKED")

    monkeypatch.setattr(update_service, "check", refuse)
    assert cli.main(["update", "--check"]) == 1
    assert "BLOCKED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the version the build publishes
# ---------------------------------------------------------------------------


def test_the_version_in_pyproject_matches_the_package():
    """The release pipeline reads the version from pyproject.toml."""
    import tomllib

    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
