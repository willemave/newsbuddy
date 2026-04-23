"""Backward-compatible shim for the HackerNews aggregator scraper.

The implementation now lives in
``app.scraping.aggregators.hackernews.HackerNewsAggregatorScraper`` and is
configured via ``config/aggregators.yml``. Existing imports of
``HackerNewsUnifiedScraper`` continue to work for tests and any in-flight
callers; we keep the legacy ``httpx`` import alias here so existing tests can
patch ``app.scraping.hackernews_unified.httpx.Client``.
"""

from __future__ import annotations

import httpx  # noqa: F401  (re-exported for legacy test patching)

from app.scraping.aggregators.config import HackerNewsAggregator
from app.scraping.aggregators.hackernews import HackerNewsAggregatorScraper


class HackerNewsUnifiedScraper(HackerNewsAggregatorScraper):
    """Backward-compatible HackerNews scraper."""

    def __init__(self) -> None:
        super().__init__(
            settings=HackerNewsAggregator(
                key=self.KEY,
                name=self.DISPLAY_NAME,
                kind="hackernews",
                limit=10,  # legacy default — matched the old hard-coded slice
            )
        )


__all__ = ["HackerNewsUnifiedScraper"]
