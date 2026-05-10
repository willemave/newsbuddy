"""Scheduled discussion comment refresher for news items."""

from __future__ import annotations

from typing import Any

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.domain.scraper_runs import ScraperStats
from app.scraping.base import BaseScraper
from app.services.news_item_discussions import refresh_due_news_item_discussions

logger = get_logger(__name__)


class DiscussionCommentsScraper(BaseScraper):
    """Refresh due Hacker News and Reddit comment summaries."""

    KEY = "discussion_comments"
    DISPLAY_NAME = "DiscussionComments"

    def __init__(self, limit: int = 10) -> None:
        super().__init__(self.DISPLAY_NAME)
        self.limit = limit

    def scrape(self) -> list[dict[str, Any]]:
        """This scraper refreshes existing rows instead of returning new items."""
        return []

    def run_with_stats(self) -> ScraperStats:
        logger.info("Running discussion comments scraper")
        stats = ScraperStats()
        try:
            with get_db() as db:
                results = refresh_due_news_item_discussions(db, limit=self.limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Discussion comments scraper failed")
            return ScraperStats(errors=1, error_details=[str(exc)])

        stats.scraped = len(results)
        for result in results:
            if not result.success:
                stats.errors += 1
                if result.error_message:
                    stats.error_details.append(result.error_message)
            elif result.refreshed or result.summarized:
                stats.saved += 1
            else:
                stats.duplicates += 1
        return stats
