"""Service-owned validation for scraper config payloads."""

from __future__ import annotations

from typing import Any

from app.constants import AGGREGATOR_SCRAPER_TYPE
from app.models.internal.scraper_configs import (
    normalize_aggregator_config,
    normalize_feed_config,
    normalize_reddit_config,
    normalize_youtube_config,
)
from app.services.feed_detection import FeedDetector

FEED_VALIDATOR = FeedDetector(use_llm=False, use_exa_search=False)


def validate_and_normalize_scraper_config(
    scraper_type: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Normalize scraper config and run service-backed feed validation."""

    if scraper_type == "youtube":
        return normalize_youtube_config(dict(config))
    if scraper_type == "reddit":
        return normalize_reddit_config(dict(config))
    if scraper_type == AGGREGATOR_SCRAPER_TYPE:
        return normalize_aggregator_config(dict(config))

    normalized = normalize_feed_config(dict(config))
    validated_feed = FEED_VALIDATOR.validate_feed_url(normalized["feed_url"])
    if not validated_feed:
        raise ValueError("config.feed_url must be a valid RSS/Atom feed URL")
    normalized["feed_url"] = validated_feed["feed_url"]
    return normalized
