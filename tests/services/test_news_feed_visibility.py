"""Visibility-filter and feed-cap tests for ``app.services.news_feed``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.constants import (
    AGGREGATOR_FEED_URL_PREFIX,
    AGGREGATOR_SCRAPER_TYPE,
)
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.db import NewsItem, NewsItemReadStatus, UserScraperConfig
from app.services.news_feed import (
    count_unread_news_items,
    list_unread_visible_news_items,
    list_visible_news_items,
)


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _global_news_item(
    db_session,
    *,
    ingest_key: str,
    platform: str,
    title: str,
    article_url: str,
    ingested_at: datetime,
    aggregator_topic: str | None = None,
    source_type: str | None = None,
) -> NewsItem:
    metadata: dict[str, Any] = {
        "platform": platform,
        "aggregator": {
            "key": platform,
            "name": platform.title(),
        },
    }
    if aggregator_topic is not None:
        metadata["aggregator"]["topic"] = aggregator_topic

    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope=NewsItemVisibilityScope.GLOBAL.value,
        platform=platform,
        source_type=source_type or platform,
        source_label=platform.title(),
        source_external_id=ingest_key,
        canonical_story_url=article_url,
        article_url=article_url,
        article_title=title,
        article_domain="example.com",
        summary_title=title,
        summary_key_points=[f"{title} bullet"],
        summary_text=f"{title} summary",
        raw_metadata=metadata,
        status=NewsItemStatus.READY.value,
        ingested_at=_utc_naive(ingested_at),
        processed_at=_utc_naive(ingested_at),
    )
    db_session.add(item)
    db_session.flush()
    return item


def _add_aggregator_subscription(
    db_session,
    *,
    user_id: int,
    key: str,
    topics: list[str] | None = None,
) -> UserScraperConfig:
    config: dict[str, Any] = {"key": key}
    if topics is not None:
        config["topics"] = topics
    row = UserScraperConfig(
        user_id=user_id,
        scraper_type=AGGREGATOR_SCRAPER_TYPE,
        display_name=key,
        feed_url=f"{AGGREGATOR_FEED_URL_PREFIX}{key}",
        config=config,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _user_news_item(
    db_session,
    *,
    user_id: int,
    ingest_key: str,
    platform: str,
    title: str,
    article_url: str,
    ingested_at: datetime,
    source_type: str | None = None,
) -> NewsItem:
    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope=NewsItemVisibilityScope.USER.value,
        owner_user_id=user_id,
        platform=platform,
        source_type=source_type or platform,
        source_label=platform.title(),
        source_external_id=ingest_key,
        canonical_story_url=article_url,
        article_url=article_url,
        article_title=title,
        article_domain="example.com",
        summary_title=title,
        summary_key_points=[f"{title} bullet"],
        summary_text=f"{title} summary",
        raw_metadata={"platform": platform},
        status=NewsItemStatus.READY.value,
        ingested_at=_utc_naive(ingested_at),
        processed_at=_utc_naive(ingested_at),
    )
    db_session.add(item)
    db_session.flush()
    return item


@pytest.fixture
def base_time() -> datetime:
    return datetime(2026, 4, 22, 12, 0, tzinfo=UTC)


def test_visibility_filters_global_items_to_user_aggregator_picks(
    db_session, test_user, base_time
) -> None:
    user_id = test_user.id
    assert user_id is not None

    _global_news_item(
        db_session,
        ingest_key="hn-1",
        platform="hackernews",
        title="HN story",
        article_url="https://example.com/hn",
        ingested_at=base_time,
    )
    _global_news_item(
        db_session,
        ingest_key="techmeme-1",
        platform="techmeme",
        title="Techmeme story",
        article_url="https://example.com/techmeme",
        ingested_at=base_time + timedelta(minutes=1),
    )
    db_session.commit()

    assert count_unread_news_items(db_session, user_id=user_id) == 0

    _add_aggregator_subscription(db_session, user_id=user_id, key="hackernews")

    response = list_visible_news_items(
        db_session, user_id=user_id, read_filter="all", cursor=None, limit=25
    )
    titles = [item.title for item in response.contents]
    assert titles == ["HN story"]


def test_visibility_ignores_reddit_aggregator_subscription(
    db_session, test_user, base_time
) -> None:
    user_id = test_user.id
    assert user_id is not None

    _global_news_item(
        db_session,
        ingest_key="global-reddit",
        platform="reddit",
        title="Global Reddit story",
        article_url="https://example.com/reddit",
        ingested_at=base_time,
        source_type="reddit",
    )
    user_item = _user_news_item(
        db_session,
        user_id=user_id,
        ingest_key="user-reddit",
        platform="reddit",
        title="User Reddit story",
        article_url="https://example.com/user-reddit",
        ingested_at=base_time + timedelta(minutes=1),
        source_type="reddit",
    )
    _add_aggregator_subscription(db_session, user_id=user_id, key="reddit")

    response = list_visible_news_items(
        db_session, user_id=user_id, read_filter="all", cursor=None, limit=25
    )

    assert [item.id for item in response.contents] == [user_item.id]


def test_list_unread_visible_news_items_uses_visible_unread_rows(
    db_session, test_user, base_time
) -> None:
    user_id = test_user.id
    assert user_id is not None

    visible_unread = _global_news_item(
        db_session,
        ingest_key="hn-unread",
        platform="hackernews",
        title="Visible unread story",
        article_url="https://example.com/unread",
        ingested_at=base_time + timedelta(minutes=2),
    )
    visible_read = _global_news_item(
        db_session,
        ingest_key="hn-read",
        platform="hackernews",
        title="Visible read story",
        article_url="https://example.com/read",
        ingested_at=base_time + timedelta(minutes=1),
    )
    _global_news_item(
        db_session,
        ingest_key="techmeme-unread",
        platform="techmeme",
        title="Hidden unread story",
        article_url="https://example.com/hidden",
        ingested_at=base_time,
    )
    _add_aggregator_subscription(db_session, user_id=user_id, key="hackernews")
    db_session.add(NewsItemReadStatus(user_id=user_id, news_item_id=visible_read.id))
    db_session.commit()

    rows, total = list_unread_visible_news_items(
        db_session,
        user_id=user_id,
        limit=10,
    )

    assert total == 1
    assert [item.id for item in rows] == [visible_unread.id]


def test_list_unread_visible_news_items_can_return_all_visible_unread_rows(
    db_session, test_user, base_time
) -> None:
    user_id = test_user.id
    assert user_id is not None

    item_count = 225
    for i in range(item_count):
        _global_news_item(
            db_session,
            ingest_key=f"hn-unread-all-{i:03d}",
            platform="hackernews",
            title=f"Unread story {i}",
            article_url=f"https://example.com/unread-all-{i}",
            ingested_at=base_time + timedelta(minutes=i),
        )
    _add_aggregator_subscription(db_session, user_id=user_id, key="hackernews")

    rows, total = list_unread_visible_news_items(
        db_session,
        user_id=user_id,
        limit=None,
    )

    assert total == item_count
    assert len(rows) == item_count
    assert rows[0].summary_title == f"Unread story {item_count - 1}"
    assert rows[-1].summary_title == "Unread story 0"


def test_visibility_filters_brutalist_topics(db_session, test_user, base_time) -> None:
    user_id = test_user.id
    assert user_id is not None

    _global_news_item(
        db_session,
        ingest_key="brut-science",
        platform="brutalist",
        title="Brutalist science",
        article_url="https://example.com/sci",
        ingested_at=base_time,
        aggregator_topic="science",
    )
    _global_news_item(
        db_session,
        ingest_key="brut-sports",
        platform="brutalist",
        title="Brutalist sports",
        article_url="https://example.com/sport",
        ingested_at=base_time + timedelta(minutes=1),
        aggregator_topic="sports",
    )
    _add_aggregator_subscription(db_session, user_id=user_id, key="brutalist", topics=["science"])

    response = list_visible_news_items(
        db_session, user_id=user_id, read_filter="all", cursor=None, limit=25
    )
    titles = [item.title for item in response.contents]
    assert titles == ["Brutalist science"]


def test_visibility_paginates_all_visible_feed_items(db_session, test_user, base_time) -> None:
    user_id = test_user.id
    assert user_id is not None

    item_count = 105
    for i in range(item_count):
        _global_news_item(
            db_session,
            ingest_key=f"hn-{i:03d}",
            platform="hackernews",
            title=f"Story {i}",
            article_url=f"https://example.com/story-{i}",
            ingested_at=base_time + timedelta(minutes=i),
        )
    _add_aggregator_subscription(db_session, user_id=user_id, key="hackernews")

    assert count_unread_news_items(db_session, user_id=user_id) == item_count

    fetched_titles: list[str | None] = []
    cursor: str | None = None
    first_page_total: int | None = None
    while True:
        response = list_visible_news_items(
            db_session,
            user_id=user_id,
            read_filter="all",
            cursor=cursor,
            limit=25,
        )
        if first_page_total is None:
            first_page_total = response.meta.total
        fetched_titles.extend(item.title for item in response.contents)
        cursor = response.meta.next_cursor
        if not cursor:
            break

    assert first_page_total == item_count
    assert len(fetched_titles) == item_count
    # Most-recent-first: pagination reaches the oldest visible item.
    assert fetched_titles[0] == f"Story {item_count - 1}"
    assert fetched_titles[-1] == "Story 0"
