"""Temporary PostgreSQL harness helpers for eval and test-style workflows."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.schema import Table


def _base_database_url() -> URL:
    candidate = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql+psycopg://postgres@127.0.0.1/postgres"
    )
    url = make_url(candidate)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError(f"Temporary harness requires PostgreSQL, got {url.drivername!r}")
    return url


def _admin_database_url(base_url: URL) -> str:
    return base_url.render_as_string(hide_password=False)


def _schema_database_url(base_url: URL, schema_name: str) -> str:
    return base_url.update_query_dict({"options": f"-csearch_path={schema_name}"}).render_as_string(
        hide_password=False
    )


@dataclass
class TemporaryPostgresHarness:
    """Ephemeral PostgreSQL schema plus session factory."""

    database_url: str
    engine: Engine
    session_factory: sessionmaker
    _admin_engine: Engine
    _schema_name: str

    def close(self) -> None:
        """Drop schema objects and remove the temp schema."""
        self.engine.dispose()
        try:
            with self._admin_engine.connect() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{self._schema_name}" CASCADE'))
        finally:
            self._admin_engine.dispose()


def create_temporary_postgres_harness(
    *,
    schema_prefix: str = "newsly_eval",
    tables: Sequence[Table] | None = None,
) -> TemporaryPostgresHarness:
    """Create a temporary PostgreSQL-backed schema and session factory."""
    base_url = _base_database_url()
    schema_name = f"{schema_prefix}_{uuid4().hex}"
    admin_engine = create_engine(_admin_database_url(base_url), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    database_url = _schema_database_url(base_url, schema_name)
    engine = create_engine(database_url, pool_pre_ping=True)
    metadata = tables[0].metadata if tables else None
    if metadata is None:
        from app.core.db import Base

        metadata = Base.metadata
    metadata.create_all(bind=engine, tables=list(tables) if tables is not None else None)
    return TemporaryPostgresHarness(
        database_url=database_url,
        engine=engine,
        session_factory=sessionmaker(autocommit=False, autoflush=False, bind=engine),
        _admin_engine=admin_engine,
        _schema_name=schema_name,
    )


@contextmanager
def open_temporary_postgres_session() -> Iterator[Session]:
    """Yield one session from a temporary PostgreSQL harness."""
    harness = create_temporary_postgres_harness()
    session = harness.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        harness.close()
