"""Shared RSS-cluster parsing used by Techmeme/Mediagazer/Memeorandum.

These three aggregators follow the same pattern: each RSS entry's description is
HTML containing the primary article anchor first, followed by related-link
anchors and a permalink back to the cluster page. The subclasses below specialize
this base by overriding ``KEY``, ``DISPLAY_NAME``, and the cluster domain so
permalink/related-link detection works.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any, ClassVar

import feedparser
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.config import RssClusterAggregator

logger = get_logger(__name__)

ENCODING_OVERRIDE_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(feedparser, "CharacterEncodingOverride", None),
        getattr(getattr(feedparser, "exceptions", None), "CharacterEncodingOverride", None),
    )
    if isinstance(exc, type)
)


class RssClusterAggregatorScraper(AggregatorScraper):
    """Base scraper for Techmeme-style RSS clusters."""

    #: Domain treated as the cluster permalink (anchors pointing here are skipped
    #: when picking the primary article and the related links).
    CLUSTER_DOMAIN: ClassVar[str] = ""

    def __init__(self, settings: RssClusterAggregator) -> None:
        super().__init__(name=settings.name)
        self.settings = settings

    def scrape(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        feed_url = str(self.settings.url)

        try:
            parsed_feed = feedparser.parse(feed_url)
        except Exception as exc:  # pragma: no cover - network guard
            logger.exception("Failed to fetch %s feed: %s", self.DISPLAY_NAME, exc)
            return items

        if getattr(parsed_feed, "bozo", 0):
            bozo_exc = getattr(parsed_feed, "bozo_exception", None)
            if bozo_exc and not isinstance(bozo_exc, ENCODING_OVERRIDE_EXCEPTIONS):
                logger.warning(
                    "%s feed reported parsing issues: %s",
                    self.DISPLAY_NAME,
                    bozo_exc,
                )

        feed_title = getattr(parsed_feed.feed, "title", self.DISPLAY_NAME)
        feed_entries = getattr(parsed_feed, "entries", [])[: self.settings.limit]

        for entry in feed_entries:
            try:
                item = self._process_entry(entry, feed_title)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "Error processing %s entry %s: %s",
                    self.DISPLAY_NAME,
                    entry.get("id"),
                    exc,
                )
                continue
            if item:
                items.append(item)

        logger.info("%s scraping completed. Processed %s clusters", self.DISPLAY_NAME, len(items))
        return items

    # ------------------------------------------------------------------
    # Entry processing
    # ------------------------------------------------------------------

    def _process_entry(self, entry: Any, feed_title: str) -> dict[str, Any] | None:
        permalink = entry.get("link") or entry.get("id")
        description_html = entry.get("description", "")
        anchors = self._extract_anchors(description_html)

        primary_anchor = self._select_primary_anchor(anchors)
        if not primary_anchor:
            return None

        primary_url = self._normalize_url(primary_anchor["href"])
        primary_domain = self.extract_domain(primary_url)
        source_name, source_href = self._resolve_source_info(
            anchors, primary_domain, primary_anchor
        )
        if not source_name:
            source_name = primary_domain

        normalized_source_href = self._normalize_url(source_href) if source_href else None

        related_links = (
            self._extract_related_links(
                anchors=anchors,
                primary_url=primary_url,
                limit=self.settings.max_related,
                source_href=normalized_source_href,
            )
            if self.settings.include_related
            else []
        )

        summary_text = self._sanitize_summary(description_html)
        publication_date = self._parse_publication_date(entry)
        normalized_permalink = self._normalize_url(permalink) if permalink else primary_url
        cluster_token = self._derive_cluster_token(normalized_permalink)

        headline_title = entry.get("title") or primary_anchor.get("text") or primary_url
        related_items = [
            {
                "title": related.get("text") or related.get("url"),
                "url": related.get("url"),
                "source": related.get("source"),
            }
            for related in related_links
        ]

        metadata: dict[str, Any] = {
            "platform": self.KEY,
            "source": primary_domain,
            "article": {
                "url": primary_url,
                "title": headline_title,
                "source_domain": primary_domain,
            },
            "aggregator": {
                "key": self.KEY,
                "name": self.settings.name,
                "title": entry.get("title"),
                "external_id": cluster_token,
                "metadata": {
                    "summary_text": summary_text,
                    "related_links": related_items,
                    "comments_count": len(related_items),
                    "feed_name": feed_title,
                    "source_name": source_name,
                },
            },
            "discussion_url": normalized_permalink,
            "excerpt": summary_text,
            "discovery_time": (
                publication_date.isoformat() if publication_date else self.now_iso()
            ),
        }

        return {
            "url": primary_url,
            "title": headline_title,
            "content_type": ContentType.NEWS,
            "is_aggregate": False,
            "metadata": metadata,
        }

    # ------------------------------------------------------------------
    # HTML helpers
    # ------------------------------------------------------------------

    def _extract_anchors(self, html: str) -> list[dict[str, str]]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        anchors: list[dict[str, str]] = []
        for tag in soup.find_all("a"):
            raw_href = tag.get("href")
            href = raw_href.strip() if isinstance(raw_href, str) else ""
            if not href:
                continue
            anchors.append({"href": href, "text": tag.get_text(" ", strip=True)})
        return anchors

    def _select_primary_anchor(self, anchors: list[dict[str, str]]) -> dict[str, str] | None:
        for anchor in anchors:
            if not self._is_cluster_link(anchor["href"]):
                return anchor
        return None

    def _extract_related_links(
        self,
        anchors: list[dict[str, str]],
        primary_url: str,
        limit: int,
        source_href: str | None,
    ) -> list[dict[str, Any]]:
        related: list[dict[str, Any]] = []
        seen_urls = {primary_url}
        if source_href:
            seen_urls.add(source_href)

        for anchor in anchors:
            href = self._normalize_url(anchor["href"])
            if href in seen_urls or self._is_cluster_link(href):
                continue
            seen_urls.add(href)
            related.append(
                {
                    "url": href,
                    "text": anchor.get("text") or href,
                    "source": self.extract_domain(href),
                }
            )
            if len(related) >= limit:
                break
        return related

    def _resolve_source_info(
        self,
        anchors: list[dict[str, str]],
        primary_domain: str,
        primary_anchor: dict[str, str],
    ) -> tuple[str | None, str | None]:
        # The source label is a separate same-domain anchor whose text labels
        # the publication (e.g. ``<a href="https://example.com/">Example</a>``).
        # Skip the primary anchor itself so we don't return it as the source —
        # otherwise it would never be added to ``seen_urls`` for related-link
        # filtering and the label anchor would leak into related links.
        for anchor in anchors:
            if anchor is primary_anchor:
                continue
            href = anchor["href"]
            if self._is_cluster_link(href):
                continue
            if self.extract_domain(href) == primary_domain and anchor.get("text"):
                return anchor["text"], href
        return None, None

    def _sanitize_summary(self, html: str) -> str | None:
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        return text or None

    def _parse_publication_date(self, entry: Any) -> datetime | None:
        timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not timestamp:
            return None
        with contextlib.suppress(TypeError, ValueError):
            year, month, day, hour, minute, second = timestamp[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        return None

    def _derive_cluster_token(self, permalink: str) -> str | None:
        if not permalink:
            return None
        token = permalink.rstrip("/").split("/")[-1]
        return token or None

    def _is_cluster_link(self, url: str) -> bool:
        if not self.CLUSTER_DOMAIN:
            return False
        try:
            domain = self.extract_domain(url)
        except Exception:  # pragma: no cover
            return False
        return domain.endswith(self.CLUSTER_DOMAIN)
