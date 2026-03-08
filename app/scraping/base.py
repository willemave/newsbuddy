from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.models.scraper_runs import ScraperStats
from app.services.long_form_images import enqueue_visible_long_form_image_if_needed
from app.services.queue import TaskType, get_queue_service
from app.services.scraper_configs import (
    ensure_inbox_status,
    list_active_user_ids,
    should_add_to_inbox,
)
from app.utils.url_utils import is_http_url, normalize_http_url

logger = get_logger(__name__)

"""
Source and Platform Conventions (updated):
-----------------------------------------
All scrapers must set both 'platform' and 'source' fields in metadata:

1) platform: the scraper identifier (lowercase), e.g.
   - hackernews, reddit, substack, podcast, twitter, youtube

2) source: the configured NAME from YAML for articles/podcasts, NEVER overwritten by processors
   - For Substack/Podcasts: Use the "name" field from config YAML (e.g., "Import AI", "Stratechery")
   - For Reddit: Use the subreddit name (e.g., "MachineLearning")
   - For HackerNews/other aggregators: Use the source domain of the linked article
   - The domain is preserved separately in 'source_domain' field for reference
   - Examples:
     - Substack configured as name="Import AI" → platform=substack, source=Import AI
     - Podcast configured as name="Stratechery" → platform=podcast, source=Stratechery
     - Reddit post in r/MachineLearning → platform=reddit, source=MachineLearning
     - Hacker News link to github.com → platform=hackernews, source=github.com

The source field is IMMUTABLE after scraping - processors must preserve it.
"""


class BaseScraper(ABC):
    """Base class for all scrapers."""

    def __init__(self, name: str):
        self.name = name
        self.queue_service = get_queue_service()

    @abstractmethod
    def scrape(self) -> list[dict[str, Any]]:
        """
        Scrape content and return list of items.

        Each item should have:
        - url: str
        - title: Optional[str]
        - content_type: ContentType
        - metadata: Dict[str, Any]
        """
        pass

    def run(self) -> int:
        """Run scraper and save results. Returns saved count for backward compatibility."""
        stats = self.run_with_stats()
        return stats.saved

    def run_with_stats(self) -> ScraperStats:
        """Run scraper and return detailed statistics."""
        logger.info(f"Running {self.name} scraper")

        stats = ScraperStats()

        try:
            # Scrape items
            items = self.scrape()
            stats.scraped = len(items)
            logger.info(f"Scraped {stats.scraped} items from {self.name}")

            # Save to database
            save_stats = self._save_items_with_stats(items)
            stats.saved = save_stats["saved"]
            stats.duplicates = save_stats["duplicates"]
            stats.errors = save_stats["errors"]
            stats.error_details = save_stats["error_details"]

            logger.info(
                f"Saved {stats.saved} new items from {self.name} "
                f"(duplicates: {stats.duplicates}, errors: {stats.errors})"
            )

        except Exception as e:
            logger.error(f"Error in {self.name} scraper: {e}")
            stats.errors = 1
            stats.error_details = [str(e)]

        return stats

    def _save_items(self, items: list[dict[str, Any]]) -> int:
        """Save scraped items to database. Returns saved count for backward compatibility."""
        stats = self._save_items_with_stats(items)
        return stats["saved"]

    def _save_items_with_stats(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Save scraped items to database and return detailed statistics."""
        saved_count = 0
        duplicate_count = 0
        error_count = 0
        error_details = []

        with get_db() as db:
            active_user_ids: list[int] | None = None
            for item in items:
                try:
                    user_id = item.get("user_id")
                    content_type_value = item["content_type"].value
                    metadata = item.get("metadata", {})
                    raw_url = item.get("source_url") or item["url"]
                    canonical_url = normalize_http_url(item["url"]) or normalize_http_url(raw_url)

                    if not canonical_url and content_type_value == ContentType.NEWS.value:
                        article = metadata.get("article")
                        if isinstance(article, dict):
                            canonical_url = normalize_http_url(article.get("url"))

                    if not is_http_url(canonical_url):
                        logger.warning(
                            "Skipping scraped item with invalid URL: %s",
                            raw_url,
                            extra={
                                "component": "scraper_base",
                                "operation": "save_item",
                                "context_data": {
                                    "raw_url": raw_url,
                                    "content_type": content_type_value,
                                },
                            },
                        )
                        error_count += 1
                        error_details.append(f"Invalid URL: {raw_url}")
                        continue
                    # Check if already exists
                    existing = (
                        db.query(Content)
                        .filter(
                            Content.url == canonical_url,
                            Content.content_type == content_type_value,
                        )
                        .first()
                    )

                    if existing:
                        inbox_created = False
                        if should_add_to_inbox(content_type_value):
                            if user_id is not None:
                                inbox_created = ensure_inbox_status(
                                    db,
                                    user_id=user_id,
                                    content_id=existing.id,
                                    content_type=content_type_value,
                                )
                            elif content_type_value == ContentType.NEWS.value:
                                if active_user_ids is None:
                                    active_user_ids = list_active_user_ids(db)
                                for active_user_id in active_user_ids:
                                    if ensure_inbox_status(
                                        db,
                                        user_id=active_user_id,
                                        content_id=existing.id,
                                        content_type=content_type_value,
                                    ):
                                        inbox_created = True
                        if inbox_created:
                            db.commit()
                            enqueue_visible_long_form_image_if_needed(db, existing)
                        logger.debug(f"URL already exists: {item['url']}")
                        duplicate_count += 1
                        continue

                    # Create new content
                    content = Content(
                        content_type=content_type_value,
                        url=canonical_url,
                        source_url=raw_url,
                        title=item.get("title"),
                        source=metadata.get("source"),  # Extract source from metadata
                        platform=metadata.get("platform"),  # Extract platform from metadata
                        is_aggregate=bool(item.get("is_aggregate", False)),
                        status=ContentStatus.NEW.value,
                        content_metadata=metadata,
                        created_at=datetime.now(UTC),
                    )

                    db.add(content)
                    db.flush()

                    if should_add_to_inbox(content_type_value):
                        if user_id is not None:
                            ensure_inbox_status(
                                db,
                                user_id=user_id,
                                content_id=content.id,
                                content_type=content_type_value,
                            )
                        elif content_type_value == ContentType.NEWS.value:
                            if active_user_ids is None:
                                active_user_ids = list_active_user_ids(db)
                            for active_user_id in active_user_ids:
                                ensure_inbox_status(
                                    db,
                                    user_id=active_user_id,
                                    content_id=content.id,
                                    content_type=content_type_value,
                                )

                    db.commit()
                    db.refresh(content)

                    # Queue for processing
                    self.queue_service.enqueue(TaskType.PROCESS_CONTENT, content_id=content.id)
                    if content_type_value == ContentType.NEWS.value:
                        self.queue_service.enqueue(TaskType.FETCH_DISCUSSION, content_id=content.id)

                    saved_count += 1

                except Exception as e:
                    db.rollback()
                    if "UNIQUE constraint failed" in str(e) or "duplicate key value" in str(e):
                        logger.debug(f"URL already exists (race condition): {item['url']}")
                        duplicate_count += 1
                    else:
                        logger.error(f"Error saving item {item['url']}: {e}")
                        error_count += 1
                        error_details.append(f"Error saving {item.get('url', 'unknown')}: {str(e)}")
                    continue

        return {
            "saved": saved_count,
            "duplicates": duplicate_count,
            "errors": error_count,
            "error_details": error_details,
        }

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for consistency."""
        # Remove trailing slashes
        url = url.rstrip("/")

        # Ensure https
        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)

        return url
