"""Normalized provider contract tests for discussion comments."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from app.services import discussion_fetcher, news_item_discussions
from app.services.discussion_comments import (
    DiscussionFetchError,
    NormalizedDiscussion,
    TerminalDiscussionUnavailable,
    fetch_hackernews_comments,
    fetch_reddit_comments,
    normalize_hackernews_comments,
)
from app.services.http import HttpService
from tests.services._discussion_fetcher_helpers import (
    FakeAuthor,
    FakeComment,
    FakeRedditClient,
    FakeSubmission,
)

HN_URL = "https://news.ycombinator.com/item?id=123"
HN_FIREBASE_URL = "https://hacker-news.firebaseio.com/v0/item/123.json"
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/items/123"


class _JsonResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _StaticHttpService:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str, **_kwargs: Any) -> _JsonResponse:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"Unexpected discussion request: {url}")
        return _JsonResponse(self.responses[url])


class _SequenceClient:
    def __init__(self, outcomes: list[tuple[int, Any]]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        del headers
        self.calls.append(url)
        status_code, payload = next(self.outcomes)
        return httpx.Response(
            status_code,
            request=httpx.Request("GET", url),
            json=payload,
        )


@pytest.fixture
def hn_provider_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    firebase_item = {
        "id": 123,
        "type": "story",
        "title": "Shared provider",
        "by": "alice",
        "score": "42",
        "descendants": "4",
        "time": 1_700_000_000,
    }
    algolia_item = {
        "id": 123,
        "title": "Algolia fallback title",
        "children": [
            {
                "id": 1,
                "author": "bob",
                "text": "Root https://example.com/paper",
                "created_at_i": 1_700_000_001,
                "children": [
                    {
                        "id": 2,
                        "author": "carol",
                        "text": "<p>Nested reply</p>",
                        "created_at_i": 1_700_000_002,
                        "children": [],
                    }
                ],
            },
            "malformed child",
            {
                "id": 3,
                "author": "unknown",
                "text": "",
                "children": [
                    {
                        "id": 4,
                        "author": "dana",
                        "text": "Visible grandchild",
                        "children": [],
                    }
                ],
            },
        ],
    }
    return firebase_item, algolia_item


@pytest.fixture
def normalized_hn_discussion(
    hn_provider_payloads: tuple[dict[str, Any], dict[str, Any]],
) -> NormalizedDiscussion:
    firebase_item, algolia_item = hn_provider_payloads
    return normalize_hackernews_comments(
        external_id="123",
        discussion_url=HN_URL,
        firebase_item=firebase_item,
        algolia_item=algolia_item,
        comment_cap=500,
    )


def test_hackernews_provider_fetches_two_requests_and_normalizes_tree(
    hn_provider_payloads: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    firebase_item, algolia_item = hn_provider_payloads
    service = _StaticHttpService(
        {
            HN_FIREBASE_URL: firebase_item,
            HN_ALGOLIA_URL: algolia_item,
        }
    )

    discussion = fetch_hackernews_comments(
        external_id="123",
        discussion_url=HN_URL,
        comment_cap=500,
        http_service=cast(HttpService, service),
    )

    assert service.calls == [HN_FIREBASE_URL, HN_ALGOLIA_URL]
    assert discussion.platform == "hackernews"
    assert discussion.thread.title == "Shared provider"
    assert discussion.thread.author == "alice"
    assert discussion.thread.score == 42
    assert discussion.thread.comment_count == 4
    assert discussion.fetched_count == 3
    assert discussion.total_seen == 4
    assert [comment.comment_id for comment in discussion.comments] == ["1", "2", "4"]
    assert discussion.comments[1].parent_id == "1"
    assert discussion.comments[2].parent_id is None
    assert discussion.comments[0].source_url == "https://news.ycombinator.com/item?id=1"
    assert [link.as_payload() for link in discussion.links] == [
        {
            "url": "https://example.com/paper",
            "source": "comment",
            "comment_id": "1",
        }
    ]


def test_hackernews_provider_stops_after_terminal_firebase_item() -> None:
    service = _StaticHttpService({HN_FIREBASE_URL: {"id": 123, "dead": True}})

    with pytest.raises(TerminalDiscussionUnavailable, match="discussion is gone") as exc_info:
        fetch_hackernews_comments(
            external_id="123",
            discussion_url=HN_URL,
            comment_cap=500,
            http_service=cast(HttpService, service),
        )

    assert exc_info.value.retryable is False
    assert service.calls == [HN_FIREBASE_URL]


def test_hackernews_provider_rejects_malformed_tree_payload(
    hn_provider_payloads: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    firebase_item, _algolia_item = hn_provider_payloads
    service = _StaticHttpService(
        {
            HN_FIREBASE_URL: firebase_item,
            HN_ALGOLIA_URL: ["not", "an", "object"],
        }
    )

    with pytest.raises(DiscussionFetchError, match="non-object payload") as exc_info:
        fetch_hackernews_comments(
            external_id="123",
            discussion_url=HN_URL,
            comment_cap=500,
            http_service=cast(HttpService, service),
        )

    assert exc_info.value.retryable is True
    assert service.calls == [HN_FIREBASE_URL, HN_ALGOLIA_URL]


def test_hackernews_provider_uses_http_service_retry_boundary(monkeypatch) -> None:
    service = HttpService()
    client = _SequenceClient(
        [
            (503, {"error": "temporary"}),
            (200, {"id": 123, "title": "Recovered", "descendants": 0}),
            (200, {"id": 123, "children": []}),
        ]
    )
    monkeypatch.setattr(service, "get_client", lambda url=None: client)
    monkeypatch.setattr(cast(Any, service.fetch).retry, "sleep", lambda _delay: None)

    discussion = fetch_hackernews_comments(
        external_id="123",
        discussion_url=HN_URL,
        comment_cap=500,
        http_service=service,
    )

    assert discussion.thread.title == "Recovered"
    assert client.calls == [HN_FIREBASE_URL, HN_FIREBASE_URL, HN_ALGOLIA_URL]


def test_hackernews_provider_classifies_client_error_without_retry(monkeypatch) -> None:
    service = HttpService()
    client = _SequenceClient(
        [
            (404, {"error": "missing"}),
            (200, {"id": 123, "title": "must not be fetched"}),
        ]
    )
    monkeypatch.setattr(service, "get_client", lambda url=None: client)
    monkeypatch.setattr(cast(Any, service.fetch).retry, "sleep", lambda _delay: None)

    with pytest.raises(DiscussionFetchError, match="HTTP 404") as exc_info:
        fetch_hackernews_comments(
            external_id="123",
            discussion_url=HN_URL,
            comment_cap=500,
            http_service=service,
        )

    assert exc_info.value.retryable is False
    assert client.calls == [HN_FIREBASE_URL]


def test_reddit_provider_preserves_praw_fetch_and_normalizes_tree() -> None:
    reply = FakeComment(
        comment_id="r1",
        body="Reply body",
        author="bob",
        created_utc=1_700_000_001,
    )
    root = FakeComment(
        comment_id="c1",
        body="Read https://example.com/paper",
        body_html=('Read <a href="https://example.com/paper">Provider design paper</a>'),
        author="alice",
        created_utc=1_700_000_000,
        replies=[reply],
    )
    submission = FakeSubmission(title="Thread title", num_comments=2, comments=[root])
    submission.author = FakeAuthor("poster")
    submission.score = 17
    submission.created_utc = 1_699_999_999
    submission.subreddit = type("Subreddit", (), {"display_name": "python"})()
    client = FakeRedditClient(submission)

    discussion = fetch_reddit_comments(
        external_id="abc123",
        discussion_url="http://old.reddit.com/r/python/comments/abc123/thread/",
        comment_cap=500,
        reddit_client=client,
    )

    assert client.requested_ids == ["abc123"]
    assert submission.comment_sort == "top"
    assert discussion.discussion_url == ("https://www.reddit.com/r/python/comments/abc123/thread/")
    assert discussion.thread.author == "poster"
    assert discussion.thread.score == 17
    assert discussion.thread.subreddit == "python"
    assert [comment.comment_id for comment in discussion.comments] == ["c1", "r1"]
    assert discussion.comments[1].parent_id == "c1"
    assert discussion.links[0].title == "Provider design paper"


def test_consumers_preserve_their_distinct_payload_contracts(
    normalized_hn_discussion: NormalizedDiscussion,
) -> None:
    legacy = discussion_fetcher._legacy_provider_payload(normalized_hn_discussion)
    news_item = news_item_discussions._news_item_provider_payload(normalized_hn_discussion)

    assert set(legacy.payload) == {
        "mode",
        "source_url",
        "discussion_groups",
        "comments",
        "compact_comments",
        "links",
        "stats",
    }
    assert legacy.payload["comments"][0]["source_url"] == HN_URL
    assert legacy.payload["stats"] == {
        "cap": 500,
        "fetched_count": 3,
        "cap_reached": False,
        "total_seen": 4,
        "declared_comment_count": 4,
    }

    assert news_item["thread"]["title"] == "Shared provider"
    assert news_item["comments"][0]["source_url"] == ("https://news.ycombinator.com/item?id=1")
    assert news_item["stats"] == {
        "provider": "algolia",
        "declared_comment_count": 4,
        "fetched_count": 3,
        "total_seen": 4,
        "stored_comment_cap": 500,
        "cap_reached": False,
    }
