"""Tests for news-native ingestion and backfill helpers."""

from datetime import UTC, datetime

from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.schema import Content, NewsItem
from app.services.news_ingestion import (
    backfill_news_items_from_contents,
    build_news_item_upsert_input_from_content,
)


def test_build_news_item_upsert_input_from_content_infers_user_scope() -> None:
    content = Content(
        id=42,
        content_type="news",
        url="https://x.com/i/status/123#newsly",
        source_url="https://x.com/i/status/123",
        title="Foundry supply chain tightens again",
        source="X",
        platform="twitter",
        status="completed",
        content_metadata={
            "digest_visibility": "digest_only",
            "submitted_by_user_id": 7,
            "tweet_id": "123",
            "tweet_url": "https://x.com/i/status/123",
            "summary": {
                "title": "TSMC packaging stays constrained",
                "article_url": "https://x.com/i/status/123",
                "key_points": ["Packaging demand remains tight."],
                "summary": "Capex and packaging constraints remain the core story.",
            },
        },
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    payload = build_news_item_upsert_input_from_content(content)

    assert payload is not None
    assert payload.visibility_scope == NewsItemVisibilityScope.USER
    assert payload.owner_user_id == 7
    assert payload.article_url == "https://x.com/i/status/123"
    assert payload.summary_title == "TSMC packaging stays constrained"
    assert payload.status == NewsItemStatus.READY
    assert payload.legacy_content_id == 42


def test_backfill_news_items_from_contents_is_idempotent(db_session) -> None:
    content = Content(
        content_type="news",
        url="https://example.com/story",
        source_url="https://news.ycombinator.com/item?id=1",
        title="Example story",
        source="example.com",
        platform="hackernews",
        status="completed",
        content_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=1",
            "article": {
                "url": "https://example.com/story",
                "title": "Example story",
                "source_domain": "example.com",
            },
            "summary": {
                "title": "Example story",
                "article_url": "https://example.com/story",
                "key_points": ["A concise point."],
                "summary": "A short summary.",
            },
        },
    )
    db_session.add(content)
    db_session.commit()

    first = backfill_news_items_from_contents(db_session)
    db_session.commit()
    second = backfill_news_items_from_contents(db_session)
    db_session.commit()

    news_items = db_session.query(NewsItem).all()
    assert len(news_items) == 1
    assert news_items[0].legacy_content_id == content.id
    assert first.created == 1
    assert second.skipped == 1


def test_backfill_news_items_from_contents_can_target_specific_content_ids(db_session) -> None:
    first_content = Content(
        content_type="news",
        url="https://example.com/story-1",
        source_url="https://news.ycombinator.com/item?id=1",
        title="Example story one",
        source="example.com",
        platform="hackernews",
        status="completed",
        content_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=1",
            "article": {
                "url": "https://example.com/story-1",
                "title": "Example story one",
                "source_domain": "example.com",
            },
            "summary": {
                "title": "Example story one",
                "article_url": "https://example.com/story-1",
                "key_points": ["Point one."],
                "summary": "Summary one.",
            },
        },
    )
    second_content = Content(
        content_type="news",
        url="https://example.com/story-2",
        source_url="https://news.ycombinator.com/item?id=2",
        title="Example story two",
        source="example.com",
        platform="hackernews",
        status="completed",
        content_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=2",
            "article": {
                "url": "https://example.com/story-2",
                "title": "Example story two",
                "source_domain": "example.com",
            },
            "summary": {
                "title": "Example story two",
                "article_url": "https://example.com/story-2",
                "key_points": ["Point two."],
                "summary": "Summary two.",
            },
        },
    )
    db_session.add_all([first_content, second_content])
    db_session.commit()

    result = backfill_news_items_from_contents(
        db_session,
        content_ids=[second_content.id],
    )
    db_session.commit()

    news_items = db_session.query(NewsItem).order_by(NewsItem.legacy_content_id.asc()).all()
    assert result.created == 1
    assert [item.legacy_content_id for item in news_items] == [second_content.id]
