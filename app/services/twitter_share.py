"""Tweet URL helpers shared by X ingestion paths."""

from __future__ import annotations

import re

TWEET_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/(?:i/)?(?:status|[^/]+/status)/(\d+)",
    re.IGNORECASE,
)


def extract_tweet_id(url: str) -> str | None:
    """Extract the tweet ID from an X or Twitter status URL."""
    match = TWEET_URL_REGEX.search(url)
    return match.group(1) if match else None


def is_tweet_url(url: str) -> bool:
    """Return whether a URL identifies an X or Twitter status."""
    return extract_tweet_id(url) is not None


def canonical_tweet_url(tweet_id: str) -> str:
    """Build the canonical X URL for a tweet ID."""
    return f"https://x.com/i/status/{tweet_id}"
