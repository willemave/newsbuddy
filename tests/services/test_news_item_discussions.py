from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from app.constants import AGGREGATOR_FEED_URL_PREFIX, AGGREGATOR_SCRAPER_TYPE
from app.models.db import NewsItemDiscussion, UserScraperConfig
from app.models.metadata.summaries import DiscussionSummary, DiscussionSummaryTopic
from app.services.gateways.object_storage_gateway import ObjectStorageGateway, StoredObjectMetadata
from app.services.llm_summarization import ContentSummarizer
from app.services.news_item_discussions import (
    is_news_item_discussion_visible_to_active_user,
    list_due_news_item_discussion_refresh_candidates,
    refresh_news_item_discussion,
    should_enqueue_news_item_discussion_refresh,
    sync_news_item_discussion_from_news_item,
)


class _FakeGateway:
    provider = "local"

    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def put_text(self, *, key: str, text: str, content_type: str) -> StoredObjectMetadata:
        assert content_type == "application/json"
        self.objects[key] = text
        return StoredObjectMetadata(
            provider="local",
            bucket=None,
            key=key,
            size_bytes=len(text.encode("utf-8")),
        )


class _FakeSummarizer:
    model_hint = "fake-discussion-model"

    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, content, content_type, **kwargs):
        self.calls += 1
        assert content_type == "discussion_summary"
        assert "Comments:" in content
        assert kwargs["usage_persist"]["feature"] == "news_discussions"
        return DiscussionSummary(
            overview="Commenters focused on the technical tradeoffs and deployment risks.",
            topics=[
                DiscussionSummaryTopic(
                    title="Deployment tradeoffs",
                    summary=(
                        "Readers compared the proposed approach with simpler operational paths."
                    ),
                )
            ],
            external_discussion_url="https://news.ycombinator.com/item?id=123",
        )


def _fake_hn_payload(*, external_id: str, discussion_url: str) -> dict:
    return {
        "platform": "hackernews",
        "external_id": external_id,
        "discussion_url": discussion_url,
        "thread": {
            "title": "Example thread",
            "author": "alice",
            "score": 99,
            "comment_count": 2,
        },
        "comments": [
            {
                "comment_id": "c1",
                "parent_id": None,
                "author": "bob",
                "text": "The operational issue is rollout safety.",
                "compact_text": "The operational issue is rollout safety.",
                "depth": 0,
                "created_at": None,
                "source_url": discussion_url,
            },
            {
                "comment_id": "c2",
                "parent_id": "c1",
                "author": "carol",
                "text": "A simpler queue would be easier to reason about.",
                "compact_text": "A simpler queue would be easier to reason about.",
                "depth": 1,
                "created_at": None,
                "source_url": discussion_url,
            },
        ],
        "links": [],
        "stats": {
            "provider": "algolia",
            "declared_comment_count": 2,
            "fetched_count": 2,
        },
    }


def _add_aggregator_subscription(db_session, *, user_id: int, key: str) -> None:
    db_session.add(
        UserScraperConfig(
            user_id=user_id,
            scraper_type=AGGREGATOR_SCRAPER_TYPE,
            display_name=key,
            feed_url=f"{AGGREGATOR_FEED_URL_PREFIX}{key}",
            config={"key": key},
            is_active=True,
        )
    )
    db_session.commit()


def test_sync_news_item_discussion_captures_scrape_time_count(
    db_session,
    news_item_factory,
) -> None:
    item = news_item_factory(
        ingest_key="hn-count",
        platform="hackernews",
        source_external_id="123",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "123",
                "author": "alice",
                "metadata": {"score": 50, "comments_count": 17},
            },
        },
    )

    row = sync_news_item_discussion_from_news_item(db_session, item)
    db_session.commit()

    assert row is not None
    assert row.news_item_id == item.id
    assert row.platform == "hackernews"
    assert row.external_id == "123"
    assert row.comment_count == 17
    assert row.score == 50
    assert row.author == "alice"


def test_refresh_enqueue_requires_visible_due_item(
    db_session,
    news_item_factory,
    user_factory,
) -> None:
    user = user_factory()
    assert user.id is not None
    _add_aggregator_subscription(db_session, user_id=user.id, key="hackernews")
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC).replace(tzinfo=None)

    visible_item = news_item_factory(
        ingest_key="hn-due-visible",
        platform="hackernews",
        source_type="hackernews",
        source_external_id="due-visible",
        discussion_url="https://news.ycombinator.com/item?id=111",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "111",
                "metadata": {"comments_count": 12},
            },
        },
    )
    visible_row = sync_news_item_discussion_from_news_item(db_session, visible_item)
    assert visible_row is not None
    visible_row.fetched_comment_count = 12
    visible_row.last_refresh_status = "completed"
    visible_row.next_refresh_after = now - timedelta(minutes=1)

    hidden_item = news_item_factory(
        ingest_key="reddit-due-hidden",
        platform="reddit",
        source_type="reddit",
        source_external_id="due-hidden",
        discussion_url="https://reddit.com/r/example/comments/abc123/thread/",
        raw_metadata={
            "platform": "reddit",
            "aggregator": {
                "external_id": "abc123",
                "metadata": {"comments_count": 20},
            },
        },
    )
    hidden_row = sync_news_item_discussion_from_news_item(db_session, hidden_item)
    assert hidden_row is not None
    hidden_row.fetched_comment_count = 20
    hidden_row.next_refresh_after = now - timedelta(minutes=1)
    db_session.commit()

    assert is_news_item_discussion_visible_to_active_user(
        db_session,
        news_item_id=visible_item.id,
    )
    assert should_enqueue_news_item_discussion_refresh(
        db_session,
        row=visible_row,
        now=now,
    )
    visible_row.next_refresh_after = now + timedelta(hours=1)
    assert not should_enqueue_news_item_discussion_refresh(
        db_session,
        row=visible_row,
        now=now,
    )

    assert not is_news_item_discussion_visible_to_active_user(
        db_session,
        news_item_id=hidden_item.id,
    )
    assert not should_enqueue_news_item_discussion_refresh(
        db_session,
        row=hidden_row,
        now=now,
    )


def test_due_discussion_candidates_prioritize_visible_due_rows(
    db_session,
    news_item_factory,
    user_factory,
) -> None:
    user = user_factory()
    assert user.id is not None
    _add_aggregator_subscription(db_session, user_id=user.id, key="hackernews")
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC).replace(tzinfo=None)

    missing_summary_item = news_item_factory(
        ingest_key="hn-missing-summary",
        platform="hackernews",
        source_type="hackernews",
        source_external_id="missing-summary",
        discussion_url="https://news.ycombinator.com/item?id=201",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "201",
                "metadata": {"comments_count": 2},
            },
        },
        ingested_at=now - timedelta(hours=2),
    )
    high_growth_item = news_item_factory(
        ingest_key="hn-high-growth",
        platform="hackernews",
        source_type="hackernews",
        source_external_id="high-growth",
        discussion_url="https://news.ycombinator.com/item?id=202",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "202",
                "metadata": {"comments_count": 80},
            },
        },
        ingested_at=now - timedelta(minutes=30),
    )
    quiet_item = news_item_factory(
        ingest_key="hn-quiet",
        platform="hackernews",
        source_type="hackernews",
        source_external_id="quiet",
        discussion_url="https://news.ycombinator.com/item?id=203",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "203",
                "metadata": {"comments_count": 5},
            },
        },
        ingested_at=now,
    )
    hidden_growth_item = news_item_factory(
        ingest_key="reddit-hidden-growth",
        platform="reddit",
        source_type="reddit",
        source_external_id="hidden-growth-candidate",
        discussion_url="https://reddit.com/r/example/comments/hid/thread/",
        raw_metadata={
            "platform": "reddit",
            "aggregator": {
                "external_id": "hid",
                "metadata": {"comments_count": 100},
            },
        },
        ingested_at=now + timedelta(minutes=1),
    )

    missing_row = sync_news_item_discussion_from_news_item(db_session, missing_summary_item)
    high_growth_row = sync_news_item_discussion_from_news_item(db_session, high_growth_item)
    quiet_row = sync_news_item_discussion_from_news_item(db_session, quiet_item)
    hidden_row = sync_news_item_discussion_from_news_item(db_session, hidden_growth_item)
    assert missing_row is not None
    assert high_growth_row is not None
    assert quiet_row is not None
    assert hidden_row is not None

    for row in (missing_row, high_growth_row, quiet_row, hidden_row):
        row.last_refresh_status = "completed"
        row.next_refresh_after = now - timedelta(minutes=1)
    missing_row.summary_status = "not_ready"
    high_growth_row.summary = {"overview": "old summary"}
    high_growth_row.summary_status = "completed"
    high_growth_row.raw_comments_ref = {"kind": "storage"}
    high_growth_row.fetched_comment_count = 3
    quiet_row.summary = {"overview": "current summary"}
    quiet_row.summary_status = "completed"
    quiet_row.raw_comments_ref = {"kind": "storage"}
    quiet_row.fetched_comment_count = 5
    hidden_row.summary = {"overview": "old hidden summary"}
    hidden_row.summary_status = "completed"
    hidden_row.raw_comments_ref = {"kind": "storage"}
    hidden_row.fetched_comment_count = 1
    db_session.commit()

    candidates = list_due_news_item_discussion_refresh_candidates(
        db_session,
        limit=10,
        now=now,
    )

    assert candidates[:3] == [missing_summary_item.id, high_growth_item.id, quiet_item.id]
    assert hidden_growth_item.id not in candidates


def test_refresh_news_item_discussion_stores_raw_summary_and_honors_ttl(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    item = news_item_factory(
        ingest_key="hn-refresh",
        platform="hackernews",
        source_external_id="123",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={
            "platform": "hackernews",
            "aggregator": {
                "external_id": "123",
                "metadata": {"comments_count": 2},
            },
        },
    )
    fetch_calls = 0

    def _fake_fetch(*, external_id: str, discussion_url: str):
        nonlocal fetch_calls
        fetch_calls += 1
        assert external_id == "123"
        assert discussion_url == "https://news.ycombinator.com/item?id=123"
        return _fake_hn_payload(external_id=external_id, discussion_url=discussion_url)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )
    gateway = _FakeGateway()
    summarizer = _FakeSummarizer()

    result = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )

    assert result.success is True
    assert result.refreshed is True
    assert result.summarized is True
    assert fetch_calls == 1
    assert summarizer.calls == 1

    row = (
        db_session.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id == item.id)
        .one()
    )
    assert row.summary_status == "completed"
    assert row.summary["overview"].startswith("Commenters focused")
    assert row.comment_count == 2
    assert row.fetched_comment_count == 2
    assert row.raw_comments_ref["storage_key"] in gateway.objects

    skipped = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )

    assert skipped.status == "skipped"
    assert fetch_calls == 1
    assert summarizer.calls == 1

    refreshed_same_hash = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        force=True,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )

    assert refreshed_same_hash.success is True
    assert fetch_calls == 2
    assert summarizer.calls == 1


def test_refresh_news_item_discussion_claim_suppresses_concurrent_fetch(
    db_session,
    db_session_factory,
    news_item_factory,
    monkeypatch,
) -> None:
    item = news_item_factory(
        ingest_key="hn-concurrent-refresh",
        platform="hackernews",
        source_external_id="123",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={"platform": "hackernews"},
    )
    news_item_id = item.id
    assert sync_news_item_discussion_from_news_item(db_session, item) is not None
    db_session.commit()

    fetch_calls = 0
    concurrent_status: str | None = None
    gateway = _FakeGateway()
    summarizer = _FakeSummarizer()

    def _fake_fetch(*, external_id: str, discussion_url: str):
        nonlocal concurrent_status, fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            with db_session_factory() as concurrent_db:
                concurrent_result = refresh_news_item_discussion(
                    concurrent_db,
                    news_item_id=news_item_id,
                    gateway=cast(ObjectStorageGateway, gateway),
                    summarizer=cast(ContentSummarizer, summarizer),
                )
                concurrent_status = concurrent_result.status
        return _fake_hn_payload(external_id=external_id, discussion_url=discussion_url)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )

    result = refresh_news_item_discussion(
        db_session,
        news_item_id=news_item_id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )

    assert result.success is True
    assert concurrent_status == "skipped"
    assert fetch_calls == 1
    assert summarizer.calls == 1


def test_refresh_news_item_discussion_reclaims_expired_processing_lease(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    item = news_item_factory(
        ingest_key="hn-expired-discussion-lease",
        platform="hackernews",
        source_external_id="123",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={"platform": "hackernews"},
    )
    row = sync_news_item_discussion_from_news_item(db_session, item)
    assert row is not None
    row.last_refresh_status = "processing"
    row.next_refresh_after = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db_session.commit()

    fetch_calls = 0

    def _fake_fetch(*, external_id: str, discussion_url: str):
        nonlocal fetch_calls
        fetch_calls += 1
        return _fake_hn_payload(external_id=external_id, discussion_url=discussion_url)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )

    result = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, _FakeGateway()),
        summarizer=cast(ContentSummarizer, _FakeSummarizer()),
    )

    assert result.success is True
    assert result.refreshed is True
    assert fetch_calls == 1
