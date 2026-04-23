"""Shared HTML-grouped parsing used by SciURLs and FinURLs.

Both sites use a single homepage template that lists news items grouped by
source. Each source block looks like::

    <div class="source">
        <h3 class="source-title"><a href="...">Source Name</a></h3>
        <ul class="story-list">
            <li><a href="https://example.com/article">Article title</a></li>
            ...
        </ul>
    </div>

The exact CSS class names vary between SciURLs and FinURLs, so subclasses
declare ``SOURCE_BLOCK_SELECTOR`` and ``SOURCE_HEADING_SELECTOR``. The base
parser is intentionally permissive: it falls back to scanning every list item
under each block.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.config import HtmlGroupedAggregator

logger = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class HtmlGroupedAggregatorScraper(AggregatorScraper):
    """Base scraper for SciURLs/FinURLs-style HTML aggregators."""

    #: BeautifulSoup CSS selector for one source block on the homepage.
    SOURCE_BLOCK_SELECTOR: ClassVar[str] = ".source"

    #: Selector (relative to a source block) that locates the source title.
    SOURCE_HEADING_SELECTOR: ClassVar[str] = ".source-title"

    #: Selector (relative to a source block) for one article entry.
    ARTICLE_LINK_SELECTOR: ClassVar[str] = "li a"

    def __init__(self, settings: HtmlGroupedAggregator) -> None:
        super().__init__(name=settings.name)
        self.settings = settings
        self.base_url = str(settings.url).rstrip("/")

    def scrape(self) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                self.base_url,
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network guard
            logger.exception("Failed to fetch %s homepage: %s", self.DISPLAY_NAME, exc)
            return []

        return self.parse(response.text)

    def parse(self, html: str) -> list[dict[str, Any]]:
        """Parse the homepage HTML into normalized aggregator items."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        blocks = soup.select(self.SOURCE_BLOCK_SELECTOR)
        for block in blocks:
            source_name, source_url = self._extract_source(block)
            for anchor in block.select(self.ARTICLE_LINK_SELECTOR):
                if not isinstance(anchor, Tag):
                    continue
                href = anchor.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                article_url = self._normalize_url(urljoin(self.base_url + "/", href.strip()))
                if not article_url or article_url in seen_urls:
                    continue
                if self.extract_domain(article_url) == self.extract_domain(self.base_url):
                    # Skip in-site links (categories, source pages, "more" links).
                    continue
                title = anchor.get_text(" ", strip=True)
                if not title:
                    continue
                seen_urls.add(article_url)
                items.append(self._build_item(article_url, title, source_name, source_url))
                if len(items) >= self.settings.limit:
                    return items
        return items

    def _extract_source(self, block: Tag) -> tuple[str | None, str | None]:
        heading = block.select_one(self.SOURCE_HEADING_SELECTOR)
        if heading is None:
            return None, None
        link = heading.find("a")
        if isinstance(link, Tag):
            name = link.get_text(" ", strip=True) or None
            href = link.get("href")
            url = (
                self._normalize_url(urljoin(self.base_url + "/", href.strip()))
                if isinstance(href, str) and href.strip()
                else None
            )
            return name, url
        return heading.get_text(" ", strip=True) or None, None

    def _build_item(
        self,
        article_url: str,
        title: str,
        source_name: str | None,
        source_url: str | None,
    ) -> dict[str, Any]:
        domain = self.extract_domain(article_url)
        display_source = source_name or domain
        return {
            "url": article_url,
            "title": title,
            "content_type": ContentType.NEWS,
            "is_aggregate": False,
            "metadata": {
                "platform": self.KEY,
                "source": display_source,
                "article": {
                    "url": article_url,
                    "title": title,
                    "source_domain": domain,
                },
                "aggregator": {
                    "key": self.KEY,
                    "name": self.settings.name,
                    "metadata": {
                        "source_name": source_name,
                        "source_url": source_url,
                    },
                },
                "discussion_url": None,
                "excerpt": None,
                "discovery_time": self.now_iso(),
            },
        }
