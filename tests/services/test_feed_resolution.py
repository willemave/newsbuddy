from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.services.feed_resolution import (
    extract_candidate_feed_urls,
    resolve_feed_candidate,
)


def test_extract_candidate_feed_urls_includes_feed_like_site_and_text_urls() -> None:
    urls = extract_candidate_feed_urls(
        site_url="https://example.com/feed.xml",
        page_text=(
            "Listen here https://feeds.example.com/show.rss and subscribe at "
            "https://feeds.example.com/show.rss."
        ),
    )

    assert urls == [
        "https://example.com/feed.xml",
        "https://feeds.example.com/show.rss",
    ]


def test_resolve_feed_candidate_returns_validated_candidate() -> None:
    class DummyDetector:
        def validate_feed_url(self, feed_url: str) -> dict[str, str] | None:
            if feed_url == "https://example.com/feed.xml":
                return {
                    "feed_url": feed_url,
                    "feed_format": "rss",
                    "title": "Example Feed",
                }
            return None

    result = resolve_feed_candidate(
        detector=cast(Any, DummyDetector()),
        title="Example Feed",
        site_url="https://example.com",
        candidate_feed_urls=["https://example.com/feed.xml"],
    )

    assert result == {
        "feed_url": "https://example.com/feed.xml",
        "feed_format": "rss",
        "title": "Example Feed",
    }


def test_resolve_feed_candidate_repairs_invalid_candidate_from_site_html() -> None:
    class DummyDetector:
        def __init__(self) -> None:
            self.http_service = SimpleNamespace(
                fetch=lambda url: SimpleNamespace(
                    url="https://example.com/show",
                    text="<html>podcast page</html>",
                )
            )

        def validate_feed_url(self, feed_url: str) -> dict[str, str] | None:
            if feed_url == "https://example.com/fixed.rss":
                return {
                    "feed_url": feed_url,
                    "feed_format": "rss",
                    "title": "Creative Coding Weekly",
                }
            return None

        def detect_from_links(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            assert kwargs["page_url"] == "https://example.com/show"
            assert kwargs["html_content"] == "<html>podcast page</html>"
            return {
                "detected_feed": {
                    "url": "https://example.com/fixed.rss",
                    "format": "rss",
                    "title": "Creative Coding Weekly",
                }
            }

    result = resolve_feed_candidate(
        detector=cast(Any, DummyDetector()),
        title="Creative Coding Weekly",
        site_url="https://example.com/show",
        candidate_feed_urls=["https://example.com/bad.rss"],
    )

    assert result == {
        "feed_url": "https://example.com/fixed.rss",
        "feed_format": "rss",
        "title": "Creative Coding Weekly",
    }


def test_resolve_feed_candidate_can_prefer_site_discovery_over_guessed_feed() -> None:
    class DummyDetector:
        def __init__(self) -> None:
            self.http_service = SimpleNamespace(
                fetch=lambda url: SimpleNamespace(
                    url="https://example.com/show",
                    text="<html>podcast page</html>",
                )
            )

        def validate_feed_url(self, feed_url: str) -> dict[str, str] | None:
            if feed_url == "https://example.com/wrong.rss":
                return {
                    "feed_url": feed_url,
                    "feed_format": "rss",
                    "title": "Completely Different Show",
                }
            if feed_url == "https://example.com/fixed.rss":
                return {
                    "feed_url": feed_url,
                    "feed_format": "rss",
                    "title": "Creative Coding Weekly",
                }
            return None

        def detect_from_links(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return {
                "detected_feed": {
                    "url": "https://example.com/fixed.rss",
                    "format": "rss",
                    "title": "Creative Coding Weekly",
                }
            }

    result = resolve_feed_candidate(
        detector=cast(Any, DummyDetector()),
        title="Creative Coding Weekly",
        site_url="https://example.com/show",
        candidate_feed_urls=["https://example.com/wrong.rss"],
        prefer_site_discovery=True,
    )

    assert result == {
        "feed_url": "https://example.com/fixed.rss",
        "feed_format": "rss",
        "title": "Creative Coding Weekly",
    }


def test_resolve_feed_candidate_fetches_site_quietly_when_supported() -> None:
    observed: dict[str, object] = {}

    class DummyHttpService:
        def fetch(
            self,
            url: str,
            *,
            log_client_errors: bool = True,
            log_exceptions: bool = True,
        ):  # noqa: ANN001
            observed["url"] = url
            observed["log_client_errors"] = log_client_errors
            observed["log_exceptions"] = log_exceptions
            return SimpleNamespace(
                url="https://example.com/show",
                text="<html>podcast page</html>",
            )

    class DummyDetector:
        def __init__(self) -> None:
            self.http_service = DummyHttpService()

        def validate_feed_url(self, feed_url: str) -> dict[str, str] | None:
            return None

        def detect_from_links(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return {
                "detected_feed": {
                    "url": "https://example.com/fixed.rss",
                    "format": "rss",
                    "title": "Creative Coding Weekly",
                }
            }

    result = resolve_feed_candidate(
        detector=cast(Any, DummyDetector()),
        title="Creative Coding Weekly",
        site_url="https://example.com/show",
        candidate_feed_urls=[],
    )

    assert result == {
        "feed_url": "https://example.com/fixed.rss",
        "feed_format": "rss",
        "title": "Creative Coding Weekly",
    }
    assert observed == {
        "url": "https://example.com/show",
        "log_client_errors": False,
        "log_exceptions": False,
    }
