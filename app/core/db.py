import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

Base = declarative_base()

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_CONFIG_PATH = _PROJECT_ROOT / "migrations" / "alembic.ini"


def init_db() -> None:
    """Initialize database engine and session factory."""
    global _engine, _SessionLocal

    if _engine is not None:
        return

    settings = get_settings()
    database_url = str(settings.database_url)
    database_url_object = make_url(database_url)
    if database_url_object.drivername.startswith("sqlite"):
        raise RuntimeError(
            "SQLite has been deprecated as a Newsly runtime dialect. "
            "Configure DATABASE_URL with PostgreSQL."
        )
    is_postgres_driver = database_url_object.drivername == "postgres" or (
        database_url_object.drivername.startswith("postgresql")
    )
    if not is_postgres_driver:
        raise RuntimeError("Newsly requires a PostgreSQL DATABASE_URL")

    _engine = create_engine(
        database_url,
        pool_pre_ping=True,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    logger.info("Database initialized successfully")


def get_engine() -> Engine:
    """Get the database engine, initializing if necessary."""
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def dispose_db_engine() -> None:
    """Dispose pooled DB connections so the next checkout starts fresh."""
    global _engine
    if _engine is None:
        return
    _engine.dispose()


def get_session_factory() -> sessionmaker[Session]:
    """Get the session factory, initializing if necessary."""
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def get_db() -> Iterator[Session]:
    """Context manager for database sessions."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session() -> Generator[Session]:
    """Get a database session for FastAPI dependency injection."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_readonly_db_session() -> Generator[Session]:
    """Get a read-only database session for FastAPI dependency injection."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Run Alembic migrations to ensure database schema is up to date."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(_ALEMBIC_CONFIG_PATH),
                "upgrade",
                "head",
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=_PROJECT_ROOT,
        )
        logger.info("Database migrations completed successfully")
        if result.stdout:
            logger.debug(f"Migration output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Database migration failed: {e}")
        if e.stderr:
            logger.error(f"Migration error output: {e.stderr}")
        raise RuntimeError("Failed to run database migrations") from e
