from unittest.mock import Mock

import httpx

from app.scraping import feed_fetch


def test_fetch_and_parse_feed_uses_shared_pipeline_http_client(monkeypatch) -> None:
    payload = b"<rss><channel><title>Example</title></channel></rss>"
    request = httpx.Request("GET", "https://example.com/feed.xml")
    response = httpx.Response(
        200,
        content=payload,
        headers={"Content-Type": "application/rss+xml"},
        request=request,
    )
    http_service = Mock()
    http_service.fetch_bounded_public.return_value = response
    parsed_feed = object()
    parse = Mock(return_value=parsed_feed)

    monkeypatch.setattr(feed_fetch, "get_http_service", lambda: http_service)
    monkeypatch.setattr(feed_fetch.feedparser, "parse", parse)

    result = feed_fetch.fetch_and_parse_feed(str(request.url))

    assert result is parsed_feed
    http_service.fetch_bounded_public.assert_called_once_with(
        str(request.url),
        headers=feed_fetch.FEED_REQUEST_HEADERS,
        max_response_bytes=None,
        log_client_errors=False,
        log_exceptions=False,
    )
    parse.assert_called_once_with(
        payload,
        response_headers={
            "content-type": "application/rss+xml",
            "content-length": str(len(payload)),
            "content-location": str(request.url),
        },
    )
