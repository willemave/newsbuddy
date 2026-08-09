from types import SimpleNamespace

import pytest

from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services import apple_podcasts, feed_discovery_candidates


def test_extract_apple_podcast_id():
    assert (
        apple_podcasts.extract_apple_podcast_id(
            "https://itunes.apple.com/podcast/state-trance-official-podcast/id260190086"
        )
        == "260190086"
    )
    assert (
        apple_podcasts.extract_apple_podcast_id(
            "https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100"
        )
        == "1669777442"
    )
    assert (
        apple_podcasts.extract_apple_podcast_id(
            "https://itunes.apple.com/lookup?id=260190086&entity=podcast"
        )
        == "260190086"
    )
    assert apple_podcasts.extract_apple_podcast_id("https://example.com") is None
    assert (
        apple_podcasts.extract_apple_podcast_id("https://notpodcasts.apple.com/show/id260190086")
        is None
    )
    assert (
        apple_podcasts.extract_apple_podcast_id(
            "https://podcasts.apple.com.evil.test/show/id260190086"
        )
        is None
    )


def test_resolve_apple_podcast_feed_url_does_not_cache_lookup_failures(monkeypatch):
    calls = 0

    class _Response:
        def json(self):  # noqa: ANN201
            return {"results": [{"feedUrl": "https://example.com/feed.xml"}]}

    def _fetch(url: str, headers=None):  # noqa: ANN001
        del url, headers
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary lookup failure")
        return _Response()

    apple_podcasts._lookup_podcast_feed_url.cache_clear()
    monkeypatch.setattr(apple_podcasts.HTTP_SERVICE, "fetch", _fetch)

    try:
        url = "https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442"
        assert apple_podcasts.resolve_apple_podcast_feed_url(url) is None
        assert apple_podcasts.resolve_apple_podcast_feed_url(url) == "https://example.com/feed.xml"
        assert calls == 2
    finally:
        apple_podcasts._lookup_podcast_feed_url.cache_clear()


def test_apple_episode_fetches_publisher_rss_only_through_sandbox_http(monkeypatch) -> None:
    feed_url = "https://publisher.example.com/show.xml"
    monkeypatch.setattr(
        apple_podcasts,
        "_lookup_feed_and_episode",
        lambda *_args: (feed_url, "Sandboxed Episode"),
    )
    fetched_urls: list[str] = []

    class _SandboxHttpService:
        def fetch(self, url: str, **_kwargs):
            fetched_urls.append(url)
            return SimpleNamespace(
                text=(
                    "<rss><channel><item><title>Sandboxed Episode</title>"
                    '<enclosure url="https://cdn.example.com/episode.mp3" '
                    'type="audio/mpeg" /></item></channel></rss>'
                )
            )

    resolution = apple_podcasts.resolve_apple_podcast_episode(
        "https://podcasts.apple.com/us/podcast/show/id123?i=456",
        feed_http_service=_SandboxHttpService(),
    )

    assert resolution.audio_url == "https://cdn.example.com/episode.mp3"
    assert fetched_urls == [feed_url]

    with pytest.raises(ValueError, match="requires sandbox HTTP"):
        apple_podcasts.resolve_apple_podcast_episode(
            "https://podcasts.apple.com/us/podcast/show/id123?i=456"
        )


def test_normalize_candidate_resolves_apple_podcast_feed(monkeypatch):
    def _stub_resolve(url: str) -> str:
        assert url == (
            "https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100"
        )
        return "https://example.com/feed.xml"

    monkeypatch.setattr(feed_discovery_candidates, "resolve_apple_podcast_feed_url", _stub_resolve)

    candidate = DiscoveryCandidate(
        title="Founders Fears Failures",
        site_url="https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100",
        rationale="Test rationale",
    )

    normalized = feed_discovery_candidates._normalize_candidate(candidate)
    assert normalized is not None
    assert normalized.feed_url == "https://example.com/feed.xml"
    assert normalized.suggestion_type == "podcast_rss"
    assert normalized.config["podcast_id"] == "1669777442"
