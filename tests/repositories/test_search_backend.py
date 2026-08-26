"""Tests for content search ranking."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ContentStatusEntry
from app.repositories.content_search_expressions import (
    CONTENT_SEARCH_DOCUMENT_SQL,
    content_search_document_expression,
    content_search_expressions,
)
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


def test_content_search_index_sql_covers_the_canonical_weighted_document() -> None:
    normalized = " ".join(CONTENT_SEARCH_DOCUMENT_SQL.split())

    assert "content_metadata -> 'summary' ->> 'title'" in normalized
    assert "'A'" in normalized
    assert "COALESCE(title, '')" in normalized
    assert "'B'" in normalized
    assert "COALESCE(source, '')" in normalized
    assert "'C'" in normalized
    assert "COALESCE(search_text, '')" in normalized
    assert "'D'" in normalized


def test_runtime_search_document_is_the_canonical_index_expression() -> None:
    assert " ".join(str(content_search_document_expression()).split()) == " ".join(
        CONTENT_SEARCH_DOCUMENT_SQL.split()
    )


def test_postgres_search_predicate_uses_search_indexes(db_session) -> None:
    """The exact runtime predicate must remain indexable, including typo matching."""
    db_session.execute(
        text(
            "CREATE INDEX test_contents_search_document_gin "
            f"ON contents USING GIN ({CONTENT_SEARCH_DOCUMENT_SQL})"
        )
    )
    db_session.execute(
        text(
            "CREATE INDEX test_contents_summary_title_trgm ON contents USING GIN "
            "((COALESCE(content_metadata -> 'summary' ->> 'title', '')) "
            "public.gin_trgm_ops)"
        )
    )
    db_session.execute(
        text(
            "CREATE INDEX test_contents_title_trgm "
            "ON contents USING GIN (title public.gin_trgm_ops)"
        )
    )
    db_session.execute(
        text(
            "CREATE INDEX test_contents_source_trgm "
            "ON contents USING GIN (source public.gin_trgm_ops)"
        )
    )
    db_session.execute(text("SET LOCAL enable_seqscan = off"))

    statement = select(Content.id).where(content_search_expressions("framwork").matches)
    compiled = statement.compile(dialect=db_session.bind.dialect)
    raw_connection = db_session.connection().connection.driver_connection
    with raw_connection.cursor() as cursor:
        cursor.execute("EXPLAIN " + str(compiled), compiled.params)
        plan = "\n".join(row[0] for row in cursor.fetchall())

    assert "Seq Scan" not in plan
    assert "BitmapOr" in plan
    assert "test_contents_search_document_gin" in plan
    assert "test_contents_summary_title_trgm" in plan
    assert "test_contents_title_trgm" in plan
    assert "test_contents_source_trgm" in plan
