import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

Base = declarative_base()

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db() -> None:
    """Initialize database engine and session factory."""
    global _engine, _SessionLocal

    if _engine is not None:
        return

    settings = get_settings()
    database_url = str(settings.database_url)

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
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Get the session factory, initializing if necessary."""
    if _SessionLocal is None:
        init_db()
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
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("Database migrations completed successfully")
        if result.stdout:
            logger.debug(f"Migration output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Database migration failed: {e}")
        if e.stderr:
            logger.error(f"Migration error output: {e.stderr}")
        raise RuntimeError("Failed to run database migrations") from e
