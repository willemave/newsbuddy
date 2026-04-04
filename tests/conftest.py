"""Shared test configuration and factories."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.main import app
from app.models.schema import (
    Base,
    ChatSession,
    Content,
    ContentFavorites,
    ContentReadStatus,
    ContentStatusEntry,
    ProcessingTask,
    UserIntegrationConnection,
)
from app.models.user import User


def _create_testing_engine():
    """Create an in-memory SQLite engine shared across sessions."""
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def test_db():
    """Create a test database engine."""
    engine = _create_testing_engine()
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def db_session(test_db):
    """Create a writable test database session."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_db)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def db(test_db):
    """Backward-compatible DB session fixture used by auth-heavy tests."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_db)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user_factory(db_session: Session):
    """Create persisted users with sensible defaults."""
    sequence = count(1)

    def _create(**overrides: Any) -> User:
        index = next(sequence)
        user = User(
            apple_id=f"test.apple.{index}",
            email=f"user{index}@example.com",
            full_name=f"Test User {index}",
            is_active=True,
        )
        for key, value in overrides.items():
            setattr(user, key, value)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _create


@pytest.fixture
def test_user(user_factory):
    """Create the default authenticated test user."""
    return user_factory(
        apple_id="test_apple_id_12345",
        email="test@example.com",
        full_name="Test User",
    )


def _default_content_metadata(*, title: str, content_type: str) -> dict[str, Any]:
    """Build list/detail-friendly default metadata for common content types."""
    del title, content_type
    return {}


@pytest.fixture
def content_factory(db_session: Session):
    """Create persisted content rows with list/detail-friendly defaults."""
    sequence = count(1)

    def _create(**overrides: Any) -> Content:
        index = next(sequence)
        content_type = overrides.pop("content_type", "article")
        title = overrides.pop("title", f"Test Content {index}")
        content = Content(
            content_type=content_type,
            url=overrides.pop("url", f"https://example.com/content/{index}"),
            source_url=overrides.pop("source_url", None),
            title=title,
            source=overrides.pop("source", "example.com"),
            status=overrides.pop("status", "completed"),
            platform=overrides.pop("platform", None),
            classification=overrides.pop("classification", None),
            publication_date=overrides.pop("publication_date", None),
            content_metadata=overrides.pop(
                "content_metadata",
                _default_content_metadata(title=title, content_type=content_type),
            ),
        )
        for key, value in overrides.items():
            setattr(content, key, value)
        db_session.add(content)
        db_session.commit()
        db_session.refresh(content)
        return content

    return _create


@pytest.fixture
def test_content(content_factory):
    """Create a default article content row."""
    return content_factory(
        title="Test Article",
        url="https://example.com/article",
    )


@pytest.fixture
def test_content_2(content_factory):
    """Create a second default article content row."""
    return content_factory(
        title="Test Article 2",
        url="https://example.com/article2",
    )


@pytest.fixture
def test_content_3(content_factory):
    """Create a third default article content row."""
    return content_factory(
        title="Test Article 3",
        url="https://example.com/article3",
    )


@pytest.fixture
def status_entry_factory(db_session: Session):
    """Create per-user content status rows."""

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        content: Content | None = None,
        content_id: int | None = None,
        status: str = "inbox",
        **overrides: Any,
    ) -> ContentStatusEntry:
        entry = ContentStatusEntry(
            user_id=user_id or (user.id if user is not None else None),
            content_id=content_id or (content.id if content is not None else None),
            status=status,
            **overrides,
        )
        db_session.add(entry)
        db_session.commit()
        db_session.refresh(entry)
        return entry

    return _create


@pytest.fixture
def favorite_factory(db_session: Session):
    """Create favorite rows for a user/content pair."""

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        content: Content | None = None,
        content_id: int | None = None,
        **overrides: Any,
    ) -> ContentFavorites:
        favorite = ContentFavorites(
            user_id=user_id or (user.id if user is not None else None),
            content_id=content_id or (content.id if content is not None else None),
            **overrides,
        )
        db_session.add(favorite)
        db_session.commit()
        db_session.refresh(favorite)
        return favorite

    return _create


@pytest.fixture
def read_status_factory(db_session: Session):
    """Create read-status rows for a user/content pair."""

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        content: Content | None = None,
        content_id: int | None = None,
        **overrides: Any,
    ) -> ContentReadStatus:
        entry = ContentReadStatus(
            user_id=user_id or (user.id if user is not None else None),
            content_id=content_id or (content.id if content is not None else None),
            **overrides,
        )
        db_session.add(entry)
        db_session.commit()
        db_session.refresh(entry)
        return entry

    return _create


@pytest.fixture
def chat_session_factory(db_session: Session):
    """Create chat sessions with optional content linkage."""
    sequence = count(1)

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        content: Content | None = None,
        content_id: int | None = None,
        **overrides: Any,
    ) -> ChatSession:
        index = next(sequence)
        session = ChatSession(
            user_id=user_id or (user.id if user is not None else None),
            content_id=content_id or (content.id if content is not None else None),
            title=overrides.pop("title", f"Chat Session {index}"),
            session_type=overrides.pop("session_type", "knowledge_chat"),
            llm_model=overrides.pop("llm_model", "openai:gpt-5.4"),
            llm_provider=overrides.pop("llm_provider", "openai"),
            topic=overrides.pop("topic", None),
            context_snapshot=overrides.pop("context_snapshot", None),
        )
        for key, value in overrides.items():
            setattr(session, key, value)
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        return session

    return _create


@pytest.fixture
def processing_task_factory(db_session: Session):
    """Create queued processing tasks."""

    def _create(
        *,
        content: Content | None = None,
        content_id: int | None = None,
        task_type: str = "analyze_url",
        payload: dict[str, Any] | None = None,
        status: str = "pending",
        queue_name: str = "content",
        **overrides: Any,
    ) -> ProcessingTask:
        task = ProcessingTask(
            task_type=task_type,
            content_id=content_id or (content.id if content is not None else None),
            payload=payload or {},
            status=status,
            queue_name=queue_name,
            **overrides,
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)
        return task

    return _create


@pytest.fixture
def integration_connection_factory(db_session: Session):
    """Create external provider integration rows."""
    sequence = count(1)

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        provider: str = "x",
        **overrides: Any,
    ) -> UserIntegrationConnection:
        index = next(sequence)
        connection = UserIntegrationConnection(
            user_id=user_id or (user.id if user is not None else None),
            provider=provider,
            provider_user_id=overrides.pop("provider_user_id", f"{provider}-user-{index}"),
            provider_username=overrides.pop("provider_username", f"{provider}_user_{index}"),
            scopes=overrides.pop("scopes", []),
            connection_metadata=overrides.pop("connection_metadata", {}),
            is_active=overrides.pop("is_active", True),
        )
        for key, value in overrides.items():
            setattr(connection, key, value)
        db_session.add(connection)
        db_session.commit()
        db_session.refresh(connection)
        return connection

    return _create


@pytest.fixture
def auth_headers_factory():
    """Create Authorization headers for a user."""

    def _create(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _create


@pytest.fixture
def stub_valid_feed_url(monkeypatch):
    """Accept test feed URLs without making network calls."""

    monkeypatch.setattr(
        "app.services.scraper_configs.FEED_VALIDATOR.validate_feed_url",
        lambda url: {"feed_url": url.strip()},
    )


@pytest.fixture
def client_factory(db_session: Session, user_factory):
    """Create TestClient instances with shared DB overrides and optional auth."""
    from app.core.db import get_db_session, get_readonly_db_session
    from app.core.deps import get_current_user

    def override_get_db() -> Iterator[Session]:
        try:
            yield db_session
        finally:
            pass

    @contextmanager
    def _create(
        *,
        user: User | None = None,
        authenticate: bool = True,
        extra_overrides: dict[Any, Any] | None = None,
    ) -> Iterator[TestClient]:
        app.dependency_overrides[get_db_session] = override_get_db
        app.dependency_overrides[get_readonly_db_session] = override_get_db

        if authenticate:
            resolved_user = user or user_factory()

            def override_get_current_user() -> User:
                return resolved_user

            app.dependency_overrides[get_current_user] = override_get_current_user

        if extra_overrides:
            app.dependency_overrides.update(extra_overrides)

        try:
            with TestClient(app) as test_client:
                yield test_client
        finally:
            app.dependency_overrides.clear()

    return _create


@pytest.fixture
def client(client_factory, test_user):
    """Create the default authenticated client."""
    with client_factory(user=test_user) as test_client:
        yield test_client


# Content fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(fixture_name: str) -> dict[str, Any]:
    """Load a fixture from the fixtures directory.

    Args:
        fixture_name: Name of the fixture file (without .json extension)

    Returns:
        Parsed JSON data from the fixture file
    """
    fixture_path = FIXTURES_DIR / f"{fixture_name}.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def content_samples() -> dict[str, dict[str, Any]]:
    """Load all content samples from fixtures.

    Returns:
        Dictionary with keys:
        - article_long_form: Long-form article with full summary
        - article_short_technical: Short technical article
        - podcast_interview: Podcast episode with transcript and summary
        - raw_content_unprocessed: Unprocessed article (status='new')
        - podcast_raw_transcript: Podcast with transcript but no summary
    """
    return load_fixture("content_samples")


@pytest.fixture
def sample_article_long(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a long-form article sample."""
    return content_samples["article_long_form"]


@pytest.fixture
def sample_article_short(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a short technical article sample."""
    return content_samples["article_short_technical"]


@pytest.fixture
def sample_podcast(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a podcast episode sample with full processing."""
    return content_samples["podcast_interview"]


@pytest.fixture
def sample_unprocessed_article(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get an unprocessed article (for testing processing pipeline)."""
    return content_samples["raw_content_unprocessed"]


@pytest.fixture
def sample_unprocessed_podcast(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a podcast with transcript but no summary (for testing summarization)."""
    return content_samples["podcast_raw_transcript"]


def _parse_datetime(date_str: str | None) -> datetime | None:
    """Parse ISO format date string to datetime object.

    Args:
        date_str: ISO format date string (e.g., "2025-06-21T15:51:43")

    Returns:
        datetime object or None if date_str is None
    """
    if not date_str:
        return None

    try:
        # Try parsing with microseconds
        return datetime.fromisoformat(date_str)
    except ValueError:
        # Try parsing without microseconds
        try:
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            # Try parsing date only
            return datetime.strptime(date_str, "%Y-%m-%d")


def create_content_from_fixture(fixture_data: dict[str, Any]) -> Content:
    """Create a Content model instance from fixture data.

    Args:
        fixture_data: Dictionary containing content data from fixture

    Returns:
        Content model instance ready to be added to database
    """
    return Content(
        id=fixture_data.get("id"),
        content_type=fixture_data["content_type"],
        url=fixture_data["url"],
        title=fixture_data["title"],
        source=fixture_data["source"],
        status=fixture_data["status"],
        platform=fixture_data.get("platform"),
        classification=fixture_data.get("classification"),
        publication_date=_parse_datetime(fixture_data.get("publication_date")),
        content_metadata=fixture_data.get("content_metadata", {}),
    )


@pytest.fixture
def create_sample_content(db_session, status_entry_factory, test_user):
    """Factory fixture to create content from samples in the database.

    Usage:
        content = create_sample_content(sample_article_long)
    """

    def _create(
        fixture_data: dict[str, Any],
        *,
        visible: bool = True,
        user: User | None = None,
    ) -> Content:
        content = create_content_from_fixture(fixture_data)
        db_session.add(content)
        db_session.commit()
        db_session.refresh(content)
        if visible:
            status_entry_factory(user=user or test_user, content=content, status="inbox")
        return content

    return _create
