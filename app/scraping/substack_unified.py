"""Unified Substack scraper following the new architecture."""

import contextlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.contracts import ContentType
from app.scraping.base import BaseScraper
from app.scraping.rss_helpers import resolve_feed_source
from app.services.scraper_configs import build_feed_payloads, list_active_configs_by_type
from app.utils.error_logger import log_scraper_event
from app.utils.paths import resolve_config_directory, resolve_config_path

ENCODING_OVERRIDE_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(feedparser, "CharacterEncodingOverride", None),
        getattr(getattr(feedparser, "exceptions", None), "CharacterEncodingOverride", None),
    )
    if isinstance(exc, type)
)

logger = get_logger(__name__)
_MISSING_CONFIG_WARNINGS: set[str] = set()


def _resolve_substack_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return resolve_config_path("SUBSTACK_CONFIG_PATH", "substack.yml")

    candidate = Path(config_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)

    base_dir = resolve_config_directory()
    return (base_dir / candidate).resolve(strict=False)


def _emit_missing_config_warning(resolved_path: Path) -> None:
    key = str(resolved_path.resolve(strict=False))
    if key in _MISSING_CONFIG_WARNINGS:
        return
    _MISSING_CONFIG_WARNINGS.add(key)
    log_scraper_event(
        service="Substack",
        event="config_missing",
        level=logging.WARNING,
        metric="scrape_config_missing",
        path=str(resolved_path.resolve(strict=False)),
    )


def load_substack_feeds(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Loads Substack feed URLs, names, and limits from a YAML file."""
    resolved_path = _resolve_substack_config_path(config_path)

    if not resolved_path.exists():
        _emit_missing_config_warning(resolved_path)
        return []

    try:
        with open(resolved_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        log_scraper_event(
            service="Substack",
            event="config_load_failed",
            level=logging.ERROR,
            path=str(resolved_path),
            error=str(exc),
        )
        return []

    feeds = config.get("feeds", [])
    result: list[dict[str, Any]] = []
    for feed in feeds:
        if isinstance(feed, dict) and feed.get("url"):
            result.append(
                {
                    "url": feed["url"],
                    "name": feed.get("name", "Unknown Substack"),
                    "limit": feed.get("limit", 10),
                }
            )
        elif isinstance(feed, str):
            result.append(
                {
                    "url": feed,
                    "name": "Unknown Substack",
                    "limit": 10,
                }
            )
    return result


class SubstackScraper(BaseScraper):
    """Scraper for Substack RSS feeds."""

    def __init__(self, config_path: str | Path | None = None):
        super().__init__("Substack")
        self.podcast_filter = re.compile(r"\b(podcast|transcript)\b", re.IGNORECASE)

    def _load_feeds(self) -> list[dict[str, Any]]:
        """Load active feeds for all users."""
        with get_db() as db:
            configs = list_active_configs_by_type(db, "substack")
            return build_feed_payloads(configs)

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape all configured Substack feeds with comprehensive error logging."""
        items: list[dict[str, Any]] = []

        feeds = self._load_feeds()
        if not feeds:
            logger.warning("No Substack feeds configured for users. Skipping scrape.")
            return items

        for feed_info in feeds:
            feed_url = feed_info.get("url")
            source_name = feed_info.get("name", "Unknown Substack")
            display_name = feed_info.get("display_name")
            limit = feed_info.get("limit", 10)
            user_id = feed_info.get("user_id")
            config_id = feed_info.get("config_id")

            if not feed_url:
                logger.warning("Skipping empty feed URL.")
                continue

            logger.info(
                f"Scraping Substack feed: {feed_url} (source: {source_name}, limit: {limit})"
            )
            try:
                parsed_feed = feedparser.parse(feed_url)

                logger.debug(
                    "Parsed feed %s (entries=%s, bozo=%s, feed_title=%s)",
                    feed_url,
                    len(getattr(parsed_feed, "entries", []) or []),
                    getattr(parsed_feed, "bozo", False),
                    parsed_feed.feed.get("title") if parsed_feed.feed else "<no-title>",
                )

                # Check for parsing issues
                if parsed_feed.bozo:
                    bozo_exc = parsed_feed.bozo_exception

                    if ENCODING_OVERRIDE_EXCEPTIONS and isinstance(
                        bozo_exc, ENCODING_OVERRIDE_EXCEPTIONS
                    ):
                        logger.debug(
                            "Feed %s has encoding mismatch: %s",
                            feed_url,
                            bozo_exc,
                        )
                    else:
                        # Log detailed parsing error
                        logger.warning(
                            "Feed %s may be ill-formed: %s",
                            feed_url,
                            bozo_exc,
                            extra={
                                "component": "substack_scraper",
                                "operation": "feed_parsing",
                                "context_data": {
                                    "feed_url": feed_url,
                                    "feed_name": parsed_feed.feed.get("title", "Unknown Feed"),
                                },
                            },
                        )

                # Extract feed name and description from the RSS feed
                feed_name = parsed_feed.feed.get("title", "Unknown Feed")
                feed_description = parsed_feed.feed.get("description", "")

                logger.info(f"Processing feed: {feed_name} - {feed_description}")

                # Apply limit to entries (similar to podcast scraper)
                entries_to_process = parsed_feed.entries[:limit]

                processed_entries = 0
                for entry in entries_to_process:
                    item = self._process_entry(
                        entry,
                        feed_name,
                        feed_description,
                        feed_url,
                        display_name,
                        source_name,
                        user_id,
                        config_id,
                    )
                    if item:
                        items.append(item)
                        processed_entries += 1

                logger.info(
                    f"Successfully processed {processed_entries} entries from {feed_name} "
                    f"(limit: {limit})"
                )

            except Exception as e:
                # Log comprehensive error details
                logger.exception(
                    "Error scraping feed %s: %s",
                    feed_url,
                    e,
                    extra={
                        "component": "substack_scraper",
                        "operation": "feed_scraping",
                        "context_data": {"feed_url": feed_url, "feed_name": "Unknown Feed"},
                    },
                )

        logger.info(f"Substack scraping completed. Processed {len(items)} total items")
        return items

    def _process_entry(
        self,
        entry,
        feed_name: str,
        feed_description: str = "",
        feed_url: str = "",
        display_name: str | None = None,
        source_name: str = "",
        user_id: int | None = None,
        config_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Process a single entry from an RSS feed."""
        title = entry.get("title", "No Title")
        link = entry.get("link")

        if not link:
            # Log detailed entry error
            logger.warning(
                "Skipping entry with no link in feed %s: %s",
                feed_name,
                title,
                extra={
                    "component": "substack_scraper",
                    "operation": "entry_processing",
                    "context_data": {
                        "feed_url": feed_url,
                        "feed_name": feed_name,
                        "entry_title": title,
                        "entry_id": entry.get("id"),
                        "error_type": "missing_link",
                    },
                },
            )
            return None

        # Filter out podcasts
        if self.podcast_filter.search(title):
            logger.info(f"Skipping podcast entry: {title}")
            return None

        # Extract content from RSS entry
        content = ""
        if "content" in entry and entry["content"]:
            for c in entry["content"]:
                if c.get("type") == "text/html":
                    content = c.get("value", "")
                    break
        if not content:
            content = entry.get("summary", "")

        logger.debug(
            "Entry debug: feed=%s title=%s content_chars=%s summary_chars=%s link=%s",
            feed_name,
            title,
            len(content or ""),
            len(entry.get("summary", "") or ""),
            link,
        )

        # Parse publication date
        publication_date = None
        if entry.get("published_parsed"):
            with contextlib.suppress(TypeError, ValueError):
                publication_date = datetime(*entry["published_parsed"][:6])

        # Create item for unified system
        # Determine domain for metadata
        try:
            from urllib.parse import urlparse

            host = urlparse(link).netloc or ""
        except Exception:
            host = ""
        resolved_source = resolve_feed_source(display_name, feed_name, feed_url) or source_name
        item = {
            "url": self._normalize_url(link),
            "title": title,
            "content_type": ContentType.ARTICLE,
            "user_id": user_id,
            "metadata": {
                "platform": "substack",  # Scraper identifier
                "source": resolved_source,
                "source_domain": host,  # Store domain separately for reference
                "feed_url": feed_url,
                "feed_config_id": config_id,
                "feed_name": feed_name,
                "feed_description": feed_description,
                "author": entry.get("author"),
                "publication_date": publication_date.isoformat() if publication_date else None,
                "rss_content": content,  # Store RSS content for processing
                "word_count": len(content.split()) if content else 0,
                "entry_id": entry.get("id"),
                "tags": [tag.get("term") for tag in entry.get("tags", []) if tag.get("term")],
            },
        }

        logger.debug(
            "Emitted Substack item: url=%s word_count=%s publication_date=%s tags=%s",
            item["url"],
            item["metadata"].get("word_count"),
            item["metadata"].get("publication_date"),
            item["metadata"].get("tags"),
        )

        return item


def run_substack_scraper():
    """Initialize and run the Substack scraper."""
    scraper = SubstackScraper()
    return scraper.run()


if __name__ == "__main__":
    count = run_substack_scraper()
    logger.info(
        "Substack scraper processed %s items",
        count,
        extra={
            "component": "substack_scraper",
            "operation": "run",
            "context_data": {"processed_count": count},
        },
    )
