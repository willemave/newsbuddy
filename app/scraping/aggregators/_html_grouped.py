"""Shared HTML-grouped parsing used by SciURLs and FinURLs.

Both sites use a single homepage template that lists news items in publisher
cards. Each publisher block looks like::

    <div class="publisher-block">
        <div class="publisher-header">...</div>
        <div class="publisher-link">
            <a class="article-link" href="https://example.com/article">Article title</a>
        </div>
    </div>

Subclasses declare the selectors so each site retains an explicit boundary.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.models.contracts import ContentType
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.config import HtmlGroupedAggregator
from app.services.agent_vm_runtime import SYSTEM_USER_ID
from app.services.feed_research_runtime import sandboxed_http_service

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
        with sandboxed_http_service(user_id=SYSTEM_USER_ID) as http_service:
            response = http_service.fetch(
                self.base_url,
                headers={"User-Agent": USER_AGENT},
            )
        items = self.parse(response.text)
        if not items:
            raise ValueError(f"{self.name} returned no parseable items")
        return items

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

        publisher_title = heading.select_one(".publisher-text .title .primary")
        publisher_link = heading.select_one("a.icon-container[href]")
        if publisher_title is None:
            return None, None
        name = publisher_title.get_text(" ", strip=True) or None
        href = publisher_link.get("href") if publisher_link is not None else None
        url = (
            self._normalize_url(urljoin(self.base_url + "/", href.strip()))
            if isinstance(href, str) and href.strip()
            else None
        )
        return name, url

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
