import contextlib
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from xml.sax import SAXParseException

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.contracts import ContentType
from app.scraping.base import BaseScraper
from app.scraping.feed_concurrency import run_feed_jobs
from app.scraping.feed_fetch import fetch_and_parse_feed
from app.services.scraper_configs import build_feed_payloads, list_active_configs_by_type

logger = get_logger(__name__)


class PodcastUnifiedScraper(BaseScraper):
    """Unified podcast RSS scraper following new architecture."""

    def __init__(self) -> None:
        super().__init__("Podcast")

    def _load_podcast_feeds(self) -> list[dict[str, Any]]:
        """Load podcast feed URLs from user configs."""
        with get_db() as db:
            configs = list_active_configs_by_type(db, "podcast_rss")
            return build_feed_payloads(configs)

    def scrape(self) -> list[dict[str, Any]]:
        """Scrape all configured Podcast feeds."""
        feeds = self._load_podcast_feeds()
        if not feeds:
            logger.warning("No podcast feeds configured")
            return []

        items = run_feed_jobs(
            feeds,
            self._scrape_feed,
            max_workers=4,
            on_error=self._record_feed_job_error,
        )
        logger.info(f"Podcast scraping completed. Processed {len(items)} total items")
        return items

    def _scrape_feed(self, feed_config: dict[str, Any]) -> list[dict[str, Any]]:
        """Scrape one feed for the bounded feed executor."""
        items: list[dict[str, Any]] = []
        if not isinstance(feed_config, dict):
            logger.warning("Invalid feed configuration, skipping")
            return items

        feed_name = feed_config.get("name", "Unknown Feed")
        feed_url = feed_config.get("url")
        limit = feed_config.get("limit", 10)
        user_id = feed_config.get("user_id")
        config_id = feed_config.get("config_id")
        if not feed_url:
            logger.warning(f"No URL found for feed: {feed_name}")
            return items

        logger.info(f"Scraping podcast feed: {feed_name} (limit: {limit})")

        try:
            # Parse RSS feed with better encoding handling
            parsed_feed = fetch_and_parse_feed(feed_url)

            # Check for parsing issues
            if parsed_feed.bozo:
                bozo_exception = parsed_feed.bozo_exception
                exception_str = str(bozo_exception).lower()

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
                    raise ValueError("Podcast feed returned HTML instead of XML")

                # Encoding declaration mismatches are common in otherwise usable feeds.
                is_encoding_issue = "encoding" in exception_str or "declared as" in exception_str

                # Check for malformed XML. SAXParseException's message does not include
                # its class name, so classify by type as well as the legacy message.
                if (
                    isinstance(bozo_exception, SAXParseException)
                    or "not well-formed" in exception_str
                ) and not is_encoding_issue:
                    logger.error(
                        "Feed %s contains malformed XML. Skipping.",
                        feed_url,
                        extra={
                            "component": "podcast_scraper",
                            "operation": "feed_parsing",
                            "context_data": {"feed_url": feed_url, "feed_name": feed_name},
                        },
                    )
                    raise ValueError("Podcast feed contains malformed XML")

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
                return items

            # Process entries (limited)
            entries_to_process = parsed_feed.entries[:limit]
            logger.info(f"Processing {len(entries_to_process)} episodes from {feed_name}")

            processed_entries = 0
            missing_audio_titles: list[str] = []
            for entry in entries_to_process:
                try:
                    item = self._process_entry(
                        entry,
                        feed_name,
                        feed_info,
                        feed_url,
                        user_id,
                        config_id,
                        missing_audio_titles=missing_audio_titles,
                    )
                except Exception as entry_exc:
                    entry_source = str(entry.get("link") or entry.get("id") or feed_url)
                    logger.exception(
                        "Error processing entry %s from feed %s",
                        entry.get("id"),
                        feed_url,
                    )
                    self._record_scrape_error(entry_source, entry_exc)
                    continue
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
            raise
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
    ) -> dict[str, Any] | None:
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

        try:
            host = urlparse(link).netloc or ""
        except Exception:
            host = ""
        metadata = {
            "platform": "podcast",
            "source": feed_name,
            "source_domain": host,
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

    def _find_audio_enclosure(self, entry, title: str) -> str | None:
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
    ) -> tuple[str, bool, str]:
        """Select the best link for a podcast entry with robust fallbacks."""
        link = entry.get("link")
        if isinstance(link, str) and self._is_valid_entry_link(link):
            return link, False, "link"

        if link:
            logger.debug("Entry link unusable for '%s': %s", title, link)

        alternate_link = self._find_alternate_link(entry)
        if isinstance(alternate_link, str) and self._is_valid_entry_link(alternate_link):
            return alternate_link, True, "alternate_link"

        entry_id = entry.get("id")
        if isinstance(entry_id, str) and self._is_url(entry_id):
            return entry_id, True, "entry_id"

        entry_guid = entry.get("guid")
        if isinstance(entry_guid, str) and self._is_url(entry_guid):
            return entry_guid, True, "entry_guid"

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

    def _parse_duration(self, duration_str: str) -> int | None:
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
