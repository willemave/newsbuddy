from __future__ import annotations

from pathlib import Path

from app.services.assistant_feed_finder import find_feed_options
from app.services.exa_client import ExaContentResult, ExaSearchResult
from app.services.feed_detection import FeedClassificationResult

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SEQUOIA_TRAINING_DATA_HTML = (FIXTURES_DIR / "sequoia_training_data_podcast_links.html").read_text()
SEQUOIA_TRAINING_DATA_FEED = "https://feeds.megaphone.fm/trainingdata"


def test_find_feed_options_extracts_and_validates_feed_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200, **_kwargs: [
            ExaSearchResult(
                title="lucumr",
                url="https://lucumr.pocoo.org/",
                snippet="Armin Ronacher's weblog.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000, **_kwargs: [
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
        lambda query, num_results, max_characters=1200, **_kwargs: [
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
        lambda urls, max_characters=5000, **_kwargs: [
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
        lambda query, num_results, max_characters=1200, **_kwargs: [
            ExaSearchResult(
                title="A" * 500,
                url="https://example.com/blog",
                snippet="B" * 1200,
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000, **_kwargs: [
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


def test_find_feed_options_uses_live_youtube_detection_over_stale_exa_feed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200, **_kwargs: [
            ExaSearchResult(
                title="Bg2 Pod",
                url="https://www.youtube.com/@bg2pod",
                snippet="Podcast page.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000, **_kwargs: [
            ExaContentResult(
                title="Bg2 Pod",
                url=urls[0],
                text=(
                    "Stale indexed feed URL: "
                    "https://www.youtube.com/feeds/videos.xml?channel_id=UC8P0dc0Zn2gf8L6tJi_k6xg"
                ),
            )
        ],
    )

    class _Response:
        def __init__(
            self,
            url: str,
            text: str,
            *,
            status_code: int = 200,
            content_type: str = "text/html; charset=utf-8",
        ) -> None:
            self.url = url
            self.text = text
            self.content = text.encode("utf-8")
            self.status_code = status_code
            self.headers = {"content-type": content_type}

    def _fake_head(
        self,
        url,
        headers=None,
        allow_statuses=None,
        log_client_errors=True,
        log_exceptions=True,
    ):
        return _Response(url, "", content_type="text/html; charset=utf-8")

    monkeypatch.setattr("app.services.http.HttpService.head", _fake_head)

    monkeypatch.setattr(
        "app.services.http.HttpService.fetch",
        lambda self, url, headers=None: (
            _Response(
                url,
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    "<feed xmlns='http://www.w3.org/2005/Atom'>"
                    "<title>Bg2 Pod</title>"
                    "</feed>"
                ),
                content_type="application/atom+xml",
            )
            if "feeds/videos.xml" in url
            else _Response(
                url,
                (
                    '<link rel="alternate" type="application/atom+xml" '
                    "href="
                    '"https://www.youtube.com/feeds/videos.xml?channel_id='
                    'UC-yRDvpR99LUc5l7i7jLzew" '
                    'title="Bg2 Pod">'
                ),
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.validate_feed_url",
        lambda self, url: {
            "feed_url": url,
            "feed_format": "atom",
            "title": "Chris Palmer SEO" if "UC8P0dc0Zn2gf8L6tJi_k6xg" in url else "Bg2 Pod",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.classify_feed_type",
        lambda self, **kwargs: FeedClassificationResult(
            feed_type="atom",
            confidence=0.9,
            reasoning="Validated YouTube channel feed.",
        ),
    )

    result = find_feed_options("BG2 podcast", limit=1)

    assert len(result.options) == 1
    option = result.options[0]
    assert option.title == "Bg2 Pod"
    assert (
        option.feed_url
        == "https://www.youtube.com/feeds/videos.xml?channel_id=UC-yRDvpR99LUc5l7i7jLzew"
    )
    assert option.site_url == "https://www.youtube.com/@bg2pod"


def test_find_feed_options_prefers_live_apple_podcast_link_over_generic_feed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200, **_kwargs: [
            ExaSearchResult(
                title="Training Data",
                url="https://sequoiacap.com/series/training-data/",
                snippet="Training Data is a podcast by Sequoia Capital.",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000, **_kwargs: [
            ExaContentResult(
                title="Training Data",
                url=urls[0],
                text="Stale generic feed: https://sequoiacap.com/feed",
            )
        ],
    )

    def _resolve_apple_feed(url: str) -> str | None:
        if "podcasts.apple.com" not in url and "itunes.apple.com" not in url:
            return None
        return SEQUOIA_TRAINING_DATA_FEED

    monkeypatch.setattr(
        "app.services.feed_detection.resolve_apple_podcast_feed_url",
        _resolve_apple_feed,
    )

    class _Response:
        def __init__(
            self,
            url: str,
            text: str,
            *,
            content_type: str = "text/html; charset=utf-8",
        ) -> None:
            self.url = url
            self.text = text
            self.content = text.encode("utf-8")
            self.status_code = 200
            self.headers = {"content-type": content_type}

    fetched_urls: list[str] = []

    def _fake_head(self, url, **_kwargs):  # noqa: ANN001
        return _Response(url, "", content_type="application/rss+xml")

    def _fake_fetch(self, url, headers=None, **_kwargs):  # noqa: ANN001
        del headers
        fetched_urls.append(url)
        if url == SEQUOIA_TRAINING_DATA_FEED:
            return _Response(
                url,
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    "<rss><channel><title>Training Data</title></channel></rss>"
                ),
                content_type="application/rss+xml",
            )
        if url == "https://sequoiacap.com/feed":
            return _Response(
                url,
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    "<rss><channel><title>Sequoia Capital</title></channel></rss>"
                ),
                content_type="application/rss+xml",
            )
        return _Response(
            url,
            SEQUOIA_TRAINING_DATA_HTML,
        )

    monkeypatch.setattr("app.services.http.HttpService.head", _fake_head)
    monkeypatch.setattr("app.services.http.HttpService.fetch", _fake_fetch)

    result = find_feed_options("Training Data Sequoia Capital podcast RSS feed", limit=1)

    assert len(result.options) == 1
    option = result.options[0]
    assert option.title == "Training Data"
    assert option.feed_url == SEQUOIA_TRAINING_DATA_FEED
    assert option.feed_type == "podcast_rss"
    assert "https://sequoiacap.com/feed" not in fetched_urls


def test_find_feed_options_prefers_podcast_rss_for_podcast_queries(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_search",
        lambda query, num_results, max_characters=1200, **_kwargs: [
            ExaSearchResult(
                title="Bg2 Pod YouTube",
                url="https://www.youtube.com/@bg2pod",
                snippet="Follow on YouTube.",
            ),
            ExaSearchResult(
                title="BG2 Pod",
                url="https://bg2pod.com",
                snippet="Podcast RSS: https://feeds.megaphone.fm/bg2pod",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.assistant_feed_finder.exa_get_contents",
        lambda urls, max_characters=5000, **_kwargs: [
            ExaContentResult(
                title="Bg2 Pod YouTube",
                url=urls[0],
                text="https://www.youtube.com/feeds/videos.xml?channel_id=UC-yRDvpR99LUc5l7i7jLzew",
            ),
            ExaContentResult(
                title="BG2 Pod",
                url=urls[1],
                text="https://feeds.megaphone.fm/bg2pod",
            ),
        ],
    )

    class _Response:
        def __init__(self, url: str, text: str) -> None:
            self.url = url
            self.text = text

    monkeypatch.setattr(
        "app.services.http.HttpService.fetch",
        lambda self, url, headers=None: _Response(url, "<html></html>"),
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.detect_from_links",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.validate_feed_url",
        lambda self, url: {
            "feed_url": url,
            "feed_format": "atom" if "youtube.com" in url else "rss",
            "title": "Bg2 Pod YouTube" if "youtube.com" in url else "BG2 Pod",
        },
    )
    monkeypatch.setattr(
        "app.services.feed_detection.FeedDetector.classify_feed_type",
        lambda self, **kwargs: FeedClassificationResult(
            feed_type="atom" if "youtube.com" in kwargs["feed_url"] else "podcast_rss",
            confidence=0.95,
            reasoning="Validated feed.",
        ),
    )

    result = find_feed_options("BG2 podcast", limit=2)

    assert len(result.options) == 2
    assert result.options[0].feed_type == "podcast_rss"
    assert result.options[0].feed_url == "https://feeds.megaphone.fm/bg2pod"
