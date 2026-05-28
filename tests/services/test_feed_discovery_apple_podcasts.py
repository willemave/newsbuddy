from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services import apple_podcasts, feed_discovery


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


def test_normalize_candidate_resolves_apple_podcast_feed(monkeypatch):
    def _stub_resolve(url: str) -> str:
        assert url == (
            "https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100"
        )
        return "https://example.com/feed.xml"

    monkeypatch.setattr(feed_discovery, "resolve_apple_podcast_feed_url", _stub_resolve)

    candidate = DiscoveryCandidate(
        title="Founders Fears Failures",
        site_url="https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100",
        rationale="Test rationale",
    )

    normalized = feed_discovery._normalize_candidate(candidate)
    assert normalized is not None
    assert normalized.feed_url == "https://example.com/feed.xml"
    assert normalized.suggestion_type == "podcast_rss"
    assert normalized.config["podcast_id"] == "1669777442"
