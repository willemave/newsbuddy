from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.services import firecrawl_client


def test_scrape_url_with_firecrawl_returns_markdown(monkeypatch) -> None:
    monkeypatch.setattr(
        firecrawl_client,
        "get_settings",
        lambda: SimpleNamespace(
            firecrawl_api_key="fc-test",
            firecrawl_timeout_seconds=12,
        ),
    )
    record_usage = Mock()
    monkeypatch.setattr(firecrawl_client, "record_vendor_usage_out_of_band", record_usage)

    def fake_post(url, *, headers, json, timeout, follow_redirects):  # noqa: ANN001
        assert url == firecrawl_client.FIRECRAWL_SCRAPE_URL
        assert headers["Authorization"] == "Bearer fc-test"
        assert json["url"] == "https://example.com/story"
        assert json["formats"] == ["markdown"]
        assert json["onlyMainContent"] is True
        assert json["proxy"] == "auto"
        assert timeout == 12.0
        assert follow_redirects is True
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# Example Story\n\nRecovered body.",
                    "metadata": {
                        "title": "Example Story",
                        "sourceURL": "https://example.com/story",
                        "publishedTime": "2026-04-23T10:00:00Z",
                    },
                },
            },
        )

    monkeypatch.setattr(firecrawl_client.httpx, "post", fake_post)

    result = firecrawl_client.scrape_url_with_firecrawl(
        "https://example.com/story",
        telemetry={"content_id": 123},
    )

    assert result.markdown == "# Example Story\n\nRecovered body."
    assert result.title == "Example Story"
    assert result.source_url == "https://example.com/story"
    assert result.published_time == "2026-04-23T10:00:00Z"
    record_usage.assert_called_once()
    assert record_usage.call_args.kwargs["provider"] == "firecrawl"
    assert record_usage.call_args.kwargs["model"] == "scrape-v2"
    assert record_usage.call_args.kwargs["feature"] == "html_extraction"
    assert record_usage.call_args.kwargs["usage"] == {"request_count": 1, "resource_count": 1}


def test_scrape_url_with_firecrawl_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        firecrawl_client,
        "get_settings",
        lambda: SimpleNamespace(
            firecrawl_api_key=None,
            firecrawl_timeout_seconds=12,
        ),
    )

    with pytest.raises(firecrawl_client.FirecrawlUnavailableError):
        firecrawl_client.scrape_url_with_firecrawl("https://example.com/story")


def test_scrape_url_with_firecrawl_rejects_empty_markdown(monkeypatch) -> None:
    monkeypatch.setattr(
        firecrawl_client,
        "get_settings",
        lambda: SimpleNamespace(
            firecrawl_api_key="fc-test",
            firecrawl_timeout_seconds=12,
        ),
    )

    monkeypatch.setattr(firecrawl_client, "record_vendor_usage_out_of_band", Mock())
    monkeypatch.setattr(
        firecrawl_client.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(200, json={"data": {"markdown": ""}}),
    )

    with pytest.raises(firecrawl_client.FirecrawlRequestError):
        firecrawl_client.scrape_url_with_firecrawl("https://example.com/story")
