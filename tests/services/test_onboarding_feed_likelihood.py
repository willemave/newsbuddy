from __future__ import annotations

from app.services.onboarding import _DiscoverSuggestion, _normalize_suggestions


def test_normalize_suggestions_uses_candidate_feed_url_when_feed_url_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.onboarding.resolve_feed_candidate",
        lambda **kwargs: {
            "feed_url": kwargs["candidate_feed_urls"][0],
            "feed_format": "rss",
            "title": "Marine Science Weekly",
        },
    )

    items = [
        _DiscoverSuggestion(
            title="Marine Science Weekly",
            site_url="https://example.org/newsletter",
            candidate_feed_url="https://example.org/rss.xml",
            is_likely_feed=True,
            feed_confidence=0.81,
        )
    ]

    normalized = _normalize_suggestions(items, "substack")

    assert len(normalized) == 1
    assert normalized[0].feed_url == "https://example.org/rss.xml"


def test_normalize_suggestions_uses_likely_feed_site_when_feed_like(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.onboarding.resolve_feed_candidate",
        lambda **kwargs: {
            "feed_url": kwargs["candidate_feed_urls"][0],
            "feed_format": "rss",
            "title": "Ocean Dispatch",
        },
    )

    items = [
        _DiscoverSuggestion(
            title="Ocean Dispatch",
            site_url="https://example.org/podcast-feed.xml",
            is_likely_feed=True,
            feed_confidence=0.74,
        )
    ]

    normalized = _normalize_suggestions(items, "podcast_rss")

    assert len(normalized) == 1
    assert normalized[0].feed_url == "https://example.org/podcast-feed.xml"


def test_normalize_suggestions_prefers_site_discovery_for_podcasts(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_resolve_feed_candidate(**kwargs):
        observed.update(kwargs)
        return {
            "feed_url": "https://good.example.com/rss",
            "feed_format": "rss",
            "title": "Creative Coding Weekly",
        }

    monkeypatch.setattr(
        "app.services.onboarding.resolve_feed_candidate",
        fake_resolve_feed_candidate,
    )

    items = [
        _DiscoverSuggestion(
            title="Creative Coding Weekly",
            site_url="https://example.com/show",
            feed_url="https://bad.example.com/rss",
        )
    ]

    normalized = _normalize_suggestions(items, "podcast_rss")

    assert len(normalized) == 1
    assert normalized[0].feed_url == "https://good.example.com/rss"
    assert observed["prefer_site_discovery"] is True
