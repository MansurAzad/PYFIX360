"""Shared test fixtures.

Every test gets an isolated data directory and a fresh in-memory-file SQLite
database. The global config/engine singletons are reset around each test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pyfix360.config import AppConfig, set_config
from pyfix360.db import init_db, reset_engine, session_scope


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("PYFIX360_DATA_DIR", raising=False)
    d = tmp_path / "pyfix_data"
    set_config(AppConfig(data_dir=d))
    reset_engine()
    init_db()
    yield d
    reset_engine()


@pytest.fixture()
def session(data_dir: Path) -> Iterator:
    with session_scope() as s:
        yield s


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    """A small but realistic project with seeded defects and passing tests."""
    root = tmp_path / "sample_proj"
    (root / "tests").mkdir(parents=True)
    (root / "conftest.py").write_text("", encoding="utf-8")
    (root / "app.py").write_text(
        "import hashlib\n"
        "import subprocess\n"
        "\n"
        "\n"
        "def digest(data, salt=[]):\n"
        "    return hashlib.md5(data + salt).hexdigest()\n"
        "\n"
        "\n"
        "def run(cmd):\n"
        "    try:\n"
        "        subprocess.run(cmd, shell=True)\n"
        "    except:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_app.py").write_text(
        "import app\n\n\ndef test_digest():\n    assert app.digest(b'x', b'')\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    (root / "README.md").write_text("# Sample\n\n## load\n\nDocumented feature.\n",
                                    encoding="utf-8")
    return root


@pytest.fixture()
def no_keyring(monkeypatch: pytest.MonkeyPatch):
    """Force the encrypted-file credential backend for determinism."""
    from pyfix360.credentials import store as cred_store

    monkeypatch.setattr(cred_store, "_keyring", None)
    monkeypatch.setattr(cred_store, "_keyring_error", "forced off for tests")
    monkeypatch.setattr(cred_store, "_load_keyring", lambda: None)
    return cred_store


@pytest.fixture(scope="session")
def qapp():
    """A QApplication for the tests that need real widgets.

    Only a handful of modules (the autopilot key filter) cannot be exercised
    without instantiated widgets, and building one needs an application. Skipped
    where no display server is reachable so the suite stays runnable headless.
    """
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    yield app
