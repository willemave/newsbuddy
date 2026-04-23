"""Shared base class and helpers for news aggregator scrapers.

Each aggregator (HN, Techmeme, Mediagazer, Memeorandum, SciURLs, FinURLs,
Brutalist Report) gets its own ``AggregatorScraper`` subclass with bespoke
parsing. They all converge on the same canonical scraped-item shape so the
ingestion layer (``app.services.news_ingestion``) and the visibility filter
(``app.services.news_feed``) can treat them uniformly.

Canonical scraped item shape::

    {
        "url": "<primary article URL>",
        "title": "<headline>",
        "content_type": ContentType.NEWS,
        "is_aggregate": False,
        "metadata": {
            "platform": "<aggregator key, e.g. 'techmeme'>",
            "source": "<article source domain or label>",
            "article": {"url": ..., "title": ..., "source_domain": ...},
            "aggregator": {
                "key": "<aggregator key, mirrors metadata.platform>",
                "name": "<human display name, e.g. 'Techmeme'>",
                "topic": "<optional topic for multi-topic aggregators>",
                ...
            },
            "discussion_url": "<optional cluster permalink>",
            "excerpt": "<optional short summary>",
            "discovery_time": "<ISO timestamp>",
        },
    }
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlparse

from app.scraping.base import BaseScraper


class AggregatorScraper(BaseScraper):
    """Base class for all news aggregator scrapers.

    Subclasses must set ``KEY`` (the aggregator identifier persisted in
    ``metadata.platform`` and used for visibility filtering) and ``DISPLAY_NAME``
    (the human-friendly label used as ``BaseScraper.name`` and surfaced in the
    onboarding UI).
    """

    KEY: ClassVar[str] = ""
    DISPLAY_NAME: ClassVar[str] = ""

    def __init__(self, name: str | None = None) -> None:
        if not self.KEY:
            raise ValueError(f"{type(self).__name__} must set KEY")
        if not self.DISPLAY_NAME:
            raise ValueError(f"{type(self).__name__} must set DISPLAY_NAME")
        super().__init__(name or self.DISPLAY_NAME)

    @abstractmethod
    def scrape(self) -> list[dict[str, Any]]:  # pragma: no cover - abstract
        """Return the canonical list of scraped items."""

    # ------------------------------------------------------------------
    # Helpers shared across aggregator subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def now_iso() -> str:
        """Return an ISO-8601 timestamp for ``discovery_time`` defaults."""
        return datetime.now(UTC).isoformat()

    @staticmethod
    def extract_domain(url: str) -> str:
        """Return the lowercase domain (without ``www.``) for a URL."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return ""
        domain = (parsed.netloc or "").lower()
        return domain[4:] if domain.startswith("www.") else domain
