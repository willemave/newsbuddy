"""Tests for content stats endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.contracts import ContentStatus, ContentType


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _content(
    content_factory,
    *,
    url: str,
    content_type: ContentType = ContentType.ARTICLE,
    status: ContentStatus = ContentStatus.COMPLETED,
    content_metadata: dict[str, Any] | None = None,
    **overrides: Any,
):
    return content_factory(
        url=url,
        content_type=content_type,
        status=status,
        content_metadata=content_metadata or {},
        **overrides,
    )


def _add_inbox_status(status_entry_factory, *, user, content) -> None:
    status_entry_factory(user=user, content=content, status="inbox")


def _add_active_task(
    processing_task_factory,
    *,
    content,
    task_type: str = "process_content",
    status: str = "pending",
) -> None:
    processing_task_factory(content=content, task_type=task_type, status=status)


def _long_form_metadata(*, with_artwork: bool) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "summary": {
            "title": "Visible Art",
            "overview": (
                "This overview is long enough to satisfy the minimum length requirement "
                "for structured summaries."
            ),
            "bullet_points": [
                {"text": "Key point one", "category": "key_finding"},
                {"text": "Key point two", "category": "methodology"},
                {"text": "Key point three", "category": "conclusion"},
            ],
            "quotes": [],
            "topics": ["Testing"],
        },
        "summary_kind": "long_structured",
        "summary_version": 1,
    }
    if with_artwork:
        metadata["image_generated_at"] = "2026-01-01T00:00:00Z"
    return metadata


def test_processing_count_includes_news_and_new_status(
    client,
    db_session,
    test_user,
    user_factory,
    content_factory,
    status_entry_factory,
    processing_task_factory,
    news_item_factory,
) -> None:
    other_user = user_factory(
        apple_id="other_apple_id",
        email="other@example.com",
        full_name="Other User",
    )

    pending_article = _content(
        content_factory,
        url="https://example.com/article-1",
        content_type=ContentType.ARTICLE,
        status=ContentStatus.PENDING,
    )
    processing_podcast = _content(
        content_factory,
        url="https://example.com/podcast-1",
        content_type=ContentType.PODCAST,
        status=ContentStatus.PROCESSING,
    )
    pending_youtube = _content(
        content_factory,
        url="https://youtube.com/watch?v=abc123",
        content_type=ContentType.UNKNOWN,
        platform="youtube",
        status=ContentStatus.PENDING,
    )
    pending_news = _content(
        content_factory,
        url="https://example.com/news-1",
        content_type=ContentType.NEWS,
        status=ContentStatus.PENDING,
    )
    pending_youtube_news = _content(
        content_factory,
        url="https://example.com/news-youtube",
        content_type=ContentType.NEWS,
        platform="youtube",
        status=ContentStatus.PENDING,
    )
    completed_article = _content(
        content_factory,
        url="https://example.com/article-2",
    )
    pending_article_no_inbox = _content(
        content_factory,
        url="https://example.com/article-3",
        status=ContentStatus.PENDING,
    )
    queued_news = _content(
        content_factory,
        url="https://example.com/news-queued",
        content_type=ContentType.NEWS,
        status=ContentStatus.NEW,
    )

    for content in (
        pending_article,
        processing_podcast,
        pending_youtube,
        pending_news,
        pending_youtube_news,
        completed_article,
        queued_news,
    ):
        _add_inbox_status(status_entry_factory, user=test_user, content=content)
    _add_inbox_status(status_entry_factory, user=other_user, content=pending_article_no_inbox)

    for content in (
        pending_article,
        pending_youtube,
        pending_news,
        pending_youtube_news,
        queued_news,
        pending_article_no_inbox,
    ):
        _add_active_task(processing_task_factory, content=content)

    processing_podcast.checked_out_by = "content-processor-1"
    processing_podcast.checked_out_at = _now()

    news_item_factory(
        ingest_key="processing-news-1",
        status="new",
        ingested_at=_now(),
    )
    news_item_factory(
        ingest_key="processing-news-2",
        visibility_scope="user",
        owner_user_id=test_user.id,
        user_scraper_config_id=10,
        source_type="reddit",
        status="processing",
        ingested_at=_now(),
    )
    news_item_factory(
        ingest_key="processing-news-3",
        source_type="reddit",
        status="new",
        ingested_at=_now(),
    )
    db_session.commit()

    response = client.get("/api/content/stats/processing-count")
    assert response.status_code == 200
    payload = response.json()

    assert payload["long_form_count"] == 3
    assert payload["news_count"] == 1
    assert payload["processing_count"] == 4


def test_processing_count_excludes_orphaned_stale_rows(
    client,
    test_user,
    content_factory,
    status_entry_factory,
) -> None:
    stale_processing = _content(
        content_factory,
        url="https://example.com/stale-processing",
        status=ContentStatus.PROCESSING,
        created_at=_now() - timedelta(days=5),
    )
    stale_pending = _content(
        content_factory,
        url="https://example.com/stale-pending",
        content_type=ContentType.PODCAST,
        status=ContentStatus.PENDING,
        created_at=_now() - timedelta(days=3),
    )

    _add_inbox_status(status_entry_factory, user=test_user, content=stale_processing)
    _add_inbox_status(status_entry_factory, user=test_user, content=stale_pending)

    response = client.get("/api/content/stats/processing-count")
    assert response.status_code == 200
    payload = response.json()

    assert payload["long_form_count"] == 0
    assert payload["news_count"] == 0
    assert payload["processing_count"] == 0


def test_long_form_stats_counts(
    client,
    db_session,
    test_user,
    user_factory,
    content_factory,
    status_entry_factory,
    processing_task_factory,
    read_status_factory,
) -> None:
    other_user = user_factory(
        apple_id="other_user_apple_id",
        email="other@example.com",
        full_name="Other User",
    )

    completed_article_unread = _content(
        content_factory,
        url="https://example.com/article-unread",
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_podcast_read = _content(
        content_factory,
        url="https://example.com/podcast-read",
        content_type=ContentType.PODCAST,
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_article_extra = _content(
        content_factory,
        url="https://example.com/article-extra",
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_youtube = _content(
        content_factory,
        url="https://youtube.com/watch?v=xyz",
        content_type=ContentType.UNKNOWN,
        platform="youtube",
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_news = _content(
        content_factory,
        url="https://example.com/news",
        content_type=ContentType.NEWS,
    )
    processing_article = _content(
        content_factory,
        url="https://example.com/article-processing",
        status=ContentStatus.PROCESSING,
    )
    pending_podcast = _content(
        content_factory,
        url="https://example.com/podcast-pending",
        content_type=ContentType.PODCAST,
        status=ContentStatus.PENDING,
    )
    completed_other_user = _content(
        content_factory,
        url="https://example.com/article-other",
    )

    for content in (
        completed_article_unread,
        completed_podcast_read,
        completed_article_extra,
        completed_youtube,
        completed_news,
        processing_article,
        pending_podcast,
    ):
        _add_inbox_status(status_entry_factory, user=test_user, content=content)
    _add_inbox_status(status_entry_factory, user=other_user, content=completed_other_user)

    _add_active_task(processing_task_factory, content=pending_podcast)
    processing_article.checked_out_by = "content-processor-2"
    processing_article.checked_out_at = _now()
    read_status_factory(user=test_user, content=completed_podcast_read)
    db_session.commit()

    response = client.get("/api/content/stats/long-form")
    assert response.status_code == 200
    payload = response.json()

    assert payload["unread_count"] == 3


def test_long_form_stats_count_completed_long_form_without_generated_artwork_metadata(
    client,
    test_user,
    content_factory,
    status_entry_factory,
) -> None:
    metadata_free_article = _content(
        content_factory,
        url="https://example.com/article-awaiting-art",
        content_metadata=_long_form_metadata(with_artwork=False),
    )
    visible_article = _content(
        content_factory,
        url="https://example.com/article-visible-art",
        content_metadata=_long_form_metadata(with_artwork=True),
    )

    _add_inbox_status(status_entry_factory, user=test_user, content=metadata_free_article)
    _add_inbox_status(status_entry_factory, user=test_user, content=visible_article)

    response = client.get("/api/content/stats/long-form")
    assert response.status_code == 200
    payload = response.json()

    assert payload["unread_count"] == 2


def test_unread_counts_use_visible_news_items(client, news_item_factory) -> None:
    news_item_factory(
        ingest_key="news-unread",
        status="ready",
        article_title="News unread",
        summary_title="News unread",
        summary_text="Summary",
        ingested_at=_now(),
    )

    response = client.get("/api/content/stats/unread-counts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["news"] == 1


def test_unread_counts_prefer_user_scoped_scraper_news_when_available(
    client,
    test_user,
    news_item_factory,
) -> None:
    news_item_factory(
        ingest_key="global-news-unread",
        status="ready",
        article_title="Global unread",
        summary_title="Global unread",
        summary_text="Summary",
        ingested_at=_now(),
    )
    news_item_factory(
        ingest_key="user-news-unread",
        visibility_scope="user",
        owner_user_id=test_user.id,
        user_scraper_config_id=10,
        source_type="reddit",
        source_label="creativecoding",
        status="ready",
        article_title="User unread",
        summary_title="User unread",
        summary_text="Summary",
        ingested_at=_now(),
    )

    response = client.get("/api/content/stats/unread-counts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["news"] == 1
