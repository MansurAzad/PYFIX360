"""Database engine/session management and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pyfix360.config import get_config
from pyfix360.db.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    global _engine, _session_factory
    if _engine is None or url is not None:
        if url is None:
            url = get_config().db_url
        _engine = create_engine(url, echo=echo, future=True)
        if _engine.dialect.name == "sqlite":
            event.listen(_engine, "connect", _configure_sqlite)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _reconcile_schema(engine: Engine) -> list[str]:
    """Add tables and columns that exist in the ORM but not in the database.

    ``create_all`` is additive at the *table* level: it creates missing tables but
    silently ignores new columns on tables that already exist. That is a real
    upgrade hazard — a user who installed a previous version has an ``ai_providers``
    table without the columns added since, and every query then fails with
    "no such column".

    This walks the ORM metadata against the live schema and issues the missing
    ``CREATE TABLE`` / ``ALTER TABLE ... ADD COLUMN`` statements. It is
    intentionally conservative: it only ever *adds*, never drops or retypes, so it
    cannot destroy data. Returns a list of human-readable changes for logging.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    changes: list[str] = []

    # A column is only safe to add without a default when it is nullable or has a
    # server default; SQLAlchemy exposes this via the column definition.
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all already handled brand-new tables
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            ddl = _column_ddl(column, engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
            changes.append(f"{table.name}.{column.name}")
    return changes


def _column_ddl(column, dialect) -> str:
    """Render a single ADD COLUMN clause for the given dialect."""
    from sqlalchemy.schema import CreateColumn

    compiled = str(CreateColumn(column).compile(dialect=dialect))
    # SQLite forbids adding a NOT NULL column with no default to a non-empty
    # table, and forbids PRIMARY KEY/UNIQUE here. Relax the definition to a
    # plain nullable column so the upgrade cannot fail on existing rows.
    compiled = compiled.replace(" NOT NULL", "")
    return compiled


def init_db(url: str | None = None) -> None:
    """Create or upgrade the schema so it matches the ORM metadata.

    Safe to call on every startup: it creates missing tables (``create_all``) and
    then adds any missing columns on tables that already existed. Never drops or
    retypes anything, so it cannot lose data.

    A database Alembic has never seen is stamped at the migration head once the
    schema is in place, so the two ways of building the database agree: running
    ``alembic upgrade head`` afterwards is a no-op instead of a "table already
    exists" failure.
    """
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _reconcile_schema(engine)
    _stamp_alembic_head(engine)


# --------------------------------------------------------------------------------------
# alembic
# --------------------------------------------------------------------------------------


def _migrations_dir() -> Path | None:
    """Where the migration scripts live, whether packaged or in a checkout."""
    packaged = Path(__file__).resolve().parent.parent / "migrations"
    if (packaged / "versions").is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[3] / "migrations"
    return checkout if (checkout / "versions").is_dir() else None


def alembic_head() -> str | None:
    """The newest migration revision, or None when the scripts are absent."""
    directory = _migrations_dir()
    if directory is None:
        return None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:                                # pragma: no cover
        return None
    config = Config()
    config.set_main_option("script_location", str(directory))
    try:
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:                                 # pragma: no cover
        return None


def _stamp_alembic_head(engine: Engine) -> None:
    """Record the migration head for a database Alembic has never managed.

    Only databases with no ``alembic_version`` row are stamped, and only when
    the live schema already matches the ORM — the migrations are DDL only, so
    a schema built by ``create_all`` plus the column reconciliation is at the
    head as far as structure goes. A database Alembic *does* manage is left
    alone, so its pending migrations still run.
    """
    from sqlalchemy import inspect, text

    head = alembic_head()
    if head is None:
        return
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables:
        with engine.begin() as conn:
            stamped = conn.execute(
                text("SELECT version_num FROM alembic_version")).fetchall()
        if stamped:
            return
    # the ORM must be fully present, or head would be a lie
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            return
        have = {c["name"] for c in inspector.get_columns(table.name)}
        if any(column.name not in have for column in table.columns):
            return
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                     {"rev": head})


def reset_engine() -> None:
    """Dispose of the cached engine (used by tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    if _session_factory is None:
        get_engine(url)
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
