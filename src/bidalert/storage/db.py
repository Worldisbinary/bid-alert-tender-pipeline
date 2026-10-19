"""Engine/session factory and programmatic Alembic migrations."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker


def _ensure_sqlite_dir(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        _ensure_sqlite_dir(database_url)
        engine = create_engine(database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            # WAL lets the API read while the worker writes.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(resources.files("bidalert") / "migrations"))
    # ConfigParser interpolation: escape % in passwords.
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def migrate(database_url: str, revision: str = "head") -> None:
    _ensure_sqlite_dir(database_url)
    command.upgrade(alembic_config(database_url), revision)
