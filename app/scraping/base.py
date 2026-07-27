from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.models.domain.scraper_runs import ScraperStats
from app.services.long_form_images import (
    has_active_generate_image_task,
    has_generated_long_form_image,
    is_visible_long_form_image_candidate,
)
from app.services.news_ingestion import (
    NewsItemUpsertInput,
    build_news_item_upsert_input_from_scraped_item,
    should_enqueue_news_item_enrichment,
    upsert_news_items,
)
from app.services.news_item_discussions import (
    news_item_discussion_refresh_ids,
    sync_news_item_discussions_from_news_items,
)
from app.services.queue import TaskEnqueueRequest, TaskType, get_queue_service
from app.services.scraper_configs import (
    ensure_inbox_statuses,
)
from app.utils.url_utils import is_http_url, normalize_http_url

logger = get_logger(__name__)

type _NewsEntry = tuple[dict[str, Any], NewsItemUpsertInput]
type _ContentEntry = tuple[dict[str, Any], str, str, str, dict[str, Any]]


@dataclass
class _SaveStats:
    saved: int = 0
    duplicates: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    processed_by_config_id: dict[int, int] = field(default_factory=dict)

    def merge(self, other: _SaveStats) -> None:
        self.saved += other.saved
        self.duplicates += other.duplicates
        self.errors += other.errors
        self.error_details.extend(other.error_details)
        for config_id, count in other.processed_by_config_id.items():
            self.processed_by_config_id[config_id] = (
                self.processed_by_config_id.get(config_id, 0) + count
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "saved": self.saved,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "error_details": self.error_details,
            "processed_by_config_id": self.processed_by_config_id,
        }


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
            stats.processed_by_config_id = save_stats["processed_by_config_id"]

            logger.info(
                f"Saved {stats.saved} new items from {self.name} "
                f"(duplicates: {stats.duplicates}, errors: {stats.errors})"
            )

        except Exception as e:
            logger.error(f"Error in {self.name} scraper: {e}")
            stats.errors = 1
            stats.error_details = [str(e)]

        return stats

    def _save_items_with_stats(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Save scraped items to database and return detailed statistics."""
        stats = _SaveStats()

        news_entries: list[_NewsEntry] = []
        content_entries: list[_ContentEntry] = []
        for item in items:
            try:
                content_type = item["content_type"]
                if not isinstance(content_type, ContentType):
                    raise TypeError("Scraped item content_type must be a ContentType")
                content_type_value = content_type.value
                metadata = item.get("metadata", {})
                if content_type_value == ContentType.NEWS.value:
                    news_entries.append(
                        (item, build_news_item_upsert_input_from_scraped_item(item))
                    )
                    continue
                if not isinstance(metadata, dict):
                    raise TypeError("Scraped item metadata must be an object")

                item_url = item["url"]
                raw_url = item.get("source_url") or item_url
                if not isinstance(item_url, str) or not isinstance(raw_url, str):
                    raise TypeError("Scraped item URLs must be strings")
                canonical_url = normalize_http_url(item_url) or normalize_http_url(raw_url)
                if canonical_url is None or not is_http_url(canonical_url):
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
                    stats.errors += 1
                    stats.error_details.append(f"Invalid URL: {raw_url}")
                    continue
                content_entries.append(
                    (
                        item,
                        content_type_value,
                        raw_url,
                        canonical_url,
                        cast(dict[str, Any], metadata),
                    )
                )
            except Exception as exc:
                logger.error("Error preparing scraped item: %s", exc)
                stats.errors += 1
                stats.error_details.append(
                    f"Error preparing {item.get('url', 'unknown')}: {str(exc)}"
                )

        if news_entries or content_entries:
            stats.merge(self._persist_with_failure_isolation(news_entries, content_entries))
        return stats.as_dict()

    def _persist_with_failure_isolation(
        self,
        news_entries: list[_NewsEntry],
        content_entries: list[_ContentEntry],
    ) -> _SaveStats:
        """Use one fast transaction, falling back to isolated writes only on failure."""
        try:
            return self._persist_prepared_batch(news_entries, content_entries)
        except Exception as batch_error:  # noqa: BLE001 - recovery boundary
            total_items = len(news_entries) + len(content_entries)
            if total_items <= 1:
                item = (news_entries[0] if news_entries else content_entries[0])[0]
                return self._failed_item_stats(item, batch_error)

            logger.warning(
                "Scraper batch persistence failed; retrying %s items independently: %s",
                total_items,
                batch_error,
            )
            stats = _SaveStats()
            for news_entry in news_entries:
                stats.merge(self._persist_one(news_entry=news_entry))
            for content_entry in content_entries:
                stats.merge(self._persist_one(content_entry=content_entry))
            return stats

    def _persist_one(
        self,
        *,
        news_entry: _NewsEntry | None = None,
        content_entry: _ContentEntry | None = None,
    ) -> _SaveStats:
        if (news_entry is None) == (content_entry is None):
            raise ValueError("Exactly one prepared scraper entry is required")
        try:
            return self._persist_prepared_batch(
                [news_entry] if news_entry is not None else [],
                [content_entry] if content_entry is not None else [],
            )
        except Exception as exc:  # noqa: BLE001 - preserve per-item isolation
            if news_entry is not None:
                item = news_entry[0]
            else:
                assert content_entry is not None
                item = content_entry[0]
            return self._failed_item_stats(item, exc)

    @staticmethod
    def _failed_item_stats(item: dict[str, Any], exc: Exception) -> _SaveStats:
        item_url = item.get("url", "unknown")
        logger.error("Error saving item %s: %s", item_url, exc)
        return _SaveStats(
            errors=1,
            error_details=[f"Error saving {item_url}: {str(exc)}"],
        )

    def _persist_prepared_batch(
        self,
        news_entries: list[_NewsEntry],
        content_entries: list[_ContentEntry],
    ) -> _SaveStats:
        stats = _SaveStats()
        queue_requests: list[TaskEnqueueRequest] = []

        with get_db() as db:
            news_results = upsert_news_items(db, [payload for _, payload in news_entries])
            news_items = [news_item for news_item, _ in news_results]
            discussion_rows = sync_news_item_discussions_from_news_items(db, news_items)
            refresh_ids = news_item_discussion_refresh_ids(db, rows=discussion_rows)

            for (item, _), (news_item, was_created) in zip(
                news_entries,
                news_results,
                strict=True,
            ):
                news_item_id = news_item.id
                if news_item_id is None:
                    raise ValueError("News item insert did not produce an id")
                if news_item_id in refresh_ids:
                    queue_requests.append(
                        TaskEnqueueRequest(
                            TaskType.FETCH_NEWS_ITEM_DISCUSSION,
                            payload={"news_item_id": int(news_item_id)},
                        )
                    )
                if should_enqueue_news_item_enrichment(
                    news_item=news_item,
                    was_created=was_created,
                ):
                    queue_requests.append(
                        TaskEnqueueRequest(
                            TaskType.ENRICH_NEWS_ITEM_ARTICLE,
                            payload={"news_item_id": int(news_item_id)},
                            dedupe=False,
                        )
                    )
                if was_created:
                    stats.saved += 1
                else:
                    stats.duplicates += 1
                _record_processed_config_item(stats.processed_by_config_id, item)

            content_keys = {(entry[3], entry[1]) for entry in content_entries}
            existing_by_key = {
                (content.url, content.content_type): content
                for content in (
                    db.query(Content)
                    .filter(tuple_(Content.url, Content.content_type).in_(content_keys))
                    .all()
                    if content_keys
                    else []
                )
            }
            resolved_content: list[tuple[dict[str, Any], str, Content, bool]] = []
            has_new_content = False
            for item, content_type_value, raw_url, canonical_url, metadata in content_entries:
                key = (canonical_url, content_type_value)
                content = existing_by_key.get(key)
                was_created = content is None
                if content is None:
                    content = Content(
                        content_type=content_type_value,
                        url=canonical_url,
                        source_url=raw_url,
                        title=item.get("title"),
                        source=metadata.get("source"),
                        platform=metadata.get("platform"),
                        is_aggregate=bool(item.get("is_aggregate", False)),
                        status=ContentStatus.NEW.value,
                        content_metadata=metadata,
                        created_at=datetime.now(UTC),
                    )
                    db.add(content)
                    has_new_content = True
                    existing_by_key[key] = content
                resolved_content.append((item, content_type_value, content, was_created))

            if has_new_content:
                db.flush()
            status_entries: list[tuple[int | None, int, str | None]] = []
            for item, content_type_value, content, _ in resolved_content:
                if content.id is None:
                    raise ValueError("Content insert did not produce an id")
                status_entries.append((item.get("user_id"), int(content.id), content_type_value))
            created_statuses = ensure_inbox_statuses(db, status_entries)
            if created_statuses:
                db.flush()

            for item, _content_type_value, content, was_created in resolved_content:
                if content.id is None:
                    raise ValueError("Content insert did not produce an id")
                content_id = int(content.id)
                if was_created:
                    queue_requests.append(
                        TaskEnqueueRequest(TaskType.PROCESS_CONTENT, content_id=content_id)
                    )
                    stats.saved += 1
                else:
                    status_key = (item.get("user_id"), content_id)
                    if status_key in created_statuses and self._needs_visible_image(db, content):
                        queue_requests.append(
                            TaskEnqueueRequest(TaskType.GENERATE_IMAGE, content_id=content_id)
                        )
                    logger.debug("URL already exists: %s", item["url"])
                    stats.duplicates += 1
                _record_processed_config_item(stats.processed_by_config_id, item)

            if queue_requests:
                self.queue_service.enqueue_many_in_session(db, queue_requests)

        return stats

    def _needs_visible_image(self, db: Session, content: Content) -> bool:
        content_id = content.id
        return bool(
            content_id is not None
            and is_visible_long_form_image_candidate(db, content)
            and not has_generated_long_form_image(content)
            and not has_active_generate_image_task(db, int(content_id))
        )

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for consistency."""
        # Remove trailing slashes
        url = url.rstrip("/")

        # Ensure https
        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)

        return url


def _record_processed_config_item(counts: dict[int, int], item: dict[str, Any]) -> None:
    config_id = item.get("user_scraper_config_id")
    if isinstance(config_id, int):
        counts[config_id] = counts.get(config_id, 0) + 1
