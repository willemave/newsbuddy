from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from app.models.db import NewsItemDiscussion
from app.models.metadata.summaries import DiscussionSummary, DiscussionSummaryTopic
from app.services.gateways.object_storage_gateway import ObjectStorageGateway, StoredObjectMetadata
from app.services.llm_summarization import ContentSummarizer
from app.services.news_item_discussions import (
    refresh_news_item_discussion,
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
