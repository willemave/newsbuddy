"""Tests for news-native ingestion and backfill helpers."""

from datetime import UTC, datetime

from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.metadata import ContentType
from app.models.schema import Content, NewsItem
from app.services.news_ingestion import (
    backfill_news_items_from_contents,
    build_news_item_upsert_input_from_content,
    build_news_item_upsert_input_from_scraped_item,
    upsert_news_item,
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


def test_build_news_item_upsert_input_from_scraped_item_requires_real_summary() -> None:
    payload = build_news_item_upsert_input_from_scraped_item(
        {
            "url": "https://example.com/story",
            "title": "Example story",
            "content_type": ContentType.NEWS,
            "metadata": {
                "platform": "reddit",
                "source": "example_subreddit",
                "source_type": "reddit",
                "source_label": "example_subreddit",
                "article": {
                    "url": "https://example.com/story",
                    "title": "Example story",
                    "source_domain": "example.com",
                },
                "aggregator": {
                    "name": "Reddit",
                    "title": "Example story",
                    "external_id": "abc123",
                    "metadata": {
                        "score": 1,
                        "comments_count": 0,
                    },
                },
                "discussion_url": "https://reddit.com/r/example/comments/abc123/example_story/",
            },
        }
    )

    assert payload.summary_title == "Example story"
    assert payload.summary_key_points == []
    assert payload.summary_text is None
    assert payload.status == NewsItemStatus.NEW


def test_build_news_item_upsert_input_from_content_requires_real_summary() -> None:
    content = Content(
        id=43,
        content_type="news",
        url="https://example.com/story",
        source_url="https://news.ycombinator.com/item?id=43",
        title="Example story",
        source="example.com",
        platform="hackernews",
        status="completed",
        content_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=43",
            "article": {
                "url": "https://example.com/story",
                "title": "Example story",
                "source_domain": "example.com",
            },
            "summary": {
                "title": "Example story",
                "article_url": "https://example.com/story",
                "key_points": [],
                "summary": None,
            },
        },
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )

    payload = build_news_item_upsert_input_from_content(content)

    assert payload is not None
    assert payload.summary_title == "Example story"
    assert payload.summary_key_points == []
    assert payload.summary_text is None
    assert payload.status == NewsItemStatus.NEW


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


def test_upsert_news_item_ignores_volatile_scrape_metadata_for_identity(db_session) -> None:
    first_item = {
        "url": (
            "https://the-decoder.com/"
            "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
            "superintelligence-without-llms"
        ),
        "title": (
            "DeepMind veteran David Silver raises $1B, bets on radically new "
            "type of Reinforcement Learning to build superintelligence"
        ),
        "content_type": ContentType.NEWS,
        "visibility_scope": NewsItemVisibilityScope.GLOBAL,
        "metadata": {
            "platform": "reddit",
            "source": "reinforcementlearning",
            "source_type": "reddit",
            "source_label": "reinforcementlearning",
            "aggregator": {
                "name": "Reddit",
                "title": (
                    "DeepMind veteran David Silver raises $1B, bets on "
                    "radically new type of Reinforcement Learning to build "
                    "superintelligence"
                ),
                "external_id": "1s4luyv",
                "metadata": {
                    "score": 7,
                    "comments_count": 0,
                    "upvote_ratio": 0.82,
                },
            },
            "article": {
                "url": (
                    "https://the-decoder.com/"
                    "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
                    "superintelligence-without-llms"
                ),
                "title": (
                    "DeepMind veteran David Silver raises $1B, bets on "
                    "radically new type of Reinforcement Learning to build "
                    "superintelligence"
                ),
                "source_domain": "the-decoder.com",
            },
            "discussion_url": (
                "https://www.reddit.com/r/reinforcementlearning/comments/1s4luyv/"
                "deepmind_veteran_david_silver_raises_1b_bets_on/"
            ),
            "scraped_at": "2026-03-30T20:00:12.583355+00:00",
            "discovery_time": "2026-03-30T20:00:12.583355+00:00",
        },
    }
    second_item = {
        **first_item,
        "metadata": {
            **first_item["metadata"],
            "aggregator": {
                **first_item["metadata"]["aggregator"],
                "metadata": {
                    "score": 9,
                    "comments_count": 0,
                    "upvote_ratio": 0.91,
                },
            },
            "scraped_at": "2026-03-30T20:15:13.213566+00:00",
            "discovery_time": "2026-03-30T20:15:13.213566+00:00",
        },
    }

    first_payload = build_news_item_upsert_input_from_scraped_item(first_item)
    second_payload = build_news_item_upsert_input_from_scraped_item(second_item)

    first_record, first_created = upsert_news_item(db_session, first_payload)
    db_session.commit()
    second_record, second_created = upsert_news_item(db_session, second_payload)
    db_session.commit()

    news_items = db_session.query(NewsItem).all()
    assert first_created is True
    assert second_created is False
    assert first_record.id == second_record.id
    assert len(news_items) == 1
    assert news_items[0].source_external_id == "1s4luyv"


def test_upsert_news_item_matches_existing_row_by_stable_identity(db_session) -> None:
    existing = NewsItem(
        ingest_key="old-volatile-key",
        visibility_scope=NewsItemVisibilityScope.GLOBAL.value,
        owner_user_id=None,
        platform="reddit",
        source_type="reddit",
        source_label="reinforcementlearning",
        source_external_id="1s4luyv",
        canonical_item_url=(
            "https://www.reddit.com/r/reinforcementlearning/comments/1s4luyv/"
            "deepmind_veteran_david_silver_raises_1b_bets_on/"
        ),
        canonical_story_url=(
            "https://the-decoder.com/"
            "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
            "superintelligence-without-llms"
        ),
        article_url=(
            "https://the-decoder.com/"
            "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
            "superintelligence-without-llms"
        ),
        article_title="Old title",
        article_domain="the-decoder.com",
        discussion_url=(
            "https://www.reddit.com/r/reinforcementlearning/comments/1s4luyv/"
            "deepmind_veteran_david_silver_raises_1b_bets_on/"
        ),
        raw_metadata={"scraped_at": "2026-03-30T19:45:00+00:00"},
        status=NewsItemStatus.NEW.value,
    )
    db_session.add(existing)
    db_session.commit()

    payload = build_news_item_upsert_input_from_scraped_item(
        {
            "url": (
                "https://the-decoder.com/"
                "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
                "superintelligence-without-llms"
            ),
            "title": (
                "DeepMind veteran David Silver raises $1B, bets on radically new "
                "type of Reinforcement Learning to build superintelligence"
            ),
            "content_type": ContentType.NEWS,
            "visibility_scope": NewsItemVisibilityScope.GLOBAL,
            "metadata": {
                "platform": "reddit",
                "source": "reinforcementlearning",
                "source_type": "reddit",
                "source_label": "reinforcementlearning",
                "aggregator": {
                    "name": "Reddit",
                    "external_id": "1s4luyv",
                },
                "article": {
                    "url": (
                        "https://the-decoder.com/"
                        "deepmind-veteran-david-silver-raises-1b-seed-round-to-build-"
                        "superintelligence-without-llms"
                    ),
                    "title": (
                        "DeepMind veteran David Silver raises $1B, bets on "
                        "radically new type of Reinforcement Learning to build "
                        "superintelligence"
                    ),
                    "source_domain": "the-decoder.com",
                },
                "discussion_url": (
                    "https://www.reddit.com/r/reinforcementlearning/comments/1s4luyv/"
                    "deepmind_veteran_david_silver_raises_1b_bets_on/"
                ),
                "summary": {
                    "title": "David Silver raises $1B",
                    "key_points": ["Point"],
                    "summary": "Summary",
                },
                "scraped_at": "2026-03-30T20:15:13.213566+00:00",
            },
        }
    )

    updated_record, was_created = upsert_news_item(db_session, payload)
    db_session.commit()

    news_items = db_session.query(NewsItem).all()
    assert was_created is False
    assert updated_record.id == existing.id
    assert len(news_items) == 1
    assert updated_record.ingest_key != "old-volatile-key"
    assert updated_record.summary_title == "David Silver raises $1B"
