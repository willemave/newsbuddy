import contextlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.base import BaseScraper
from app.services.scraper_configs import build_feed_payloads, list_active_configs_by_type
from app.utils.error_logger import log_scraper_event
from app.utils.paths import resolve_config_directory, resolve_config_path

logger = get_logger(__name__)
_MISSING_CONFIG_WARNINGS: set[str] = set()


def _resolve_podcast_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return resolve_config_path("PODCAST_CONFIG_PATH", "podcasts.yml")

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
        service="Podcast",
        event="config_missing",
        level=logging.WARNING,
        metric="scrape_config_missing",
        path=str(resolved_path.resolve(strict=False)),
    )


class PodcastUnifiedScraper(BaseScraper):
    """Unified podcast RSS scraper following new architecture."""

    def __init__(self, config_path: str | Path | None = None):
        super().__init__("Podcast")

    def _load_podcast_feeds(self) -> list[dict[str, Any]]:
        """Load podcast feed URLs from user configs."""
        with get_db() as db:
            configs = list_active_configs_by_type(db, "podcast_rss")
            return build_feed_payloads(configs)

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape all configured podcast feeds with comprehensive error logging."""
        feeds = self._load_podcast_feeds()
        if not feeds:
            logger.warning("No podcast feeds configured")
            return []

        items = []

        for feed_config in feeds:
            if not isinstance(feed_config, dict):
                logger.warning("Invalid feed configuration, skipping")
                continue

            feed_name = feed_config.get("name", "Unknown Feed")
            feed_url = feed_config.get("url")
            limit = feed_config.get("limit", 10)
            user_id = feed_config.get("user_id")
            config_id = feed_config.get("config_id")

            if not feed_url:
                logger.warning(f"No URL found for feed: {feed_name}")
                continue

            logger.info(f"Scraping podcast feed: {feed_name} (limit: {limit})")

            try:
                # Parse RSS feed with better encoding handling
                parsed_feed = feedparser.parse(feed_url)

                # Check for parsing issues
                if parsed_feed.bozo:
                    exception_str = str(parsed_feed.bozo_exception).lower()

                    # Check for critical errors that should skip processing

                    # Check if it's HTML instead of XML
                    if "is not an xml media type" in exception_str:
                        logger.error(
                            "Feed %s returned HTML instead of XML. Skipping.",
                            feed_url,
                            extra={
                                "component": "podcast_scraper",
                                "operation": "feed_parsing",
                                "context_data": {"feed_url": feed_url, "feed_name": feed_name},
                            },
                        )
                        continue

                    # Check for malformed XML
                    if "not well-formed" in exception_str or "saxparseexception" in exception_str:
                        logger.error(
                            "Feed %s contains malformed XML. Skipping.",
                            feed_url,
                            extra={
                                "component": "podcast_scraper",
                                "operation": "feed_parsing",
                                "context_data": {"feed_url": feed_url, "feed_name": feed_name},
                            },
                        )
                        continue

                    # Check if it's just an encoding mismatch (not critical)
                    is_encoding_issue = False
                    if "encoding" in exception_str or "declared as" in exception_str:
                        is_encoding_issue = True

                    # Only log other errors
                    if not is_encoding_issue:
                        logger.warning(
                            "Feed %s may be ill-formed: %s",
                            feed_url,
                            parsed_feed.bozo_exception,
                            extra={
                                "component": "podcast_scraper",
                                "operation": "feed_parsing",
                                "context_data": {"feed_url": feed_url, "feed_name": feed_name},
                            },
                        )
                    else:
                        logger.debug(
                            f"Feed {feed_url} has encoding declaration mismatch "
                            f"(not critical): {parsed_feed.bozo_exception}"
                        )

                feed_info = getattr(parsed_feed, "feed", {})
                logger.debug(f"Feed title: {feed_info.get('title', 'N/A')}")
                logger.debug(f"Total entries: {len(parsed_feed.entries)}")

                # Check if feed has entries
                if not parsed_feed.entries:
                    logger.warning(f"Feed {feed_url} has no entries. Skipping.")
                    continue

                # Process entries (limited)
                entries_to_process = parsed_feed.entries[:limit]
                logger.info(f"Processing {len(entries_to_process)} episodes from {feed_name}")

                processed_entries = 0
                missing_audio_titles: list[str] = []
                for entry in entries_to_process:
                    item = self._process_entry(
                        entry,
                        feed_name,
                        feed_info,
                        feed_url,
                        user_id,
                        config_id,
                        missing_audio_titles=missing_audio_titles,
                    )
                    if item:
                        items.append(item)
                        processed_entries += 1

                if missing_audio_titles:
                    logger.info(
                        "Skipped %s podcast entries without audio enclosures from %s",
                        len(missing_audio_titles),
                        feed_name,
                    )
                logger.info(f"Successfully processed {processed_entries} episodes from {feed_name}")

            except Exception as e:
                # Log comprehensive error details
                logger.exception(
                    "Error scraping feed %s: %s",
                    feed_url,
                    e,
                    extra={
                        "component": "podcast_scraper",
                        "operation": "feed_scraping",
                        "context_data": {"feed_url": feed_url, "feed_name": feed_name},
                    },
                )

        logger.info(f"Podcast scraping completed. Processed {len(items)} total items")
        return items

    def _process_entry(
        self,
        entry,
        feed_name: str,
        feed_info: dict,
        feed_url: str,
        user_id: int | None,
        config_id: int | None = None,
        missing_audio_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Process a single podcast entry."""
        title = entry.get("title", "No Title")

        # Find audio enclosure URL first (this is the most important for podcasts)
        enclosure_url = self._find_audio_enclosure(entry, title)
        if not enclosure_url:
            if missing_audio_titles is not None:
                missing_audio_titles.append(title)
            return None

        link, used_fallback, fallback_reason = self._select_entry_link(
            entry,
            title=title,
            enclosure_url=enclosure_url,
            feed_url=feed_url,
        )
        if used_fallback:
            logger.info(
                "Using fallback link for '%s' (%s): %s",
                title,
                fallback_reason,
                link,
            )

        # Extract publication date
        publication_date = None
        if entry.get("published_parsed"):
            try:
                publication_date = datetime(*entry.published_parsed[:6])
            except Exception as e:
                logger.debug(f"Error parsing publication date: {e}")

        # Extract episode number if available
        episode_number = None
        episode_str = entry.get("itunes_episode") or entry.get("episode")
        if episode_str:
            with contextlib.suppress(ValueError, TypeError):
                episode_number = int(episode_str)

        # Extract duration if available
        duration = None
        duration_str = entry.get("itunes_duration")
        if duration_str:
            duration = self._parse_duration(duration_str)

        # Build metadata
        # Determine domain for metadata
        try:
            from urllib.parse import urlparse

            host = urlparse(link).netloc or ""
        except Exception:
            host = ""
        metadata = {
            "platform": "podcast",  # Scraper identifier
            "source": feed_name,  # Configured name from YAML (never overwritten)
            "source_domain": host,  # Store domain separately for reference
            "feed_url": feed_url,
            "feed_config_id": config_id,
            "audio_url": enclosure_url,
            "publication_date": publication_date.isoformat() if publication_date else None,
            "episode_number": episode_number,
            "duration_seconds": duration,
            "feed_name": feed_name,
            "feed_title": feed_info.get("title"),
            "feed_description": feed_info.get("description"),
            "author": entry.get("author") or feed_info.get("author"),
            "description": entry.get("description") or entry.get("summary"),
        }

        return {
            "url": self._normalize_url(link),
            "title": title,
            "content_type": ContentType.PODCAST,
            "user_id": user_id,
            "metadata": metadata,
        }

    def _find_audio_enclosure(self, entry, title: str) -> str:
        """Find the audio enclosure URL for a podcast entry."""
        enclosures = entry.get("enclosures")
        if not enclosures:
            enclosures = dict(entry).get("enclosures")

        # Check enclosures first
        if enclosures:
            for enclosure in enclosures:
                enclosure_type = getattr(enclosure, "type", None) or enclosure.get("type", "")
                enclosure_href = getattr(enclosure, "href", None) or enclosure.get("href", "")
                if not enclosure_href:
                    continue
                if enclosure_type and "audio" in enclosure_type:
                    logger.debug("Found audio enclosure for '%s': %s", title, enclosure_href)
                    return enclosure_href
                if any(
                    enclosure_href.lower().endswith(ext) for ext in (".mp3", ".m4a", ".wav", ".ogg")
                ):
                    logger.debug(
                        "Found audio enclosure by extension for '%s': %s",
                        title,
                        enclosure_href,
                    )
                    return enclosure_href

        # Fallback: check links for audio content
        for link_item in getattr(entry, "links", []):
            link_href = link_item.get("href", "")
            link_type = link_item.get("type", "")

            # Check by MIME type
            if link_type and "audio" in link_type:
                logger.debug(f"Found audio link by type for '{title}': {link_href}")
                return link_href

            # Check by file extension
            if link_href and any(
                ext in link_href.lower() for ext in [".mp3", ".m4a", ".wav", ".ogg"]
            ):
                logger.debug(f"Found audio link by extension for '{title}': {link_href}")
                return link_href

        return None

    def _select_entry_link(
        self,
        entry,
        *,
        title: str,
        enclosure_url: str,
        feed_url: str,
    ) -> tuple[str, bool, str]:
        """Select the best link for a podcast entry with robust fallbacks."""
        link = entry.get("link")
        if self._is_valid_entry_link(link):
            return link, False, "link"

        if link:
            logger.debug("Entry link unusable for '%s': %s", title, link)

        alternate_link = self._find_alternate_link(entry)
        if self._is_valid_entry_link(alternate_link):
            return alternate_link, True, "alternate_link"

        entry_id = entry.get("id")
        if self._is_url(entry_id):
            return entry_id, True, "entry_id"

        entry_guid = entry.get("guid")
        if self._is_url(entry_guid):
            return entry_guid, True, "entry_guid"

        if not enclosure_url:
            logger.warning("Missing enclosure URL for '%s' (%s)", title, feed_url)
            return feed_url, True, "feed_url"

        return enclosure_url, True, "enclosure_url"

    def _find_alternate_link(self, entry) -> str | None:
        """Find an alternate HTML link for a podcast entry if available."""
        for link_item in entry.get("links", []):
            href = link_item.get("href")
            if not href:
                continue
            rel = link_item.get("rel")
            link_type = link_item.get("type", "")
            if rel not in (None, "", "alternate"):
                continue
            if link_type and "html" not in link_type:
                continue
            return href
        return None

    def _is_valid_entry_link(self, link: str | None) -> bool:
        """Return True if link is a URL and not just a bare domain."""
        if not self._is_url(link):
            return False
        parsed = urlparse(link)
        if parsed.path not in ("", "/"):
            return True
        return bool(parsed.query or parsed.fragment)

    def _is_url(self, value: str | None) -> bool:
        """Return True if value is an http(s) URL."""
        if not value:
            return False
        try:
            parsed = urlparse(value)
        except Exception:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _parse_duration(self, duration_str: str) -> int:
        """Parse duration string to seconds."""
        try:
            # Handle formats like "1:23:45" or "23:45" or "123"
            parts = duration_str.split(":")
            if len(parts) == 3:  # H:M:S
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:  # M:S
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            else:  # Just seconds
                return int(duration_str)
        except (ValueError, TypeError):
            logger.debug(f"Could not parse duration: {duration_str}")
            return None

    def _sanitize_filename(self, title: str) -> str:
        """Sanitize title for filename use."""
        # Remove invalid characters
        sanitized = re.sub(r"[^\w\s-]", "", title).strip()
        # Replace spaces with hyphens
        sanitized = re.sub(r"[-\s]+", "-", sanitized)
        # Truncate to reasonable length
        return sanitized[:100]
