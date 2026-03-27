"""Helpers for subscribing to detected RSS/Atom feeds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.core.logging import get_logger
from app.models.schema import UserScraperConfig
from app.scraping.rss_helpers import resolve_feed_source
from app.services.scraper_configs import (
    ALLOWED_SCRAPER_TYPES,
    CreateUserScraperConfig,
    create_user_scraper_config,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class FeedSubscriptionResult:
    """Outcome for creating a feed subscription."""

    created: bool
    status: str
    config_id: int | None = None


def _normalize_feed_url_for_lookup(feed_url: str) -> str:
    trimmed = feed_url.strip()
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return trimmed.rstrip("/")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path)
    return urlunparse(normalized)


def is_feed_already_subscribed(
    db: Session,
    user_id: int,
    feed_type: str,
    feed_url: str,
) -> bool:
    """Check whether the user already has an active config for the feed."""
    if not feed_url.strip():
        return False

    normalized_target = _normalize_feed_url_for_lookup(feed_url)

    configs = (
        db.query(UserScraperConfig.feed_url)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.scraper_type == feed_type)
        .filter(UserScraperConfig.is_active.is_(True))
        .all()
    )
    for (existing_url,) in configs:
        if not existing_url:
            continue
        if _normalize_feed_url_for_lookup(existing_url) == normalized_target:
            return True
    return False


def can_subscribe_to_feed(
    db: Session,
    user_id: int | None,
    detected_feed: dict[str, Any] | None,
) -> bool:
    """Return True if the detected feed can be subscribed to for this user."""
    if user_id is None:
        return False
    if not isinstance(detected_feed, dict):
        return False

    feed_url = detected_feed.get("url")
    feed_type = detected_feed.get("type")
    if not isinstance(feed_url, str) or not feed_url.strip():
        return False
    if not isinstance(feed_type, str) or not feed_type.strip():
        return False
    if feed_type not in ALLOWED_SCRAPER_TYPES:
        return False

    return not is_feed_already_subscribed(db, user_id, feed_type, feed_url)


def subscribe_to_detected_feed(
    db: Session,
    user_id: int | None,
    detected_feed: dict[str, Any] | None,
    *,
    display_name: str | None = None,
) -> tuple[bool, str]:
    """Create a scraper config for a detected feed."""
    result = subscribe_to_detected_feed_result(
        db,
        user_id,
        detected_feed,
        display_name=display_name,
    )
    return result.created, result.status


def subscribe_to_detected_feed_result(
    db: Session,
    user_id: int | None,
    detected_feed: dict[str, Any] | None,
    *,
    display_name: str | None = None,
) -> FeedSubscriptionResult:
    """Create a scraper config for a detected feed.

    Args:
        db: Active database session.
        user_id: User identifier (required).
        detected_feed: Dict containing feed details (url/type/title/format).
        display_name: Optional display name to store with the feed config.

    Returns:
        FeedSubscriptionResult describing the outcome and created config id.
    """
    if user_id is None:
        return FeedSubscriptionResult(created=False, status="missing_user")
    if not isinstance(detected_feed, dict):
        return FeedSubscriptionResult(created=False, status="missing_feed")

    feed_url = detected_feed.get("url")
    feed_type = detected_feed.get("type")
    if not isinstance(feed_url, str) or not feed_url.strip():
        return FeedSubscriptionResult(created=False, status="missing_feed_url")
    if not isinstance(feed_type, str) or not feed_type.strip():
        return FeedSubscriptionResult(created=False, status="missing_feed_type")
    if feed_type not in ALLOWED_SCRAPER_TYPES:
        return FeedSubscriptionResult(created=False, status="unsupported_feed_type")
    feed_title = detected_feed.get("title")
    resolved_display_name = resolve_feed_source(
        display_name,
        feed_title if isinstance(feed_title, str) else None,
        feed_url,
    )

    payload = CreateUserScraperConfig(
        scraper_type=feed_type,
        display_name=resolved_display_name,
        config={
            "feed_url": feed_url.strip(),
            "limit": DEFAULT_NEW_FEED_LIMIT,
        },
        is_active=True,
    )

    try:
        record = create_user_scraper_config(db, user_id, payload)
    except ValueError as exc:
        logger.info(
            "Feed subscription skipped for user %s: %s",
            user_id,
            exc,
            extra={
                "component": "feed_subscription",
                "operation": "subscribe",
                "context_data": {"feed_url": feed_url, "feed_type": feed_type},
            },
        )
        return FeedSubscriptionResult(created=False, status="already_exists")

    return FeedSubscriptionResult(created=True, status="created", config_id=record.id)
