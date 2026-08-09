"""Provider routing must use parsed hostname boundaries, not URL substrings."""

from __future__ import annotations

from app.pipeline.podcast_workers import PodcastMediaWorker
from app.services import (
    discussion_comments,
    discussion_fetcher,
    feed_discovery_candidates,
    podcast_search,
    youtube_equivalent_resolver,
)
from app.services.onboarding.suggestion_projection import _extract_subreddit


def test_discussion_routing_rejects_domain_lookalikes() -> None:
    assert discussion_fetcher._is_techmeme("", "https://www.techmeme.com/260101/p1")
    assert not discussion_fetcher._is_techmeme("", "https://techmeme.com.evil.test/p1")
    assert not discussion_fetcher._is_techmeme(
        "techmeme",
        "https://techmeme.com.evil.test/p1",
    )
    assert not discussion_fetcher._is_techmeme(
        "techmeme",
        "https://127.0.0.1/internal",
    )
    assert discussion_comments.is_hackernews_discussion(
        "", "https://news.ycombinator.com/item?id=1"
    )
    assert not discussion_comments.is_hackernews_discussion(
        "", "https://news.ycombinator.com.evil.test/item?id=1"
    )
    assert discussion_comments.is_reddit_discussion(
        "", "https://old.reddit.com/r/news/comments/abc/story"
    )
    assert not discussion_comments.is_reddit_discussion(
        "", "https://reddit.com.evil.test/r/news/comments/abc/story"
    )


def test_techmeme_platform_does_not_send_private_url_to_sandbox(monkeypatch) -> None:
    monkeypatch.setattr(
        discussion_fetcher,
        "sandboxed_http_service",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid Techmeme URL reached sandbox transport")
        ),
    )

    payload = discussion_fetcher._build_discussion_payload(
        platform="techmeme",
        discussion_url="https://127.0.0.1/internal",
        metadata={},
        comment_cap=1,
    )

    assert payload.status == "partial"
    assert payload.error_message == "Unsupported discussion platform: techmeme"


def test_youtube_routing_rejects_domain_lookalikes() -> None:
    valid_url = "https://www.youtube.com/watch?v=abc123"
    lookalike_url = "https://youtube.com.evil.test/watch?v=abc123"

    assert feed_discovery_candidates._looks_like_watch_url(valid_url)
    assert not feed_discovery_candidates._looks_like_watch_url(lookalike_url)
    assert PodcastMediaWorker._is_youtube_url(None, valid_url)
    assert not PodcastMediaWorker._is_youtube_url(None, lookalike_url)
    assert PodcastMediaWorker._extract_youtube_id(None, lookalike_url) is None
    assert youtube_equivalent_resolver._normalize_youtube_watch_url(lookalike_url) == lookalike_url


def test_apple_feed_resolution_rejects_domain_lookalike(monkeypatch) -> None:
    calls: list[str] = []

    def _fetch(url: str, **_kwargs):
        calls.append(url)
        raise AssertionError("lookalike URL reached Apple Podcasts resolution")

    monkeypatch.setattr(podcast_search, "_http_get_json", _fetch)

    assert (
        podcast_search._resolve_feed_url("https://podcasts.apple.com.evil.test/show/id123") is None
    )
    assert calls == []


def test_subreddit_extraction_rejects_domain_lookalike() -> None:
    assert _extract_subreddit("https://www.reddit.com/r/python/") == "python"
    assert _extract_subreddit("https://reddit.com.evil.test/r/private/") is None
