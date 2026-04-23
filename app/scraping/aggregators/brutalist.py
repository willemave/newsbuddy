"""Brutalist Report aggregator scraper.

Brutalist Report (https://brutalist.report) is a flat HTML aggregator with
per-topic feeds. Each topic page (``/topic/<topic>?limit=N&hours=H``) renders
sections like::

    <h3>Source Name <a href="...">[rss]</a></h3>
    <ul>
        <li><a href="https://example.com/article">Article title</a> [3h]</li>
        ...
    </ul>

We iterate over a configured list of topics and tag each item with
``metadata.aggregator.topic`` so the visibility filter can match the user's
selected topics.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.aggregators._html_grouped import USER_AGENT
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.config import HtmlTopicAggregator

logger = get_logger(__name__)


class BrutalistReportAggregatorScraper(AggregatorScraper):
    """Scrape configured Brutalist Report topic pages."""

    KEY = "brutalist"
    DISPLAY_NAME = "Brutalist Report"

    def __init__(self, settings: HtmlTopicAggregator) -> None:
        super().__init__(name=settings.name)
        self.settings = settings

    def scrape(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for topic in self.settings.topics:
            try:
                topic_url = self._build_topic_url(topic)
                response = httpx.get(
                    topic_url,
                    timeout=20.0,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
            except Exception as exc:  # pragma: no cover - network guard
                logger.exception("Failed to fetch Brutalist Report topic %s: %s", topic, exc)
                continue
            items.extend(self.parse(response.text, topic=topic))
        return items

    def _build_topic_url(self, topic: str) -> str:
        return self.settings.base_url.format(
            topic=topic,
            limit=self.settings.limit,
            hours=self.settings.hours,
        )

    def parse(self, html: str, *, topic: str) -> list[dict[str, Any]]:
        """Parse a single topic page into normalized aggregator items."""
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        topic_url = self._build_topic_url(topic)
        host_domain = self.extract_domain(topic_url)

        # Brutalist groups items by source: each source heading is followed by
        # a <ul> of <li><a>title</a> [Nh]</li> items. Walk the structure by
        # finding each heading and pairing it with the next <ul>. Source
        # headings are <h3>/<h4>; <h1>/<h2> are page chrome ("Brutalist Report",
        # "Topic: Science") and would otherwise steal the first source's <ul>.
        for heading in soup.find_all(["h3", "h4"]):
            source_name = self._extract_source_name(heading)
            ul = heading.find_next("ul")
            if ul is None:
                continue
            for anchor in ul.select("li a"):
                if not isinstance(anchor, Tag):
                    continue
                href = anchor.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                article_url = self._normalize_url(urljoin(topic_url, href.strip()))
                if not article_url or article_url in seen:
                    continue
                if self.extract_domain(article_url) == host_domain:
                    continue
                title = anchor.get_text(" ", strip=True)
                if not title:
                    continue
                seen.add(article_url)
                items.append(
                    self._build_item(
                        article_url=article_url,
                        title=title,
                        source_name=source_name,
                        topic=topic,
                        topic_url=topic_url,
                    )
                )
                if len(items) >= self.settings.limit:
                    return items
        return items

    @staticmethod
    def _extract_source_name(heading: Tag) -> str | None:
        # Strip auxiliary anchors like "[rss]" / "[favicon]" from the heading
        # text by removing all anchor children before extracting text.
        text_parts: list[str] = []
        for child in heading.children:
            if isinstance(child, Tag):
                if child.name == "a":
                    continue
                text_parts.append(child.get_text(" ", strip=True))
            else:
                text_parts.append(str(child))
        cleaned = " ".join(part for part in text_parts if part).strip()
        return cleaned or None

    def _build_item(
        self,
        *,
        article_url: str,
        title: str,
        source_name: str | None,
        topic: str,
        topic_url: str,
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
                    "topic": topic,
                    "metadata": {
                        "source_name": source_name,
                        "topic_url": topic_url,
                    },
                },
                "discussion_url": None,
                "excerpt": None,
                "discovery_time": self.now_iso(),
            },
        }

    @staticmethod
    def _normalize_topic(value: str) -> str:
        # Tolerate spaces / capitalization in user-provided topic values.
        return value.strip().lower().replace(" ", "-")

    @staticmethod
    def is_topic_url(url: str) -> bool:  # pragma: no cover - unused helper
        return "/topic/" in urlparse(url).path
