"""Tests for in-place schema reconciliation in ``db.session.init_db``.

``create_all`` only creates *missing tables*; it silently ignores new **columns**
on tables that already exist. That is a genuine upgrade hazard: a user who
installed an earlier version keeps an ``ai_providers`` table without the columns
added since, and every query fails with "no such column".

``init_db`` therefore also reconciles columns. These tests pin the behaviour that
matters:

* a missing column is added, and the data already in the table survives
* a missing table is created
* running it twice is a no-op (idempotent)
* nothing is ever dropped or retyped
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, text

from pyfix360.db import session as db_session
from pyfix360.db.models import Base


def _legacy_db(path) -> None:
    """Build a database that looks like an older release: ai_providers without
    the catalogue/verification columns, and no quality_gates/ai_chats tables."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE ai_providers (
                id INTEGER PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                provider_type VARCHAR(32) NOT NULL,
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                timeout_s INTEGER NOT NULL,
                retry_limit INTEGER NOT NULL,
                privacy_mode VARCHAR(32) NOT NULL,
                enabled BOOLEAN NOT NULL
            )
            """
        ))
        conn.execute(text(
            "INSERT INTO ai_providers (id, name, provider_type, base_url, model,"
            " timeout_s, retry_limit, privacy_mode, enabled)"
            " VALUES (1, 'legacy', 'local_runtime', 'http://localhost:11434/v1',"
            " 'qwen', 60, 2, 'local_only', 1)"
        ))
    engine.dispose()


def _columns(path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _tables(path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_missing_columns_are_added(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    assert "catalog_key" not in _columns(path, "ai_providers")

    db_session.init_db(f"sqlite:///{path}")

    cols = _columns(path, "ai_providers")
    for expected in ("catalog_key", "discovered_models", "last_verified_at",
                     "last_verify_ok"):
        assert expected in cols, f"{expected} was not added"


def test_existing_rows_survive_the_upgrade(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    db_session.init_db(f"sqlite:///{path}")

    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "SELECT name, model FROM ai_providers WHERE id = 1").fetchone()
    finally:
        con.close()
    assert row == ("legacy", "qwen")


def test_missing_tables_are_created(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    db_session.init_db(f"sqlite:///{path}")

    tables = _tables(path)
    assert "quality_gates" in tables
    assert "ai_chats" in tables


def test_reconcile_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    db_session.init_db(f"sqlite:///{path}")
    first = _columns(path, "ai_providers")

    # A second run on the now-current schema must change nothing.
    db_session.init_db(f"sqlite:///{path}")
    assert _columns(path, "ai_providers") == first


def test_reconcile_never_drops_a_table(tmp_path):
    path = tmp_path / "custom.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE user_scratch (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    db_session.init_db(f"sqlite:///{path}")

    assert "user_scratch" in _tables(path)


def test_reconcile_reports_what_it_changed(tmp_path):
    """The change list is what would be logged, so it must be accurate."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    changes = db_session._reconcile_schema(engine)
    engine.dispose()

    assert "ai_providers.catalog_key" in changes
    # and a fully-current database reports nothing
    engine = create_engine(f"sqlite:///{path}")
    assert db_session._reconcile_schema(engine) == []
    engine.dispose()


def test_fresh_database_is_complete(tmp_path):
    path = tmp_path / "fresh.db"
    db_session.init_db(f"sqlite:///{path}")

    tables = _tables(path)
    # Derive the expected set from the ORM so this cannot drift from the models.
    for table in Base.metadata.sorted_tables:
        assert table.name in tables, f"{table.name} missing from a fresh database"
    # And a couple of sanity checks that the new features are really there.
    for expected in ("quality_gates", "ai_chats", "settings"):
        assert expected in tables
