"""Shared remote-feed download and parsing boundary."""

from typing import Any

import feedparser

from app.services.feed_research_runtime import sandboxed_http_service

FEED_REQUEST_HEADERS = {
    "Accept": ("application/atom+xml,application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8")
}


def fetch_and_parse_feed(
    url: str,
    *,
    user_id: int,
    execution_id: int | None = None,
) -> Any:
    """Fetch a feed inside E2B, then parse the returned bytes on the host."""
    with sandboxed_http_service(
        user_id=user_id,
        execution_id=execution_id,
    ) as http_service:
        response = http_service.fetch(url, headers=FEED_REQUEST_HEADERS)
    response_headers = {key.lower(): value for key, value in response.headers.items()}
    response_headers["content-location"] = str(response.url)
    return feedparser.parse(response.content, response_headers=response_headers)
