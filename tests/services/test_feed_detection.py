from types import SimpleNamespace

from app.services import feed_detection


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


def test_build_candidate_feed_urls_includes_root_and_section() -> None:
    candidates = feed_detection._build_candidate_feed_urls("https://example.com/blog/2025/post")

    assert "https://example.com/rss.xml" in candidates
    assert "https://example.com/blog/rss.xml" in candidates


def test_validate_feed_candidate_parses_rss(monkeypatch) -> None:
    rss_payload = b'<?xml version="1.0"?><rss><channel><title>Test Feed</title></channel></rss>'

    class DummyHttpService:
        def head(self, url: str, allow_statuses=None):  # noqa: ANN001
            return SimpleNamespace(status_code=200)

        def fetch(self, url: str):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "application/rss+xml"},
                content=rss_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        use_exa_search=False,
        http_service=DummyHttpService(),
    )

    result = detector._validate_feed_candidate("https://example.com/rss.xml")

    assert result == {
        "feed_url": "https://example.com/rss.xml",
        "feed_format": "rss",
        "title": "Test Feed",
    }


def test_validate_feed_candidate_rejects_html_article() -> None:
    html_payload = b"<html><head><title>Example Article</title></head><body>Hello</body></html>"

    class DummyHttpService:
        def head(self, url: str, allow_statuses=None):  # noqa: ANN001
            return SimpleNamespace(status_code=200)

        def fetch(self, url: str):  # noqa: ANN001
            return SimpleNamespace(
                headers={"content-type": "text/html; charset=utf-8"},
                content=html_payload,
            )

    detector = feed_detection.FeedDetector(
        use_llm=False,
        use_exa_search=False,
        http_service=DummyHttpService(),
    )

    result = detector._validate_feed_candidate("https://example.com/articles/post")

    assert result is None
