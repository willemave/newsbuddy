"""Dedicated scraper for Techmeme clusters."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.base import BaseScraper

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "techmeme.yml"
TECHMEME_DOMAIN = "techmeme.com"

ENCODING_OVERRIDE_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(feedparser, "CharacterEncodingOverride", None),
        getattr(getattr(feedparser, "exceptions", None), "CharacterEncodingOverride", None),
    )
    if isinstance(exc, type)
)


class TechmemeFeedSettings(BaseModel):
    """Configuration for fetching Techmeme clusters."""

    url: HttpUrl = Field(default="https://www.techmeme.com/feed.xml")
    limit: int = Field(default=20, ge=1, le=50)
    include_related: bool = Field(default=True)
    max_related: int = Field(default=6, ge=0, le=20)


class TechmemeSettings(BaseModel):
    """Top-level Techmeme scraper configuration."""

    feed: TechmemeFeedSettings = Field(default_factory=TechmemeFeedSettings)


def load_techmeme_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> TechmemeSettings:
    """Load Techmeme scraper configuration from YAML."""

    resolved_path = Path(config_path)
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path

    if not resolved_path.exists():
        logger.warning("Techmeme config file not found at %s; using defaults", resolved_path)
        return TechmemeSettings()

    try:
        with open(resolved_path, encoding="utf-8") as fh:
            raw_config = yaml.safe_load(fh) or {}
        return TechmemeSettings.model_validate(raw_config)
    except ValidationError as exc:
        logger.error("Invalid Techmeme config data at %s: %s", resolved_path, exc)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("Error loading Techmeme config at %s: %s", resolved_path, exc, exc_info=True)

    return TechmemeSettings()


class TechmemeScraper(BaseScraper):
    """Scraper for Techmeme RSS feed clusters."""

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH):
        super().__init__("Techmeme")
        resolved_path = Path(config_path)
        if not resolved_path.is_absolute():
            resolved_path = PROJECT_ROOT / resolved_path
        self.config_path = resolved_path
        self.settings = load_techmeme_config(resolved_path)

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape Techmeme feed entries and normalize into aggregator items."""

        feed_settings = self.settings.feed
        items: list[dict[str, Any]] = []

        try:
            parsed_feed = feedparser.parse(str(feed_settings.url))
        except Exception as exc:  # pragma: no cover - network failure guard
            logger.exception(
                "Failed to fetch Techmeme feed: %s",
                exc,
                extra={
                    "component": "techmeme_scraper",
                    "operation": "feed_fetch",
                    "context_data": {"feed_url": str(feed_settings.url), "feed_name": "Techmeme"},
                },
            )
            return items

        if getattr(parsed_feed, "bozo", 0):
            bozo_exc = getattr(parsed_feed, "bozo_exception", None)
            if bozo_exc and not isinstance(bozo_exc, ENCODING_OVERRIDE_EXCEPTIONS):
                logger.warning(
                    "Techmeme feed reported parsing issues: %s",
                    bozo_exc,
                    extra={
                        "component": "techmeme_scraper",
                        "operation": "feed_parsing",
                        "context_data": {
                            "feed_url": str(feed_settings.url),
                            "feed_name": getattr(parsed_feed.feed, "title", "Techmeme"),
                        },
                    },
                )
            else:
                logger.debug("Techmeme feed encoding warning ignored: %s", bozo_exc)

        feed_entries = getattr(parsed_feed, "entries", [])[: feed_settings.limit]
        feed_title = getattr(parsed_feed.feed, "title", "Techmeme")

        for entry in feed_entries:
            try:
                item = self._process_entry(entry, feed_settings, feed_title)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "Error processing Techmeme entry %s: %s",
                    entry.get("id"),
                    exc,
                    extra={
                        "component": "techmeme_scraper",
                        "operation": "entry_processing",
                        "context_data": {
                            "feed_url": str(feed_settings.url),
                            "entry_id": entry.get("id"),
                            "entry_title": entry.get("title"),
                        },
                    },
                )
                continue

            if item:
                items.append(item)

        logger.info("Techmeme scraping completed. Processed %s clusters", len(items))
        return items

    def _process_entry(
        self, entry: Any, feed_settings: TechmemeFeedSettings, feed_name: str
    ) -> dict[str, Any] | None:
        """Normalize a Techmeme RSS entry into the aggregator schema."""

        permalink = entry.get("link") or entry.get("id")
        description_html = entry.get("description", "")
        anchors = self._extract_anchors(description_html)

        primary_anchor = self._select_primary_anchor(anchors)
        if not primary_anchor:
            logger.debug(
                "Skipping Techmeme entry with no primary article link: %s", entry.get("title")
            )
            return None

        primary_url = self._normalize_url(primary_anchor["href"])
        primary_domain = self._extract_domain(primary_url)
        source_name, source_href = self._resolve_source_info(anchors, primary_domain)
        if not source_name:
            source_name = primary_domain

        normalized_source_href = self._normalize_url(source_href) if source_href else None

        related_links: list[dict[str, Any]] = []
        if feed_settings.include_related:
            related_links = self._extract_related_links(
                anchors=anchors,
                primary_url=primary_url,
                limit=feed_settings.max_related,
                source_href=normalized_source_href,
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
            "platform": "techmeme",
            "source": primary_domain,
            "article": {
                "url": primary_url,
                "title": headline_title,
                "source_domain": primary_domain,
            },
            "aggregator": {
                "name": "Techmeme",
                "title": entry.get("title"),
                "external_id": cluster_token,
                "metadata": {
                    "summary_text": summary_text,
                    "related_links": related_items,
                    "comments_count": len(related_items),
                    "feed_name": feed_name,
                    "source_name": source_name,
                },
            },
            "discussion_url": normalized_permalink,
            "excerpt": summary_text,
            "discovery_time": (
                publication_date.isoformat()
                if publication_date
                else datetime.now(UTC).isoformat()
            ),
        }

        return {
            "url": primary_url,  # Use original article URL (consistent with Twitter/HN scrapers)
            "title": headline_title,
            "content_type": ContentType.NEWS,
            "is_aggregate": False,
            "metadata": metadata,
        }

    def _extract_anchors(self, html: str) -> list[dict[str, str]]:
        """Parse anchor tags from HTML description."""

        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        anchors: list[dict[str, str]] = []

        for tag in soup.find_all("a"):
            href = (tag.get("href") or "").strip()
            if not href:
                continue
            anchors.append(
                {
                    "href": href,
                    "text": tag.get_text(" ", strip=True),
                }
            )

        return anchors

    def _select_primary_anchor(self, anchors: list[dict[str, str]]) -> dict[str, str] | None:
        """Return the first non-Techmeme anchor as the primary article."""

        for anchor in anchors:
            if not self._is_techmeme_link(anchor["href"]):
                return anchor
        return None

    def _extract_related_links(
        self,
        anchors: list[dict[str, str]],
        primary_url: str,
        limit: int,
        source_href: str | None,
    ) -> list[dict[str, Any]]:
        """Extract related links from the description, excluding the primary article."""

        related: list[dict[str, Any]] = []
        seen_urls = {primary_url}
        if source_href:
            seen_urls.add(source_href)

        for anchor in anchors:
            href = self._normalize_url(anchor["href"])
            if href in seen_urls or self._is_techmeme_link(href):
                continue

            seen_urls.add(href)
            related.append(
                {
                    "url": href,
                    "text": anchor.get("text") or href,
                    "source": self._extract_domain(href),
                }
            )

            if len(related) >= limit:
                break

        return related

    def _resolve_source_info(
        self, anchors: list[dict[str, str]], primary_domain: str
    ) -> tuple[str | None, str | None]:
        """Attempt to resolve human-friendly source information for the primary article."""

        for anchor in anchors:
            href = anchor["href"]
            if self._is_techmeme_link(href):
                continue
            if self._extract_domain(href) == primary_domain and anchor.get("text"):
                return anchor["text"], href
        return None, None

    def _sanitize_summary(self, html: str) -> str | None:
        """Strip HTML down to a concise text summary."""

        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        return text or None

    def _parse_publication_date(self, entry: Any) -> datetime | None:
        """Parse publication date if present."""

        timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not timestamp:
            return None

        with contextlib.suppress(TypeError, ValueError):
            return datetime(*timestamp[:6])
        return None

    def _derive_cluster_token(self, permalink: str) -> str | None:
        """Derive a stable token for the Techmeme cluster."""

        if not permalink:
            return None

        token = permalink.rstrip("/").split("/")[-1]
        return token or None

    def _extract_domain(self, url: str) -> str:
        """Extract normalized domain from URL."""

        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain

    def _is_techmeme_link(self, url: str) -> bool:
        """Return True if URL belongs to Techmeme."""

        try:
            domain = self._extract_domain(url)
        except Exception:  # pragma: no cover - guard for malformed URLs
            return False
        return domain.endswith(TECHMEME_DOMAIN)
