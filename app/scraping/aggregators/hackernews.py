"""HackerNews aggregator scraper (Firebase API)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.scraping.aggregators.base import AggregatorScraper
from app.scraping.aggregators.config import HackerNewsAggregator

logger = get_logger(__name__)


class HackerNewsAggregatorScraper(AggregatorScraper):
    """Scrape the HackerNews top-stories feed via the public Firebase API."""

    KEY = "hackernews"
    DISPLAY_NAME = "HackerNews"

    def __init__(self, settings: HackerNewsAggregator) -> None:
        super().__init__(name=settings.name)
        self.settings = settings
        self.api_base_url = str(settings.api_base_url).rstrip("/")
        self.site_base_url = str(settings.site_base_url).rstrip("/")

    def scrape(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        with httpx.Client(timeout=10.0) as client:
            try:
                top_response = client.get(f"{self.api_base_url}/topstories.json")
                story_ids = top_response.json()[: self.settings.limit]
            except Exception as exc:  # pragma: no cover - network failure guard
                logger.exception("Failed to fetch HN top stories: %s", exc)
                return items

            for story_id in story_ids:
                try:
                    story_response = client.get(f"{self.api_base_url}/item/{story_id}.json")
                    story = story_response.json()
                except Exception as exc:
                    logger.error("Error fetching HN story %s: %s", story_id, exc)
                    continue

                if not story or story.get("type") != "story" or "url" not in story:
                    continue

                story_url = self._normalize_url(story["url"])
                host = urlparse(story_url).netloc or ""
                discussion_url = f"{self.site_base_url}/item?id={story_id}"

                items.append(
                    {
                        "url": story_url,
                        "title": story.get("title"),
                        "content_type": ContentType.NEWS,
                        "is_aggregate": False,
                        "metadata": {
                            "platform": self.KEY,
                            "source": host,
                            "article": {
                                "url": story_url,
                                "title": story.get("title"),
                                "source_domain": host,
                            },
                            "aggregator": {
                                "key": self.KEY,
                                "name": self.settings.name,
                                "title": story.get("title"),
                                "external_id": str(story_id),
                                "author": story.get("by"),
                                "metadata": {
                                    "score": story.get("score", 0),
                                    "comments_count": story.get("descendants", 0),
                                    "item_type": story.get("type"),
                                    "timestamp": story.get("time"),
                                    "hn_linked_url": story_url,
                                },
                            },
                            "discussion_url": discussion_url,
                            "excerpt": story.get("text"),
                            "discovery_time": self.now_iso(),
                        },
                    }
                )

        return items
