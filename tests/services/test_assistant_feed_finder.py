from __future__ import annotations

from app.services.assistant_feed_finder import find_feed_options
from app.services.exa_client import ExaContentResult, ExaSearchResult
from app.services.feed_detection import FeedClassificationResult


def test_find_feed_options_extracts_and_validates_feed_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200: [
            ExaSearchResult(
                title="lucumr",
                url="https://lucumr.pocoo.org/",
                snippet="Armin Ronacher's weblog.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000: [
            ExaContentResult(
                title="lucumr",
                url="https://lucumr.pocoo.org/",
                text="Feed URL: https://lucumr.pocoo.org/feed.atom",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.validate_feed_url",
        lambda self, url: {
            "feed_url": url,
            "feed_format": "atom",
            "title": "lucumr",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.classify_feed_type",
        lambda self, **kwargs: FeedClassificationResult(
            feed_type="atom",
            confidence=0.96,
            reasoning="Validated Atom feed for the site.",
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.detect_from_links",
        lambda self, *args, **kwargs: None,
    )

    result = find_feed_options("find a blog by Armin Ronacher")

    assert result.query == "find a blog by Armin Ronacher"
    assert len(result.options) == 1
    option = result.options[0]
    assert option.title == "lucumr"
    assert option.feed_url == "https://lucumr.pocoo.org/feed.atom"
    assert option.feed_type == "atom"
    assert option.feed_format == "atom"
    assert option.evidence_url == "https://lucumr.pocoo.org"


def test_find_feed_options_dedupes_normalized_feed_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200: [
            ExaSearchResult(
                title="Primary",
                url="https://example.com/blog",
                snippet="Feed URL: https://example.com/feed.xml",
            ),
            ExaSearchResult(
                title="Duplicate",
                url="https://example.com/about",
                snippet="Subscribe at https://example.com/feed.xml/",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000: [
            ExaContentResult(title="Primary", url=urls[0], text="https://example.com/feed.xml"),
            ExaContentResult(title="Duplicate", url=urls[1], text="https://example.com/feed.xml/"),
        ],
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.validate_feed_url",
        lambda self, url: {
            "feed_url": url,
            "feed_format": "rss",
            "title": "Example Feed",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.classify_feed_type",
        lambda self, **kwargs: FeedClassificationResult(
            feed_type="atom",
            confidence=0.8,
            reasoning="Validated RSS feed.",
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.detect_from_links",
        lambda self, *args, **kwargs: None,
    )

    result = find_feed_options("find example blog feeds", limit=5)

    assert len(result.options) == 1
    assert result.options[0].feed_url == "https://example.com/feed.xml"


def test_find_feed_options_truncates_long_option_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200: [
            ExaSearchResult(
                title="A" * 500,
                url="https://example.com/blog",
                snippet="B" * 1200,
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000: [
            ExaContentResult(
                title="ignored",
                url=urls[0],
                text="https://example.com/feed.xml\n" + ("C" * 5000),
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.validate_feed_url",
        lambda self, url: {
            "feed_url": url,
            "feed_format": "rss",
            "title": "D" * 500,
        },
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.classify_feed_type",
        lambda self, **kwargs: FeedClassificationResult(
            feed_type="atom",
            confidence=0.8,
            reasoning="E" * 1200,
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.detect_from_links",
        lambda self, *args, **kwargs: None,
    )

    result = find_feed_options("find example blog feeds", limit=5)

    assert len(result.options) == 1
    option = result.options[0]
    assert len(option.title) <= 300
    assert option.title.endswith("...")
    assert option.description is not None
    assert len(option.description) <= 600
    assert option.description.endswith("...")
    assert option.rationale is not None
    assert len(option.rationale) <= 600
    assert option.rationale.endswith("...")
