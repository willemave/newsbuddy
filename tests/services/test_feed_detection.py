from types import SimpleNamespace
from typing import Any, cast

from app.models.schema import VendorUsageRecord
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
        http_service=cast(Any, DummyHttpService()),
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
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/articles/post")

    assert result is None


def test_validate_feed_candidate_uses_quiet_probe_flags() -> None:
    rss_payload = b'<?xml version="1.0"?><rss><channel><title>Test Feed</title></channel></rss>'
    observed: dict[str, object] = {}

    class DummyHttpService:
        def head(
            self,
            url: str,
            allow_statuses=None,
            *,
            log_client_errors: bool = True,
            log_exceptions: bool = True,
        ):  # noqa: ANN001
            observed["head"] = {
                "url": url,
                "allow_statuses": allow_statuses,
                "log_client_errors": log_client_errors,
                "log_exceptions": log_exceptions,
            }
            return SimpleNamespace(status_code=200)

        def fetch(
            self,
            url: str,
            *,
            log_client_errors: bool = True,
            log_exceptions: bool = True,
        ):  # noqa: ANN001
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
        use_exa_search=False,
        http_service=cast(Any, DummyHttpService()),
    )

    result = detector._validate_feed_candidate("https://example.com/rss.xml")

    assert result == {
        "feed_url": "https://example.com/rss.xml",
        "feed_format": "rss",
        "title": "Test Feed",
    }
    assert observed["head"] == {
        "url": "https://example.com/rss.xml",
        "allow_statuses": {405},
        "log_client_errors": False,
        "log_exceptions": False,
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
                usage=lambda: SimpleNamespace(
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
