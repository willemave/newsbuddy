from contextlib import contextmanager
from unittest.mock import Mock

import httpx

from app.scraping import feed_fetch


def test_fetch_and_parse_feed_downloads_bytes_inside_e2b(monkeypatch) -> None:
    payload = b"<rss><channel><title>Example</title></channel></rss>"
    request = httpx.Request("GET", "https://example.com/feed.xml")
    response = httpx.Response(
        200,
        content=payload,
        headers={"Content-Type": "application/rss+xml"},
        request=request,
    )
    http_service = Mock()
    http_service.fetch.return_value = response
    parsed_feed = object()
    parse = Mock(return_value=parsed_feed)

    @contextmanager
    def _runtime(**kwargs):
        assert kwargs == {"user_id": 7, "execution_id": 42}
        yield http_service

    monkeypatch.setattr(feed_fetch, "sandboxed_http_service", _runtime)
    monkeypatch.setattr(feed_fetch.feedparser, "parse", parse)

    result = feed_fetch.fetch_and_parse_feed(
        str(request.url),
        user_id=7,
        execution_id=42,
    )

    assert result is parsed_feed
    http_service.fetch.assert_called_once_with(
        str(request.url),
        headers=feed_fetch.FEED_REQUEST_HEADERS,
    )
    parse.assert_called_once_with(
        payload,
        response_headers={
            "content-type": "application/rss+xml",
            "content-length": str(len(payload)),
            "content-location": str(request.url),
        },
    )
