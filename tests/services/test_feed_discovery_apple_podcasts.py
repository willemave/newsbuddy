from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services import feed_discovery


def test_extract_apple_podcast_id():
    assert (
        feed_discovery._extract_apple_podcast_id(
            "https://itunes.apple.com/podcast/state-trance-official-podcast/id260190086"
        )
        == "260190086"
    )
    assert (
        feed_discovery._extract_apple_podcast_id(
            "https://podcasts.apple.com/us/podcast/founders-fears-failures/id1669777442?i=100"
        )
        == "1669777442"
    )
    assert feed_discovery._extract_apple_podcast_id("https://example.com") is None


def test_normalize_candidate_resolves_apple_podcast_feed(monkeypatch):
    def _stub_lookup(podcast_id: str, country: str) -> str:
        assert podcast_id == "1669777442"
        return "https://example.com/feed.xml"

    feed_discovery._itunes_lookup_feed_url.cache_clear()
    monkeypatch.setattr(feed_discovery, "_itunes_lookup_feed_url", _stub_lookup)

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
