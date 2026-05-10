"""Tests for content search ranking."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentStatusEntry
from app.repositories.search_repository import (
    search_content_page,
)


def _add_inbox_content(
    db_session,
    user_id: int,
    *,
    title: str,
    search_text: str,
    summary_title: str | None = None,
) -> Content:
    """Create a searchable inbox item."""
    resolved_summary_title = summary_title or title
    content = Content(
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        title=title,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        search_text=search_text,
        content_metadata={
            "summary": {
                "title": resolved_summary_title,
                "overview": f"{title} overview",
                "bullet_points": [],
                "quotes": [],
                "topics": [],
                "classification": "to_read",
            },
            "summary_kind": "long_structured",
            "summary_version": 1,
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
        created_at=datetime.now(UTC),
    )
    db_session.add(content)
    db_session.flush()
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content.id,
            status="inbox",
        )
    )
    db_session.commit()
    db_session.refresh(content)
    return content


def test_postgres_search_ranks_title_matches_before_body_matches(db_session, test_user) -> None:
    """Title matches should outrank body-only matches under the native backend."""
    title_match = _add_inbox_content(
        db_session,
        test_user.id,
        title="Framework release notes",
        search_text="brief update",
    )
    body_match = _add_inbox_content(
        db_session,
        test_user.id,
        title="Unrelated notes",
        search_text="framework release notes with implementation detail",
    )

    rows = search_content_page(
        db_session,
        user_id=test_user.id,
        query_text="framework",
        content_type="all",
        cursor=(None, None, None),
        limit=10,
        offset=0,
    )

    assert [row[0].id for row in rows[:2]] == [title_match.id, body_match.id]
    assert rows[0][3] is not None
    assert rows[1][3] is not None
    assert rows[0][3] > rows[1][3]


def test_postgres_search_handles_typo_with_trigram_when_available(db_session, test_user) -> None:
    """Typo-tolerant fallback should return title matches."""
    typo_match = _add_inbox_content(
        db_session,
        test_user.id,
        title="Framework release notes",
        search_text="brief update",
    )

    rows = search_content_page(
        db_session,
        user_id=test_user.id,
        query_text="framwork",
        content_type="all",
        cursor=(None, None, None),
        limit=10,
        offset=0,
    )

    assert rows
    assert rows[0][0].id == typo_match.id


def test_postgres_search_matches_summary_title_metadata(db_session, test_user) -> None:
    """Summary metadata titles should be searchable even when the stored title differs."""
    metadata_match = _add_inbox_content(
        db_session,
        test_user.id,
        title="Original page headline",
        summary_title="Canonical AI systems overview",
        search_text="brief update",
    )

    rows = search_content_page(
        db_session,
        user_id=test_user.id,
        query_text="canonical systems",
        content_type="all",
        cursor=(None, None, None),
        limit=10,
        offset=0,
    )

    assert rows
    assert rows[0][0].id == metadata_match.id
