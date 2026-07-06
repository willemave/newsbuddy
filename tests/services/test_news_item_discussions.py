from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from app.constants import AGGREGATOR_FEED_URL_PREFIX, AGGREGATOR_SCRAPER_TYPE
from app.models.db import NewsItemDiscussion, UserScraperConfig
from app.models.metadata.summaries import DiscussionSummary, DiscussionSummaryTopic
from app.services.gateways.object_storage_gateway import ObjectStorageGateway, StoredObjectMetadata
from app.services.llm_summarization import ContentSummarizer
from app.services.news_item_discussions import (
    REFRESH_STATUS_GONE,
    TerminalNewsItemDiscussionUnavailable,
    _fetch_hackernews_comments,
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
        self.content_types: list[str] = []
        self.prompts: list[str] = []

    def summarize(self, content, content_type, **kwargs):
        self.calls += 1
        self.content_types.append(content_type)
        self.prompts.append(content)
        assert content_type in {"discussion_summary", "discussion_summary_merge"}
        if content_type == "discussion_summary":
            assert "Comments:" in content
        else:
            assert "Existing summary JSON:" in content
            assert "New or changed comments:" in content
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


class _MergeFailingSummarizer(_FakeSummarizer):
    def summarize(self, content, content_type, **kwargs):
        if content_type == "discussion_summary_merge":
            self.calls += 1
            self.content_types.append(content_type)
            self.prompts.append(content)
            raise ValueError("merge output validation failed")
        return super().summarize(content, content_type, **kwargs)


def _fake_hn_payload(
    *,
    external_id: str,
    discussion_url: str,
    extra_comments: list[dict] | None = None,
) -> dict:
    comments = [
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
    ]
    comments.extend(extra_comments or [])
    return {
        "platform": "hackernews",
        "external_id": external_id,
        "discussion_url": discussion_url,
        "thread": {
            "title": "Example thread",
            "author": "alice",
            "score": 99,
            "comment_count": len(comments),
        },
        "comments": comments,
        "links": [],
        "stats": {
            "provider": "algolia",
            "declared_comment_count": len(comments),
            "fetched_count": len(comments),
        },
    }


def test_fetch_hackernews_comments_marks_dead_firebase_item_terminal(monkeypatch) -> None:
    def _fake_fetch_json(_client, url: str, *, retryable_404: bool = False):
        assert retryable_404 is False
        if "hacker-news.firebaseio.com" in url:
            return {"id": 123, "type": "story", "dead": True}
        raise AssertionError("dead HN items should not fetch Algolia comments")

    monkeypatch.setattr("app.services.news_item_discussions._fetch_json", _fake_fetch_json)

    with pytest.raises(TerminalNewsItemDiscussionUnavailable) as exc_info:
        _fetch_hackernews_comments(
            external_id="123",
            discussion_url="https://news.ycombinator.com/item?id=123",
        )

    assert str(exc_info.value) == "Hacker News discussion is gone"
    assert exc_info.value.retryable is False
    assert exc_info.value.status == REFRESH_STATUS_GONE


def _extra_hn_comments(
    *,
    discussion_url: str,
    count: int,
    start: int = 3,
) -> list[dict]:
    return [
        {
            "comment_id": f"c{index}",
            "parent_id": None,
            "author": f"user{index}",
            "text": f"Additional material comment {index} about deployment details.",
            "compact_text": f"Additional material comment {index} about deployment details.",
            "depth": 0,
            "created_at": None,
            "source_url": discussion_url,
        }
        for index in range(start, start + count)
    ]


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


def test_refresh_enqueue_ignores_reddit_aggregator_subscription(
    db_session,
    news_item_factory,
    user_factory,
) -> None:
    user = user_factory()
    assert user.id is not None
    _add_aggregator_subscription(db_session, user_id=user.id, key="reddit")
    now = datetime(2026, 5, 14, 12, 0, tzinfo=UTC).replace(tzinfo=None)

    item = news_item_factory(
        ingest_key="reddit-stale-aggregator-hidden",
        platform="reddit",
        source_type="reddit",
        source_external_id="stale-aggregator-hidden",
        discussion_url="https://reddit.com/r/example/comments/stale/thread/",
        raw_metadata={
            "platform": "reddit",
            "aggregator": {
                "external_id": "stale",
                "metadata": {"comments_count": 20},
            },
        },
    )
    row = sync_news_item_discussion_from_news_item(db_session, item)
    assert row is not None
    row.fetched_comment_count = 1
    row.next_refresh_after = now - timedelta(minutes=1)
    db_session.commit()

    assert not is_news_item_discussion_visible_to_active_user(
        db_session,
        news_item_id=item.id,
    )
    assert not should_enqueue_news_item_discussion_refresh(
        db_session,
        row=row,
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
    stale_unsupported_item = news_item_factory(
        ingest_key="techmeme-stale-discussion-row",
        platform="techmeme",
        source_type="techmeme",
        source_external_id="techmeme-stale",
        discussion_url="https://www.techmeme.com/260518/p46#a260518p46",
        canonical_item_url="https://www.techmeme.com/260518/p46#a260518p46",
        raw_metadata={"platform": "techmeme"},
        ingested_at=now + timedelta(minutes=2),
    )

    missing_row = sync_news_item_discussion_from_news_item(db_session, missing_summary_item)
    high_growth_row = sync_news_item_discussion_from_news_item(db_session, high_growth_item)
    quiet_row = sync_news_item_discussion_from_news_item(db_session, quiet_item)
    hidden_row = sync_news_item_discussion_from_news_item(db_session, hidden_growth_item)
    stale_unsupported_row = NewsItemDiscussion(
        news_item_id=stale_unsupported_item.id,
        platform="hackernews",
        external_id="123",
        discussion_url="https://news.ycombinator.com/item?id=123",
        summary_status="completed",
        last_refresh_status="completed",
        next_refresh_after=now - timedelta(minutes=1),
        raw_comments_ref={"kind": "storage"},
        summary={"overview": "stale summary"},
        fetched_comment_count=1,
    )
    db_session.add(stale_unsupported_row)
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
    assert stale_unsupported_item.id not in candidates
    assert not should_enqueue_news_item_discussion_refresh(
        db_session,
        row=stale_unsupported_row,
        now=now,
    )


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
    bumped_news_item_ids: list[int] = []

    def _fake_bump(_db: object, *, news_item_id: int) -> bool:
        bumped_news_item_ids.append(news_item_id)
        return True

    monkeypatch.setattr(
        "app.services.news_item_discussions.bump_briefing_version_for_news_item",
        _fake_bump,
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
    assert bumped_news_item_ids == [item.id]

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
    assert row.summary_input_sha256 is not None
    assert row.summary_comment_count == 2
    assert row.summary_comment_fingerprints is not None
    assert sorted(row.summary_comment_fingerprints) == ["c1", "c2"]
    assert row.summary_seen_input_sha256 == row.summary_input_sha256
    assert row.summary_seen_comment_count == 2
    assert row.summary_seen_comment_fingerprints == row.summary_comment_fingerprints
    assert row.summary_incremental_update_count == 0

    skipped = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )

    assert skipped.status == "skipped"
    assert fetch_calls == 1
    assert summarizer.calls == 1
    assert bumped_news_item_ids == [item.id]

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
    assert bumped_news_item_ids == [item.id]


def test_refresh_news_item_discussion_marks_dead_hn_terminal(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC).replace(tzinfo=None)
    item = news_item_factory(
        ingest_key="hn-dead-terminal",
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
        raise TerminalNewsItemDiscussionUnavailable("Hacker News discussion is gone")

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )

    result = refresh_news_item_discussion(db_session, news_item_id=item.id)

    assert result.success is False
    assert result.status == REFRESH_STATUS_GONE
    assert result.retryable is False

    row = (
        db_session.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id == item.id)
        .one()
    )
    assert row.last_refresh_status == REFRESH_STATUS_GONE
    assert row.last_refresh_error == "Hacker News discussion is gone"
    assert row.next_refresh_after is None
    assert row.summary_status == REFRESH_STATUS_GONE
    assert item.id not in list_due_news_item_discussion_refresh_candidates(
        db_session,
        now=now,
    )
    assert not should_enqueue_news_item_discussion_refresh(db_session, row=row, now=now)
    assert fetch_calls == 1

    second = refresh_news_item_discussion(db_session, news_item_id=item.id)

    assert second.status == REFRESH_STATUS_GONE
    assert second.retryable is False
    assert fetch_calls == 1


def test_refresh_news_item_discussion_skips_small_comment_delta(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    discussion_url = "https://news.ycombinator.com/item?id=124"
    item = news_item_factory(
        ingest_key="hn-refresh-small-delta",
        platform="hackernews",
        source_external_id="124",
        discussion_url=discussion_url,
        raw_metadata={"platform": "hackernews"},
    )
    small_delta_payload = _fake_hn_payload(
        external_id="124",
        discussion_url=discussion_url,
        extra_comments=_extra_hn_comments(discussion_url=discussion_url, count=10),
    )
    payloads = [
        _fake_hn_payload(external_id="124", discussion_url=discussion_url),
        small_delta_payload,
        small_delta_payload,
    ]

    def _fake_fetch(*, external_id: str, discussion_url: str):
        return payloads.pop(0)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )
    gateway = _FakeGateway()
    summarizer = _FakeSummarizer()

    first = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    row = (
        db_session.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id == item.id)
        .one()
    )
    previous_summary_input_sha256 = row.summary_input_sha256

    second = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        force=True,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    db_session.refresh(row)
    seen_input_sha256 = row.summary_seen_input_sha256

    third = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        force=True,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    db_session.refresh(row)

    assert first.summarized is True
    assert second.success is True
    assert second.summarized is False
    assert third.success is True
    assert third.summarized is False
    assert summarizer.content_types == ["discussion_summary"]
    assert row.fetched_comment_count == 12
    assert row.summary_comment_count == 2
    assert row.summary_input_sha256 == previous_summary_input_sha256
    assert row.summary_seen_comment_count == 12
    assert row.summary_seen_input_sha256 == seen_input_sha256
    assert row.summary_seen_input_sha256 != row.summary_input_sha256


def test_refresh_news_item_discussion_merges_large_comment_delta(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    discussion_url = "https://news.ycombinator.com/item?id=125"
    item = news_item_factory(
        ingest_key="hn-refresh-large-delta",
        platform="hackernews",
        source_external_id="125",
        discussion_url=discussion_url,
        raw_metadata={"platform": "hackernews"},
    )
    payloads = [
        _fake_hn_payload(external_id="125", discussion_url=discussion_url),
        _fake_hn_payload(
            external_id="125",
            discussion_url=discussion_url,
            extra_comments=_extra_hn_comments(discussion_url=discussion_url, count=11),
        ),
    ]

    def _fake_fetch(*, external_id: str, discussion_url: str):
        return payloads.pop(0)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )
    gateway = _FakeGateway()
    summarizer = _FakeSummarizer()

    first = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    second = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        force=True,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    row = (
        db_session.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id == item.id)
        .one()
    )

    assert first.summarized is True
    assert second.summarized is True
    assert summarizer.content_types == ["discussion_summary", "discussion_summary_merge"]
    assert "Additional material comment 13" in summarizer.prompts[-1]
    assert row.summary_comment_count == 13
    assert row.summary_seen_input_sha256 == row.summary_input_sha256
    assert row.summary_seen_comment_count == 13
    assert row.summary_incremental_update_count == 1


def test_refresh_news_item_discussion_falls_back_to_full_summary_when_merge_fails(
    db_session,
    news_item_factory,
    monkeypatch,
) -> None:
    discussion_url = "https://news.ycombinator.com/item?id=126"
    item = news_item_factory(
        ingest_key="hn-refresh-merge-fallback",
        platform="hackernews",
        source_external_id="126",
        discussion_url=discussion_url,
        raw_metadata={"platform": "hackernews"},
    )
    payloads = [
        _fake_hn_payload(external_id="126", discussion_url=discussion_url),
        _fake_hn_payload(
            external_id="126",
            discussion_url=discussion_url,
            extra_comments=_extra_hn_comments(discussion_url=discussion_url, count=11),
        ),
    ]

    def _fake_fetch(*, external_id: str, discussion_url: str):
        return payloads.pop(0)

    monkeypatch.setattr(
        "app.services.news_item_discussions._fetch_hackernews_comments",
        _fake_fetch,
    )
    gateway = _FakeGateway()
    summarizer = _MergeFailingSummarizer()

    first = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    second = refresh_news_item_discussion(
        db_session,
        news_item_id=item.id,
        force=True,
        gateway=cast(ObjectStorageGateway, gateway),
        summarizer=cast(ContentSummarizer, summarizer),
    )
    row = (
        db_session.query(NewsItemDiscussion)
        .filter(NewsItemDiscussion.news_item_id == item.id)
        .one()
    )

    assert first.summarized is True
    assert second.success is True
    assert second.summarized is True
    assert summarizer.content_types == [
        "discussion_summary",
        "discussion_summary_merge",
        "discussion_summary",
    ]
    assert row.summary_status == "completed"
    assert row.summary_incremental_update_count == 0


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
