"""Shared remote-feed download and parsing boundary."""

from typing import Any

import feedparser

from app.services.http import get_http_service

FEED_REQUEST_HEADERS = {
    "Accept": ("application/atom+xml,application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8")
}


def fetch_and_parse_feed(url: str) -> Any:
    """Fetch a configured feed with the shared pipeline HTTP client, then parse it."""
    response = get_http_service().fetch_bounded_public(
        url,
        headers=FEED_REQUEST_HEADERS,
        log_client_errors=False,
        log_exceptions=False,
    )
    response_headers = {key.lower(): value for key, value in response.headers.items()}
    response_headers["content-location"] = str(response.url)
    return feedparser.parse(response.content, response_headers=response_headers)
