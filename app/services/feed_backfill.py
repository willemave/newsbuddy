"""Helpers for on-demand feed backfills ("download more from this series")."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import get_logger
from app.models.db import Content, UserScraperConfig
from app.models.internal.feed_backfill import FeedBackfillRequest, FeedBackfillResult
from app.scraping.atom_unified import AtomScraper
from app.scraping.podcast_unified import PodcastUnifiedScraper
from app.scraping.substack_unified import SubstackScraper
from app.services.scraper_configs import build_feed_payloads

logger = get_logger(__name__)

DEFAULT_FEED_SCRAPE_LIMIT = 10


def _require_config_id(config: UserScraperConfig) -> int:
    """Return a persisted scraper-config ID or raise."""
    config_id = config.id
    if config_id is None:
        raise ValueError("User scraper config must be persisted before use")
    return config_id


def _require_scraper_type(config: UserScraperConfig) -> str:
    """Return a scraper type or raise."""
    scraper_type = config.scraper_type
    if not isinstance(scraper_type, str) or not scraper_type:
        raise ValueError("Scraper config missing scraper_type")
    return scraper_type


def resolve_feed_config_for_content(
    db: Session,
    user_id: int,
    content: Content,
) -> UserScraperConfig | None:
    """Resolve the user scraper config associated with a content item.

    Args:
        db: Active database session.
        user_id: Current user id.
        content: Content row to resolve.

    Returns:
        Matching UserScraperConfig or None.
    """
    metadata = content.content_metadata or {}

    feed_config_id = metadata.get("feed_config_id")
    if isinstance(feed_config_id, str) and feed_config_id.isdigit():
        feed_config_id = int(feed_config_id)
    if isinstance(feed_config_id, int):
        config = (
            db.query(UserScraperConfig)
            .filter(UserScraperConfig.id == feed_config_id)
            .filter(UserScraperConfig.user_id == user_id)
            .filter(UserScraperConfig.is_active.is_(True))
            .first()
        )
        if config:
            return config

    feed_url = metadata.get("feed_url")
    if isinstance(feed_url, str) and feed_url.strip():
        normalized_target = _normalize_feed_url(feed_url)
        configs = _active_user_configs(db, user_id)
        matches = [
            config
            for config in configs
            if _normalize_feed_url(_config_feed_url(config)) == normalized_target
        ]
        if len(matches) == 1:
            return matches[0]

    source = content.source or metadata.get("source")
    if isinstance(source, str) and source.strip():
        source_value = source.strip().lower()
        candidates = _active_user_configs(db, user_id)
        matches = []
        for config in candidates:
            display_name = (config.display_name or "").strip().lower()
            config_name = str((config.config or {}).get("name") or "").strip().lower()
            feed_domain = _extract_domain(_config_feed_url(config))
            if display_name and display_name == source_value:
                matches.append(config)
                continue
            if config_name and config_name == source_value:
                matches.append(config)
                continue
            if feed_domain and feed_domain == source_value:
                matches.append(config)
        if len(matches) == 1:
            return matches[0]

        if len(matches) > 1:
            logger.error(
                "Multiple feed configs matched content source",
                extra={
                    "component": "feed_backfill",
                    "operation": "resolve_config",
                    "item_id": str(content.id),
                    "context_data": {
                        "user_id": user_id,
                        "source": source_value,
                        "match_ids": [m.id for m in matches],
                    },
                },
            )
            return None

    logger.error(
        "Unable to resolve feed config for content",
        extra={
            "component": "feed_backfill",
            "operation": "resolve_config",
            "item_id": str(content.id),
            "context_data": {
                "user_id": user_id,
                "feed_config_id": metadata.get("feed_config_id"),
                "feed_url": metadata.get("feed_url"),
                "source": content.source,
            },
        },
    )
    return None


def backfill_feed_for_config(request: FeedBackfillRequest) -> FeedBackfillResult:
    """Backfill older items for a feed configuration.

    Args:
        request: FeedBackfillRequest containing user, config id, and count.

    Returns:
        FeedBackfillResult with scraper stats and computed limits.
    """
    with get_db() as db:
        config = (
            db.query(UserScraperConfig)
            .filter(UserScraperConfig.id == request.config_id)
            .filter(UserScraperConfig.user_id == request.user_id)
            .first()
        )

        if not config:
            raise ValueError("feed_config_not_found")

        if not config.is_active:
            raise ValueError("feed_config_inactive")

        base_limit = _resolve_limit(config.config or {}, DEFAULT_FEED_SCRAPE_LIMIT)
        target_limit = min(100, base_limit + request.count)

        feed_payloads = build_feed_payloads([config], default_limit=base_limit)
        if not feed_payloads:
            raise ValueError("feed_url_missing")

        feed_info = {**feed_payloads[0], "limit": target_limit}
        scraper = _single_feed_scraper(_require_scraper_type(config), feed_info)
        stats = scraper.run_with_stats()

        return FeedBackfillResult(
            config_id=_require_config_id(config),
            base_limit=base_limit,
            target_limit=target_limit,
            scraped=stats.scraped,
            saved=stats.saved,
            duplicates=stats.duplicates,
            errors=stats.errors,
        )


def _resolve_limit(config: dict[str, Any], default_limit: int) -> int:
    limit = config.get("limit")
    if isinstance(limit, int) and 1 <= limit <= 100:
        return limit
    return default_limit


def _config_feed_url(config: UserScraperConfig) -> str:
    return (config.feed_url or (config.config or {}).get("feed_url") or "").strip()


def _normalize_feed_url(feed_url: str) -> str:
    try:
        parsed = urlparse(feed_url.strip())
    except Exception:
        return feed_url.strip().rstrip("/")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path)
    return normalized.geturl()


def _extract_domain(feed_url: str) -> str | None:
    if not feed_url:
        return None
    try:
        parsed = urlparse(feed_url)
    except Exception:
        return None
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def _active_user_configs(db: Session, user_id: int) -> list[UserScraperConfig]:
    return (
        db.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.is_active.is_(True))
        .all()
    )


def _single_feed_scraper(scraper_type: str, feed_info: dict[str, Any]):
    if scraper_type == "substack":
        return _single_scraper(SubstackScraper, feed_info, load_attr="_load_feeds")
    if scraper_type == "atom":
        return _single_scraper(AtomScraper, feed_info, load_attr="_load_feeds")
    if scraper_type == "podcast_rss":
        return _single_scraper(
            PodcastUnifiedScraper,
            feed_info,
            load_attr="_load_podcast_feeds",
        )
    raise ValueError(f"unsupported_scraper_type:{scraper_type}")


def _single_scraper(scraper_cls, feed_info: dict[str, Any], *, load_attr: str):
    class _SingleFeedScraper(scraper_cls):
        def __init__(self, feed: dict[str, Any]):
            super().__init__()
            self._feed_override = feed

        def _load_override(self) -> list[dict[str, Any]]:
            return [self._feed_override]

    setattr(_SingleFeedScraper, load_attr, _SingleFeedScraper._load_override)
    return _SingleFeedScraper(feed_info)
