from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.models.db import VendorUsageRecord
from app.services import feed_detection

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
SEQUOIA_TRAINING_DATA_HTML = (FIXTURES_DIR / "sequoia_training_data_podcast_links.html").read_text()
SEQUOIA_TRAINING_DATA_FEED = "https://feeds.megaphone.fm/trainingdata"
SEQUOIA_APPLE_SHOW_URL = "https://podcasts.apple.com/us/podcast/training-data/id1750736528"
SEQUOIA_APPLE_EPISODE_URL = (
    "https://podcasts.apple.com/us/podcast/"
    "delphis-dara-ladjevardian-how-ai-digital-minds-can/"
    "id1750736528?i=1000721630464"
)
SEQUOIA_FEED_LINK = {
    "feed_url": SEQUOIA_TRAINING_DATA_FEED,
    "feed_format": "rss",
    "title": "",
}


def _extract_podcast_links_with_stubbed_apple_lookup(
    monkeypatch,
    html: str,
) -> tuple[list[dict[str, str]], list[str]]:
    resolved_urls: list[str] = []

    def _resolve(url: str) -> str | None:
        if "podcasts.apple.com" not in url and "itunes.apple.com" not in url:
            return None
        resolved_urls.append(url)
        return SEQUOIA_TRAINING_DATA_FEED

    monkeypatch.setattr(feed_detection, "resolve_apple_podcast_feed_url", _resolve)

    links = feed_detection.extract_podcast_feed_links_from_anchors(
        html,
        "https://sequoiacap.com/series/training-data/",
    )
    return links, resolved_urls


def test_extract_feed_links_from_anchors_resolves_relative_url() -> None:
    html = '<a href="/rss.xml">RSS</a>'
    links = feed_detection.extract_feed_links_from_anchors(
        html,
        "https://example.com/blog/2025/post",
    )

    assert links == [
        {
            "feed_url": "https://example.com/rss.xml",
            "feed_format": "rss",
            "title": "RSS",
        }
    ]


def test_extract_feed_links_uses_urljoin_for_parent_relative_urls() -> None:
    links = feed_detection.extract_feed_links(
        '<link rel="alternate" type="application/atom+xml" href="../feed.xml">',
        "https://example.com/blog/posts/article",
    )

    assert links[0]["feed_url"] == "https://example.com/blog/feed.xml"


def test_feed_link_titles_are_cleaned_for_display() -> None:
    link_tag_results = feed_detection.extract_feed_links(
        '<link rel="alternate" type="application/rss+xml" href="/feed" '
        'title="Research &amp; Ideas &raquo;">',
        "https://example.com/articles/post",
    )
    anchor_results = feed_detection.extract_feed_links_from_anchors(
        '<a href="/feed">Research &amp; Ideas &raquo;</a>',
        "https://example.com/articles/post",
    )

    assert link_tag_results[0]["title"] == "Research & Ideas »"
    assert anchor_results[0]["title"] == "Research & Ideas »"


def test_generic_page_linking_to_substack_is_not_classified_as_substack() -> None:
    result = feed_detection._classify_feed_type_heuristic(
        "https://example.com/feed.atom",
        "https://example.com/",
        "Example",
        '<a href="https://writer.substack.com/p/story">Related story</a>',
    )

    assert result.feed_type == "atom"


def test_custom_domain_substack_requires_platform_html_marker() -> None:
    result = feed_detection._classify_feed_type_heuristic(
        "https://newsletter.example/feed",
        "https://newsletter.example/",
        "Example Newsletter",
        '<link rel="preconnect" href="https://substackcdn.com">',
    )

    assert result.feed_type == "substack"


def test_extract_podcast_feed_links_handles_sequoia_stream_links_structure(
    monkeypatch,
) -> None:
    html = SEQUOIA_TRAINING_DATA_HTML.split('<div class="podcast-card__platforms">')[0]
    links, resolved_urls = _extract_podcast_links_with_stubbed_apple_lookup(monkeypatch, html)

    assert links == [SEQUOIA_FEED_LINK]
    assert resolved_urls == [SEQUOIA_APPLE_SHOW_URL]


def test_extract_podcast_feed_links_handles_sequoia_episode_card_structure(
    monkeypatch,
) -> None:
    html = SEQUOIA_TRAINING_DATA_HTML.split('<div class="podcast-card__platforms">')[1]
    links, resolved_urls = _extract_podcast_links_with_stubbed_apple_lookup(monkeypatch, html)

    assert links == [SEQUOIA_FEED_LINK]
    assert resolved_urls == [SEQUOIA_APPLE_EPISODE_URL]


def test_extract_podcast_feed_links_dedupes_sequoia_show_and_episode_links(
    monkeypatch,
) -> None:
    links, resolved_urls = _extract_podcast_links_with_stubbed_apple_lookup(
        monkeypatch,
        SEQUOIA_TRAINING_DATA_HTML,
    )

    assert links == [SEQUOIA_FEED_LINK]
    assert resolved_urls == [
        SEQUOIA_APPLE_SHOW_URL,
        SEQUOIA_APPLE_EPISODE_URL,
    ]


def test_detect_from_html_resolves_sequoia_fixture_before_generic_candidates(
    monkeypatch,
) -> None:
    rss_payload = b'<?xml version="1.0"?><rss><channel><title>Training Data</title></channel></rss>'
    fetched_urls: list[str] = []

    monkeypatch.setattr(
        feed_detection,
        "resolve_apple_podcast_feed_url",
        lambda url: (
            SEQUOIA_TRAINING_DATA_FEED
            if "podcasts.apple.com" in url or "itunes.apple.com" in url
            else None
        ),
    )

    class DummyHttpService:
        def fetch(self, url: str, **_kwargs):  # noqa: ANN001
            fetched_urls.append(url)
            return SimpleNamespace(
                url=url,
                headers={"content-type": "application/rss+xml"},
                content=rss_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector.detect_from_html(
        SEQUOIA_TRAINING_DATA_HTML,
        "https://sequoiacap.com/series/training-data/",
        page_title="Training Data",
        force_detect=True,
    )

    assert result == {
        "detected_feed": {
            "url": SEQUOIA_TRAINING_DATA_FEED,
            "type": "podcast_rss",
            "title": "Training Data",
            "format": "rss",
        },
        "all_detected_feeds": None,
    }
    assert fetched_urls == [SEQUOIA_TRAINING_DATA_FEED]


def test_build_candidate_feed_urls_includes_root_and_section() -> None:
    candidates = feed_detection._build_candidate_feed_urls("https://example.com/blog/2025/post")

    assert "https://example.com/rss.xml" in candidates
    assert "https://example.com/blog/rss.xml" in candidates


def test_validate_feed_candidate_parses_rss(monkeypatch) -> None:
    rss_payload = b'<?xml version="1.0"?><rss><channel><title>Test Feed</title></channel></rss>'

    class DummyHttpService:
        def fetch(self, url: str, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "application/rss+xml"},
                content=rss_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/rss.xml")

    assert result == {
        "feed_url": "https://example.com/rss.xml",
        "feed_format": "rss",
        "title": "Test Feed",
    }


def test_validate_feed_candidate_cleans_feed_title() -> None:
    rss_payload = (
        b'<?xml version="1.0"?><rss><channel><title>Research &amp;amp; Ideas</title>'
        b"</channel></rss>"
    )

    class DummyHttpService:
        def fetch(self, url: str, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "application/rss+xml"},
                content=rss_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/rss.xml")

    assert result is not None
    assert result["title"] == "Research & Ideas"


def test_validate_feed_candidate_rejects_html_article() -> None:
    html_payload = b"<html><head><title>Example Article</title></head><body>Hello</body></html>"

    class DummyHttpService:
        def fetch(self, url: str, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "text/html; charset=utf-8"},
                content=html_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/articles/post")

    assert result is None


def test_validate_feed_candidate_rejects_generic_xml_without_feed_semantics() -> None:
    class DummyHttpService:
        def fetch(self, url: str, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "application/xml"},
                content=b"<?xml version='1.0'?><catalog><item>Not a feed</item></catalog>",
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    assert detector.validate_feed_url("https://example.com/catalog.xml") is None


def test_validate_feed_candidate_uses_one_quiet_get_probe() -> None:
    rss_payload = b'<?xml version="1.0"?><rss><channel><title>Test Feed</title></channel></rss>'
    observed: dict[str, object] = {}

    class DummyHttpService:
        def fetch(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            *,
            log_client_errors: bool = True,
            log_exceptions: bool = True,
        ):  # noqa: ANN001
            assert headers is None
            observed["fetch"] = {
                "url": url,
                "log_client_errors": log_client_errors,
                "log_exceptions": log_exceptions,
            }
            return SimpleNamespace(
                headers={"content-type": "application/rss+xml"},
                content=rss_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/rss.xml")

    assert result == {
        "feed_url": "https://example.com/rss.xml",
        "feed_format": "rss",
        "title": "Test Feed",
    }
    assert observed["fetch"] == {
        "url": "https://example.com/rss.xml",
        "log_client_errors": False,
        "log_exceptions": False,
    }


def test_classify_feed_type_with_llm_persists_usage(
    db_session,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db

    class _Agent:
        def run_sync(self, _prompt, model_settings=None):  # noqa: ANN001
            del model_settings
            return SimpleNamespace(
                output=feed_detection.FeedClassificationResult(
                    feed_type="atom",
                    confidence=0.9,
                    reasoning="Looks like a standard blog feed.",
                ),
                usage=SimpleNamespace(
                    input_tokens=40,
                    output_tokens=12,
                    total_tokens=52,
                ),
            )

    monkeypatch.setattr(feed_detection, "get_basic_agent", lambda *args, **kwargs: _Agent())

    result = feed_detection.classify_feed_type_with_llm(
        "https://example.com/feed.xml",
        "https://example.com",
        "Example Feed",
        db=db_session,
        usage_persist={
            "feature": "feed_detection",
            "operation": "feed_detection.classify_feed_type",
            "source": "queue",
            "content_id": 99,
        },
    )

    assert result is not None
    db_session.commit()
    row = db_session.query(VendorUsageRecord).one()
    assert row.feature == "feed_detection"
    assert row.operation == "feed_detection.classify_feed_type"
    assert row.content_id == 99
    assert row.total_tokens == 52
