"""Tests for content stats endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    ProcessingTask,
)
from app.models.user import User


def _add_inbox_status(db_session, user_id: int | None, content_id: int | None) -> None:
    assert user_id is not None
    assert content_id is not None
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content_id,
            status="inbox",
        )
    )


def _add_active_task(
    db_session,
    *,
    content_id: int | None,
    task_type: str = "process_content",
    status: str = "pending",
) -> None:
    assert content_id is not None
    db_session.add(
        ProcessingTask(
            task_type=task_type,
            content_id=content_id,
            status=status,
            queue_name="content",
            payload={},
        )
    )


def test_processing_count_includes_news_and_new_status(client, db_session, test_user) -> None:
    other_user = User(
        apple_id="other_apple_id",
        email="other@example.com",
        full_name="Other User",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    pending_article = Content(
        url="https://example.com/article-1",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    processing_podcast = Content(
        url="https://example.com/podcast-1",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    pending_youtube = Content(
        url="https://youtube.com/watch?v=abc123",
        content_type=ContentType.UNKNOWN.value,
        platform="youtube",
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    pending_news = Content(
        url="https://example.com/news-1",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    pending_youtube_news = Content(
        url="https://example.com/news-youtube",
        content_type=ContentType.NEWS.value,
        platform="youtube",
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    completed_article = Content(
        url="https://example.com/article-2",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    pending_article_no_inbox = Content(
        url="https://example.com/article-3",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    queued_news = Content(
        url="https://example.com/news-queued",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.NEW.value,
        content_metadata={},
    )

    db_session.add_all(
        [
            pending_article,
            processing_podcast,
            pending_youtube,
            pending_news,
            pending_youtube_news,
            completed_article,
            pending_article_no_inbox,
            queued_news,
        ]
    )
    db_session.commit()
    for content in (
        pending_article,
        processing_podcast,
        pending_youtube,
        pending_news,
        pending_youtube_news,
        completed_article,
        pending_article_no_inbox,
        queued_news,
    ):
        db_session.refresh(content)

    _add_inbox_status(db_session, test_user.id, pending_article.id)
    _add_inbox_status(db_session, test_user.id, processing_podcast.id)
    _add_inbox_status(db_session, test_user.id, pending_youtube.id)
    _add_inbox_status(db_session, test_user.id, pending_news.id)
    _add_inbox_status(db_session, test_user.id, pending_youtube_news.id)
    _add_inbox_status(db_session, test_user.id, completed_article.id)
    _add_inbox_status(db_session, test_user.id, queued_news.id)
    _add_inbox_status(db_session, other_user.id, pending_article_no_inbox.id)
    _add_active_task(db_session, content_id=pending_article.id)
    _add_active_task(db_session, content_id=pending_youtube.id)
    _add_active_task(db_session, content_id=pending_news.id)
    _add_active_task(db_session, content_id=pending_youtube_news.id)
    _add_active_task(db_session, content_id=queued_news.id)
    _add_active_task(db_session, content_id=pending_article_no_inbox.id)
    processing_podcast.checked_out_by = "content-processor-1"
    processing_podcast.checked_out_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.add_all(
        [
            NewsItem(
                ingest_key="processing-news-1",
                visibility_scope="global",
                source_type="hackernews",
                status="new",
                ingested_at=datetime.now(UTC).replace(tzinfo=None),
            ),
            NewsItem(
                ingest_key="processing-news-2",
                visibility_scope="user",
                owner_user_id=test_user.id,
                user_scraper_config_id=10,
                source_type="reddit",
                status="processing",
                ingested_at=datetime.now(UTC).replace(tzinfo=None),
            ),
            NewsItem(
                ingest_key="processing-news-3",
                visibility_scope="global",
                source_type="reddit",
                status="new",
                ingested_at=datetime.now(UTC).replace(tzinfo=None),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/content/stats/processing-count")
    assert response.status_code == 200
    payload = response.json()

    assert payload["long_form_count"] == 3
    assert payload["news_count"] == 1
    assert payload["processing_count"] == 4


def test_processing_count_excludes_orphaned_stale_rows(client, db_session, test_user) -> None:
    stale_processing = Content(
        url="https://example.com/stale-processing",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5),
        content_metadata={},
    )
    stale_pending = Content(
        url="https://example.com/stale-pending",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.PENDING.value,
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3),
        content_metadata={},
    )
    db_session.add_all([stale_processing, stale_pending])
    db_session.commit()
    db_session.refresh(stale_processing)
    db_session.refresh(stale_pending)

    _add_inbox_status(db_session, test_user.id, stale_processing.id)
    _add_inbox_status(db_session, test_user.id, stale_pending.id)
    db_session.commit()

    response = client.get("/api/content/stats/processing-count")
    assert response.status_code == 200
    payload = response.json()

    assert payload["long_form_count"] == 0
    assert payload["news_count"] == 0
    assert payload["processing_count"] == 0


def test_long_form_stats_counts(client, db_session, test_user) -> None:
    other_user = User(
        apple_id="other_user_apple_id",
        email="other@example.com",
        full_name="Other User",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    completed_article_unread = Content(
        url="https://example.com/article-unread",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_podcast_read = Content(
        url="https://example.com/podcast-read",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_article_extra = Content(
        url="https://example.com/article-extra",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_youtube = Content(
        url="https://youtube.com/watch?v=xyz",
        content_type=ContentType.UNKNOWN.value,
        platform="youtube",
        status=ContentStatus.COMPLETED.value,
        content_metadata={"image_generated_at": "2026-01-01T00:00:00Z"},
    )
    completed_news = Content(
        url="https://example.com/news",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    processing_article = Content(
        url="https://example.com/article-processing",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PROCESSING.value,
        content_metadata={},
    )
    pending_podcast = Content(
        url="https://example.com/podcast-pending",
        content_type=ContentType.PODCAST.value,
        status=ContentStatus.PENDING.value,
        content_metadata={},
    )
    completed_other_user = Content(
        url="https://example.com/article-other",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )

    db_session.add_all(
        [
            completed_article_unread,
            completed_podcast_read,
            completed_article_extra,
            completed_youtube,
            completed_news,
            processing_article,
            pending_podcast,
            completed_other_user,
        ]
    )
    db_session.commit()
    for content in (
        completed_article_unread,
        completed_podcast_read,
        completed_article_extra,
        completed_youtube,
        completed_news,
        processing_article,
        pending_podcast,
        completed_other_user,
    ):
        db_session.refresh(content)

    _add_inbox_status(db_session, test_user.id, completed_article_unread.id)
    _add_inbox_status(db_session, test_user.id, completed_podcast_read.id)
    _add_inbox_status(db_session, test_user.id, completed_article_extra.id)
    _add_inbox_status(db_session, test_user.id, completed_youtube.id)
    _add_inbox_status(db_session, test_user.id, completed_news.id)
    _add_inbox_status(db_session, test_user.id, processing_article.id)
    _add_inbox_status(db_session, test_user.id, pending_podcast.id)
    _add_inbox_status(db_session, other_user.id, completed_other_user.id)
    _add_active_task(db_session, content_id=pending_podcast.id)
    processing_article.checked_out_by = "content-processor-2"
    processing_article.checked_out_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.commit()

    db_session.add(
        ContentReadStatus(
            user_id=test_user.id,
            content_id=completed_podcast_read.id,
        )
    )
    db_session.commit()

    response = client.get("/api/content/stats/long-form")
    assert response.status_code == 200
    payload = response.json()

    assert payload["unread_count"] == 3


def test_long_form_stats_count_completed_long_form_without_generated_artwork_metadata(
    client,
    db_session,
    test_user,
) -> None:
    metadata_free_article = Content(
        url="https://example.com/article-awaiting-art",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": {
                "title": "Awaiting Art",
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
        },
    )
    visible_article = Content(
        url="https://example.com/article-visible-art",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
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
            "image_generated_at": "2026-01-01T00:00:00Z",
        },
    )

    db_session.add_all([metadata_free_article, visible_article])
    db_session.commit()
    db_session.refresh(metadata_free_article)
    db_session.refresh(visible_article)

    _add_inbox_status(db_session, test_user.id, metadata_free_article.id)
    _add_inbox_status(db_session, test_user.id, visible_article.id)
    db_session.commit()

    response = client.get("/api/content/stats/long-form")
    assert response.status_code == 200
    payload = response.json()

    assert payload["unread_count"] == 2


def test_unread_counts_use_visible_news_items(client, db_session, test_user) -> None:
    news_item = NewsItem(
        ingest_key="news-unread",
        visibility_scope="global",
        source_type="hackernews",
        status="ready",
        article_title="News unread",
        summary_title="News unread",
        summary_text="Summary",
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(news_item)
    db_session.commit()
    db_session.refresh(news_item)

    response = client.get("/api/content/stats/unread-counts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["news"] == 1


def test_unread_counts_prefer_user_scoped_scraper_news_when_available(
    client,
    db_session,
    test_user,
) -> None:
    db_session.add(
        NewsItem(
            ingest_key="global-news-unread",
            visibility_scope="global",
            source_type="hackernews",
            status="ready",
            article_title="Global unread",
            summary_title="Global unread",
            summary_text="Summary",
            ingested_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.add(
        NewsItem(
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
            ingested_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.commit()

    response = client.get("/api/content/stats/unread-counts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["news"] == 1
