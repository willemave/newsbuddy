"""Scheduled discussion comment refresher for news items."""

from __future__ import annotations

from typing import Any

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.domain.scraper_runs import ScraperStats
from app.scraping.base import BaseScraper
from app.services.news_item_discussions import (
    DEFAULT_DISCUSSION_REFRESH_ENQUEUE_LIMIT,
    list_due_news_item_discussion_refresh_candidates,
    sync_missing_visible_news_item_discussions,
)
from app.services.queue import TaskType, get_queue_service

logger = get_logger(__name__)


class DiscussionCommentsScraper(BaseScraper):
    """Refresh due Hacker News and Reddit comment summaries."""

    KEY = "discussion_comments"
    DISPLAY_NAME = "DiscussionComments"

    def __init__(self, limit: int = DEFAULT_DISCUSSION_REFRESH_ENQUEUE_LIMIT) -> None:
        super().__init__(self.DISPLAY_NAME)
        self.limit = limit

    def scrape(self) -> list[dict[str, Any]]:
        """This scraper refreshes existing rows instead of returning new items."""
        return []

    def run_with_stats(self) -> ScraperStats:
        logger.info("Enqueueing due discussion comment refreshes")
        stats = ScraperStats()
        try:
            with get_db() as db:
                sync_missing_visible_news_item_discussions(db, limit=max(self.limit * 2, 50))
                news_item_ids = list_due_news_item_discussion_refresh_candidates(
                    db,
                    limit=self.limit,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Discussion comments scraper failed")
            return ScraperStats(errors=1, error_details=[str(exc)])

        stats.scraped = len(news_item_ids)
        queue_service = get_queue_service()
        for news_item_id in news_item_ids:
            try:
                queue_service.enqueue(
                    TaskType.FETCH_NEWS_ITEM_DISCUSSION,
                    payload={"news_item_id": news_item_id},
                )
                stats.saved += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Unable to enqueue discussion comment refresh",
                    extra={
                        "component": "discussion_comments",
                        "operation": "enqueue",
                        "item_id": str(news_item_id),
                    },
                )
                stats.errors += 1
                stats.error_details.append(str(exc))
        return stats
