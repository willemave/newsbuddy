"""Service-owned validation for scraper config payloads."""

from __future__ import annotations

from typing import Any

from app.constants import AGGREGATOR_SCRAPER_TYPE
from app.models.internal.scraper_configs import (
    canonicalize_feed_url,
    normalize_aggregator_config,
    normalize_feed_config,
    normalize_reddit_config,
    normalize_youtube_config,
)
from app.services.feed_research_runtime import feed_research_runtime


def validate_and_normalize_scraper_config(
    scraper_type: str,
    config: dict[str, Any],
    *,
    user_id: int,
) -> dict[str, Any]:
    """Normalize scraper config and run service-backed feed validation."""

    if scraper_type == "youtube":
        return normalize_youtube_config(dict(config))
    if scraper_type == "reddit":
        return normalize_reddit_config(dict(config))
    if scraper_type == AGGREGATOR_SCRAPER_TYPE:
        return normalize_aggregator_config(dict(config))

    normalized = normalize_feed_config(dict(config))
    with feed_research_runtime(user_id=user_id, use_llm=False) as runtime:
        validated_feed = runtime.detector.validate_feed_url(normalized["feed_url"])
    if not validated_feed:
        raise ValueError("config.feed_url must be a valid RSS/Atom feed URL")
    normalized["feed_url"] = canonicalize_feed_url(validated_feed["feed_url"])
    return normalized
