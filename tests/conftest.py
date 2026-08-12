"""Shared test configuration and factories."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.core.db as core_db
from app.core.security import create_access_token
from app.main import app
from app.models.db import Content
from app.models.db.users import User
from app.testing.postgres_harness import TemporaryPostgresHarness, create_temporary_postgres_harness
from tests.support.builders import (
    build_content_row,
    create_chat_session_row,
    create_content_knowledge_save_row,
    create_content_read_status_row,
    create_content_row,
    create_content_status_entry_row,
    create_integration_connection_row,
    create_news_item_row,
    create_processing_task_row,
    create_user_row,
)
from tests.support.fixture_files import load_json_fixture


@pytest.fixture
def stub_briefing_layout_generator(monkeypatch):
    """Replace Briefing model I/O with valid model-shaped output in orchestration tests."""

    def _generate(sources, **kwargs):  # noqa: ANN001
        source_sentences = []
        for source in sources:
            url_kind = "content" if source.kind == "content" else "news"
            summary = source.summary or (
                source.key_points[0] if source.key_points else "is ready to read"
            )
            source_sentences.append(
                f"[{source.title}](newsly://briefing/{url_kind}/{source.id}) {summary}"
            )
        if kwargs.get("tier") == "news":
            clauses = [
                re.sub(r"[.!?]+(?=\s|$)", ",", sentence).strip(" ,;")
                for sentence in source_sentences
            ]
            markdown = "; ".join(clauses) + "."
        else:
            markdown = " ".join(source_sentences)
        return [
            {
                "type": "passage",
                "weight": "feature",
                "markdown": markdown,
            }
        ], None

    monkeypatch.setattr(
        "app.services.briefing.composer.generate_layout_with_llm",
        _generate,
    )
    return _generate


@pytest.fixture
def _isolated_content_image_storage(monkeypatch, tmp_path) -> Path:
    """Keep generated-image existence checks away from developer assets."""
    content_images_dir = tmp_path / "content-images"
    monkeypatch.setattr(
        "app.services.long_form_images.get_content_images_dir",
        lambda: content_images_dir,
    )
    return content_images_dir


@pytest.fixture
def postgres_harness() -> Iterator[TemporaryPostgresHarness]:
    """Create an isolated PostgreSQL harness and bind global DB access to it."""
    harness = create_temporary_postgres_harness(schema_prefix="newsly_test")
    previous_engine = core_db._engine
    previous_session_local = core_db._SessionLocal

    core_db._engine = harness.engine
    core_db._SessionLocal = harness.session_factory
    try:
        yield harness
    finally:
        core_db._engine = previous_engine
        core_db._SessionLocal = previous_session_local
        harness.close()


@pytest.fixture
def test_db(postgres_harness: TemporaryPostgresHarness):
    """Create an isolated PostgreSQL test database and bind global DB access to it."""
    return postgres_harness.engine


@pytest.fixture
def db_session_factory(postgres_harness: TemporaryPostgresHarness) -> sessionmaker:
    """Return a session factory bound to the shared isolated test database."""
    return postgres_harness.session_factory


@pytest.fixture
def db_session(db_session_factory: sessionmaker):
    """Create a writable test database session."""
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def db(db_session_factory: sessionmaker):
    """Backward-compatible DB session fixture used by auth-heavy tests."""
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def vendor_usage_db(db_session_factory: sessionmaker, monkeypatch):
    """Route out-of-band vendor usage writes to the current test database."""
    from app.services import vendor_costs

    @contextmanager
    def _get_db():
        session = db_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(vendor_costs, "get_db", _get_db)


@pytest.fixture
def user_factory(db_session: Session):
    """Create persisted users with sensible defaults."""
    sequence = count(1)

    def _create(**overrides: Any) -> User:
        return create_user_row(db_session, index=next(sequence), **overrides)

    return _create


@pytest.fixture
def test_user(user_factory):
    """Create the default authenticated test user."""
    return user_factory(
        apple_id="test_apple_id_12345",
        email="test@example.com",
        full_name="Test User",
        reading_experience="classic",
    )


@pytest.fixture
def content_factory(db_session: Session):
    """Create persisted content rows with list/detail-friendly defaults."""
    sequence = count(1)

    def _create(**overrides: Any) -> Content:
        return create_content_row(db_session, index=next(sequence), **overrides)

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
    ):
        return create_content_status_entry_row(
            db_session,
            user=user,
            user_id=user_id,
            content=content,
            content_id=content_id,
            status=status,
            **overrides,
        )

    return _create


@pytest.fixture
def knowledge_save_factory(db_session: Session):
    """Create knowledge-save rows for a user/content pair."""

    def _create(
        *,
        user: User | None = None,
        user_id: int | None = None,
        content: Content | None = None,
        content_id: int | None = None,
        **overrides: Any,
    ):
        return create_content_knowledge_save_row(
            db_session,
            user=user,
            user_id=user_id,
            content=content,
            content_id=content_id,
            **overrides,
        )

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
    ):
        return create_content_read_status_row(
            db_session,
            user=user,
            user_id=user_id,
            content=content,
            content_id=content_id,
            **overrides,
        )

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
    ):
        return create_chat_session_row(
            db_session,
            index=next(sequence),
            user=user,
            user_id=user_id,
            content=content,
            content_id=content_id,
            **overrides,
        )

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
    ):
        return create_processing_task_row(
            db_session,
            content=content,
            content_id=content_id,
            task_type=task_type,
            payload=payload,
            status=status,
            queue_name=queue_name,
            **overrides,
        )

    return _create


@pytest.fixture
def discussion_payload_factory():
    """Create discussion payloads with stable defaults for API/service tests."""

    def _create(
        *,
        discussion_url: str,
        mode: str = "comments",
        comments: list[dict[str, Any]] | None = None,
        discussion_groups: list[dict[str, Any]] | None = None,
        links: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_comments = comments
        if resolved_comments is None and mode == "comments":
            resolved_comments = [
                {
                    "comment_id": "c1",
                    "author": "alice",
                    "text": "great",
                    "compact_text": "great",
                    "depth": 0,
                }
            ]
        if resolved_comments is None:
            resolved_comments = []

        resolved_groups = discussion_groups
        if resolved_groups is None and mode == "discussion_list":
            resolved_groups = [
                {
                    "label": "Forums",
                    "items": [
                        {
                            "title": "Hacker News",
                            "url": "https://news.ycombinator.com/item?id=123",
                        }
                    ],
                }
            ]
        if resolved_groups is None:
            resolved_groups = []

        resolved_links = links
        if resolved_links is None and mode == "comments":
            resolved_links = [{"url": "https://example.com", "source": "comment"}]
        elif resolved_links is None and mode == "discussion_list":
            resolved_links = [
                {
                    "url": "https://news.ycombinator.com/item?id=123",
                    "source": "discussion_group",
                    "group_label": "Forums",
                }
            ]
        if resolved_links is None:
            resolved_links = []

        resolved_stats = stats
        if resolved_stats is None and mode == "comments":
            resolved_stats = {"fetched_count": len(resolved_comments)}
        elif resolved_stats is None and mode == "discussion_list":
            resolved_stats = {"group_count": len(resolved_groups)}
        if resolved_stats is None:
            resolved_stats = {}

        return {
            "mode": mode,
            "source_url": discussion_url,
            "comments": resolved_comments,
            "compact_comments": [
                str(comment.get("compact_text") or comment.get("text") or "")
                for comment in resolved_comments
                if isinstance(comment, dict)
                and str(comment.get("compact_text") or comment.get("text") or "").strip()
            ],
            "discussion_groups": resolved_groups,
            "links": resolved_links,
            "summary": summary,
            "stats": resolved_stats,
        }

    return _create


@pytest.fixture
def news_item_factory(db_session: Session):
    """Create persisted news items with defaults that are visible in feed/detail APIs."""
    sequence = count(1)

    def _create(**overrides: Any):
        return create_news_item_row(db_session, index=next(sequence), **overrides)

    return _create


@pytest.fixture
def visible_news_item(news_item_factory):
    """Create a default visible representative news item."""
    return news_item_factory()


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
    ):
        return create_integration_connection_row(
            db_session,
            index=next(sequence),
            user=user,
            user_id=user_id,
            provider=provider,
            **overrides,
        )

    return _create


@pytest.fixture
def auth_headers_factory():
    """Create Authorization headers for a user."""

    def _create(user: User) -> dict[str, str]:
        assert user.id is not None
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _create


@pytest.fixture
def stub_valid_feed_url(monkeypatch):
    """Accept test feed URLs without making network calls."""

    @contextmanager
    def _runtime(**_kwargs):
        detector = SimpleNamespace(validate_feed_url=lambda url: {"feed_url": url.strip()})
        yield SimpleNamespace(detector=detector)

    monkeypatch.setattr(
        "app.services.scraper_config_validation.feed_research_runtime",
        _runtime,
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


def load_fixture(fixture_name: str) -> dict[str, Any]:
    """Load a fixture from the fixtures directory.

    Args:
        fixture_name: Name of the fixture file (without .json extension)

    Returns:
        Parsed JSON data from the fixture file
    """
    return load_json_fixture(fixture_name)


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


def _with_completed_long_form_artwork(sample: dict[str, Any]) -> dict[str, Any]:
    """Return a sample copy with generated artwork metadata for visible long-form fixtures."""
    if sample.get("status") != "completed":
        return sample
    if sample.get("content_type") not in {"article", "podcast"}:
        return sample

    normalized = deepcopy(sample)
    metadata = dict(normalized.get("content_metadata") or {})
    metadata.setdefault("image_generated_at", "2026-01-01T00:00:00Z")
    normalized["content_metadata"] = metadata
    return normalized


@pytest.fixture
def sample_article_long(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a long-form article sample."""
    return _with_completed_long_form_artwork(content_samples["article_long_form"])


@pytest.fixture
def sample_article_short(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a short technical article sample."""
    return content_samples["article_short_technical"]


@pytest.fixture
def sample_podcast(content_samples: dict[str, Any]) -> dict[str, Any]:
    """Get a podcast episode sample with full processing."""
    return _with_completed_long_form_artwork(content_samples["podcast_interview"])


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
    return build_content_row(
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
