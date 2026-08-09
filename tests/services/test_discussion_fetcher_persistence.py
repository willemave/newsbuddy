"""Persistence and payload-path tests for discussion ingestion service."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, cast

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.db import Content, ContentDiscussion, NewsItem
from app.services import discussion_fetcher
from app.services.discussion_fetcher import (
    DiscussionFetchError,
    DiscussionFetchResult,
    DiscussionPayload,
    fetch_and_store_discussion,
    fetch_and_store_news_item_discussion,
)
from tests.services._discussion_fetcher_helpers import (
    FakeComment,
    FakeRedditClient,
    FakeResponse,
    FakeSubmission,
    create_news_content,
)


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def _require_mapping(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


@pytest.fixture(autouse=True)
def _prevent_live_techmeme_sandbox(monkeypatch):
    class _NoNetworkHttpService:
        def fetch(self, *_args, **_kwargs):
            raise AssertionError("test must inject Techmeme sandbox HTTP")

    @contextmanager
    def _runtime(**_kwargs):
        yield cast(Any, _NoNetworkHttpService())

    monkeypatch.setattr(discussion_fetcher, "sandboxed_http_service", _runtime)


def test_fetch_techmeme_discussion_groups_parses_grouped_links(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <div class="item" id="0i1">
          <span pml="260217p39"></span>
          <span class="drhed">More:</span>
          <span class="bls">
            <a href="https://example.com/a">Example A</a>
            <a href="https://example.com/b">Example B</a>
          </span>
          <span class="drhed">Forums:</span>
          <span class="bls">
            <a href="https://news.ycombinator.com/item?id=123">Hacker News</a>
            <a href="https://www.reddit.com/r/test/comments/abc123/thread/">r/test</a>
          </span>
        </div>
      </body>
    </html>
    """

    fetched: list[tuple[str, dict[str, str] | None]] = []

    class _SandboxHttpService:
        def fetch(self, url: str, headers: dict[str, str] | None = None):
            fetched.append((url, headers))
            return FakeResponse(text=html)

    groups = discussion_fetcher._fetch_techmeme_discussion_groups(
        "https://www.techmeme.com/260217/p39#a260217p39",
        {"aggregator": {"external_id": "p39#a260217p39"}},
        http_service=cast(Any, _SandboxHttpService()),
    )

    assert fetched == [
        (
            "https://www.techmeme.com/260217/p39",
            {"User-Agent": "news_app.discussion/1.0"},
        )
    ]
    assert len(groups) == 2
    assert groups[0]["label"] == "More"
    assert groups[1]["label"] == "Forums"
    forum_urls = [item["url"] for item in groups[1]["items"]]
    assert "https://news.ycombinator.com/item?id=123" in forum_urls
    assert "https://www.reddit.com/r/test/comments/abc123/thread/" in forum_urls


def test_techmeme_payload_uses_e2b_transport_and_never_host_http(monkeypatch) -> None:
    html = """
    <div class="item">
      <span pml="260217p39"></span>
      <span class="drhed">Forums:</span>
      <span class="bls"><a href="https://news.ycombinator.com/item?id=1">HN</a></span>
    </div>
    """
    fetched: list[str] = []

    class _SandboxHttpService:
        def fetch(self, url: str, **_kwargs):
            fetched.append(url)
            return FakeResponse(text=html)

    @contextmanager
    def _runtime(**kwargs):
        assert kwargs == {"user_id": 0}
        yield cast(Any, _SandboxHttpService())

    monkeypatch.setattr(discussion_fetcher, "sandboxed_http_service", _runtime)
    monkeypatch.setattr(
        discussion_fetcher.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Techmeme HTML must not use host HTTP")
        ),
        raising=False,
    )

    payload = discussion_fetcher._build_discussion_payload(
        platform="techmeme",
        discussion_url="https://www.techmeme.com/260217/p39#a260217p39",
        metadata={},
        comment_cap=10,
    )

    assert payload.status == "completed"
    assert fetched == ["https://www.techmeme.com/260217/p39"]


def test_fetch_and_store_discussion_techmeme_persists_list(db_session, monkeypatch) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "techmeme",
            "discussion_url": "https://www.techmeme.com/260217/p39#a260217p39",
        },
    )

    monkeypatch.setattr(
        discussion_fetcher,
        "_fetch_techmeme_discussion_groups",
        lambda *args, **kwargs: [
            {
                "label": "Forums",
                "items": [
                    {
                        "title": "Hacker News",
                        "url": "https://news.ycombinator.com/item?id=123",
                    }
                ],
            }
        ],
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))

    assert result.success is True
    assert result.status == "completed"

    row = (
        db_session.query(ContentDiscussion)
        .filter(ContentDiscussion.content_id == content.id)
        .first()
    )
    assert row is not None
    assert row.status == "completed"
    discussion_data = _require_mapping(row.discussion_data)
    assert discussion_data["mode"] == "discussion_list"
    assert discussion_data["discussion_groups"][0]["label"] == "Forums"


def test_fetch_and_store_discussion_hn_persists_comments(db_session, monkeypatch) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_hackernews_payload",
        lambda *args, **kwargs: DiscussionPayload(
            status="completed",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=123",
                "discussion_groups": [],
                "comments": [
                    {
                        "comment_id": "c1",
                        "parent_id": None,
                        "author": "alice",
                        "text": "Great post",
                        "compact_text": "Great post",
                        "depth": 0,
                        "created_at": None,
                        "source_url": "https://news.ycombinator.com/item?id=123",
                    }
                ],
                "compact_comments": ["Great post"],
                "links": [],
                "stats": {"cap": 500, "fetched_count": 1, "cap_reached": False},
            },
        ),
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))

    assert result.success is True
    assert result.status == "completed"

    row = (
        db_session.query(ContentDiscussion)
        .filter(ContentDiscussion.content_id == content.id)
        .first()
    )
    assert row is not None
    discussion_data = _require_mapping(row.discussion_data)
    assert discussion_data["mode"] == "comments"
    assert discussion_data["comments"][0]["author"] == "alice"


def test_build_reddit_payload_uses_authenticated_reddit_client(monkeypatch) -> None:
    reply = FakeComment(
        comment_id="r1",
        body="Reply body",
        author="bob",
        created_utc=1_700_000_001,
        replies=[],
    )
    root = FakeComment(
        comment_id="c1",
        body="Root body",
        author="alice",
        created_utc=1_700_000_000,
        replies=[reply],
    )
    fake_submission = FakeSubmission(title="Thread title", num_comments=2, comments=[root])
    fake_client = FakeRedditClient(fake_submission)

    monkeypatch.setattr(discussion_fetcher, "_get_reddit_client", lambda: fake_client)

    payload = discussion_fetcher._build_reddit_payload(
        "https://reddit.com/r/test/comments/abc123/thread/",
        comment_cap=10,
    )

    assert fake_client.requested_ids == ["abc123"]
    assert payload.status == "completed"
    assert payload.payload["source_url"] == "https://www.reddit.com/r/test/comments/abc123/thread/"
    assert payload.payload["comments"][0]["comment_id"] == "c1"
    assert payload.payload["comments"][1]["parent_id"] == "c1"
    assert payload.payload["stats"]["declared_comment_count"] == 2


def test_build_reddit_payload_marks_http_403_as_non_retryable(monkeypatch) -> None:
    class _ForbiddenResponseError(Exception):
        def __init__(self) -> None:
            self.response = type("Response", (), {"status_code": 403})()
            super().__init__("forbidden")

    class _FailingClient:
        def submission(self, *, id: str):  # noqa: A002 - mimic praw API
            raise _ForbiddenResponseError()

    monkeypatch.setattr(discussion_fetcher, "_get_reddit_client", lambda: _FailingClient())

    with pytest.raises(DiscussionFetchError) as exc:
        discussion_fetcher._build_reddit_payload(
            "https://reddit.com/r/test/comments/abc123/thread/",
            comment_cap=10,
        )

    assert exc.value.retryable is False


def test_fetch_and_store_discussion_propagates_non_retryable_fetch_errors(
    db_session,
    monkeypatch,
) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "reddit",
            "discussion_url": "https://reddit.com/r/test/comments/abc123/thread/",
        },
    )

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_reddit_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DiscussionFetchError("blocked", retryable=False)
        ),
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))

    assert result.success is False
    assert result.retryable is False
    row = (
        db_session.query(ContentDiscussion)
        .filter(ContentDiscussion.content_id == content.id)
        .first()
    )
    assert row is not None
    assert row.status == "failed"


def test_fetch_and_store_discussion_unsupported_platform_is_partial(db_session) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "unknown_platform",
            "discussion_url": "https://example.com/discussion",
        },
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))

    assert result.success is True
    assert result.status == "partial"

    row = (
        db_session.query(ContentDiscussion)
        .filter(ContentDiscussion.content_id == content.id)
        .first()
    )
    assert row is not None
    assert row.status == "partial"
    discussion_data = _require_mapping(row.discussion_data)
    assert discussion_data["mode"] == "none"


def test_fetch_and_store_denormalizes_comment_count_for_hn(db_session, monkeypatch) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_hackernews_payload",
        lambda *args, **kwargs: DiscussionPayload(
            status="completed",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=123",
                "discussion_groups": [],
                "comments": [
                    {
                        "comment_id": "c1",
                        "parent_id": None,
                        "author": "alice",
                        "text": "Great post",
                        "compact_text": "Great post",
                        "depth": 0,
                        "created_at": None,
                        "source_url": "https://news.ycombinator.com/item?id=123",
                    }
                ],
                "compact_comments": ["Great post"],
                "links": [],
                "stats": {
                    "cap": 500,
                    "fetched_count": 1,
                    "cap_reached": False,
                    "declared_comment_count": 42,
                },
            },
        ),
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))
    assert result.success is True

    db_session.refresh(content)
    assert _require_mapping(content.content_metadata)["comment_count"] == 42


def test_fetch_and_store_denormalizes_discussion_count_for_techmeme_without_top_comment(
    db_session,
    monkeypatch,
) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "techmeme",
            "discussion_url": "https://www.techmeme.com/260217/p39#a260217p39",
            "top_comment": {"author": "x.com", "text": "@sama"},
        },
    )

    monkeypatch.setattr(
        discussion_fetcher,
        "_fetch_techmeme_discussion_groups",
        lambda *args, **kwargs: [
            {
                "label": "Forums",
                "items": [
                    {"title": "HN", "url": "https://news.ycombinator.com/item?id=1"},
                    {
                        "title": "Reddit",
                        "url": "https://www.reddit.com/r/tech/comments/abc/thread/",
                    },
                ],
            }
        ],
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))
    assert result.success is True

    db_session.refresh(content)
    content_metadata = _require_mapping(content.content_metadata)
    assert content_metadata["comment_count"] == 2
    assert "top_comment" not in content_metadata


def test_fetch_and_store_discussion_preserves_concurrent_metadata_updates(
    db_session,
    monkeypatch,
) -> None:
    content = create_news_content(
        db_session,
        metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
            "workflow_transition": "initial",
        },
    )

    external_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    external_session = external_session_factory()
    try:
        external_content = external_session.query(Content).filter(Content.id == content.id).first()
        assert external_content is not None
        metadata = dict(external_content.content_metadata or {})
        metadata["workflow_transition"] = "analyze_url.complete"
        metadata["external_flag"] = True
        external_content.content_metadata = metadata
        external_session.commit()
    finally:
        external_session.close()

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_hackernews_payload",
        lambda *args, **kwargs: DiscussionPayload(
            status="completed",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=123",
                "discussion_groups": [],
                "comments": [
                    {
                        "comment_id": "c1",
                        "parent_id": None,
                        "author": "alice",
                        "text": "Great post",
                        "compact_text": "Great post",
                        "depth": 0,
                        "created_at": None,
                        "source_url": "https://news.ycombinator.com/item?id=123",
                    }
                ],
                "compact_comments": ["Great post"],
                "links": [],
                "stats": {
                    "cap": 500,
                    "fetched_count": 1,
                    "cap_reached": False,
                    "declared_comment_count": 42,
                },
            },
        ),
    )

    result = fetch_and_store_discussion(db_session, _require_id(content.id))
    assert result.success is True

    db_session.refresh(content)
    content_metadata = _require_mapping(content.content_metadata)
    assert content_metadata["workflow_transition"] == "analyze_url.complete"
    assert content_metadata["external_flag"] is True
    assert _require_mapping(content_metadata["top_comment"])["author"] == "alice"
    assert content_metadata["comment_count"] == 42


def test_fetch_and_store_news_item_discussion_persists_payload_and_preview_fields(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-discussion-persist",
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="news-item-discussion-persist",
        article_url="https://example.com/story",
        article_title="Story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=123",
        raw_metadata={"workflow_transition": "news.ingested"},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_hackernews_payload",
        lambda *args, **kwargs: DiscussionPayload(
            status="completed",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=123",
                "discussion_groups": [],
                "comments": [
                    {
                        "comment_id": "c1",
                        "parent_id": None,
                        "author": "alice",
                        "text": "Great post",
                        "compact_text": "Great post",
                        "depth": 0,
                        "created_at": None,
                        "source_url": "https://news.ycombinator.com/item?id=123",
                    }
                ],
                "compact_comments": ["Great post"],
                "links": [],
                "stats": {
                    "cap": 500,
                    "fetched_count": 1,
                    "cap_reached": False,
                    "declared_comment_count": 42,
                },
            },
        ),
    )

    result = fetch_and_store_news_item_discussion(db_session, _require_id(item.id))

    assert result.success is True
    db_session.refresh(item)
    raw_metadata = _require_mapping(item.raw_metadata)
    assert raw_metadata["workflow_transition"] == "news.ingested"
    assert raw_metadata["discussion_status"] == "completed"
    assert _require_mapping(raw_metadata["discussion_payload"])["comments"][0]["author"] == "alice"
    assert raw_metadata["top_comment"] == {"author": "alice", "text": "Great post"}
    assert raw_metadata["comment_count"] == 42
    assert isinstance(raw_metadata["discussion_fetched_at"], str)
    assert "discussion_error" not in raw_metadata


def test_fetch_and_store_news_item_discussion_persists_failed_status(
    db_session,
    monkeypatch,
) -> None:
    item = NewsItem(
        ingest_key="news-item-discussion-failed",
        visibility_scope="global",
        platform="reddit",
        source_type="reddit",
        source_label="Reddit",
        source_external_id="news-item-discussion-failed",
        article_url="https://example.com/story",
        article_title="Story",
        article_domain="example.com",
        discussion_url="https://reddit.com/r/test/comments/abc123/thread/",
        raw_metadata={},
        status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    monkeypatch.setattr(
        discussion_fetcher,
        "_build_reddit_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DiscussionFetchError("blocked", retryable=False)
        ),
    )

    result = fetch_and_store_news_item_discussion(db_session, _require_id(item.id))

    assert result == DiscussionFetchResult(
        success=False,
        status="failed",
        error_message="Discussion fetch failed: blocked",
        retryable=False,
    )
    db_session.refresh(item)
    raw_metadata = _require_mapping(item.raw_metadata)
    assert raw_metadata["discussion_status"] == "failed"
    assert _require_mapping(raw_metadata["discussion_payload"])["mode"] == "none"
    assert raw_metadata["discussion_error"] == "Discussion fetch failed: blocked"
    assert "discussion_fetched_at" not in raw_metadata
