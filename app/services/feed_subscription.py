"""Helpers for subscribing to detected RSS/Atom feeds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.commands import subscribe_feed as subscribe_feed_command
from app.core.logging import get_logger
from app.models.api.scraper_configs import SubscribeToFeedRequest
from app.models.contracts import FeedSubscriptionOutcome
from app.models.db import UserScraperConfig
from app.models.internal.scraper_configs import canonicalize_feed_url
from app.scraping.rss_helpers import resolve_feed_source
from app.services.active_users import lock_active_user
from app.services.scraper_configs import (
    ALLOWED_SCRAPER_TYPES,
)

logger = get_logger(__name__)

type FeedSubscriptionStatus = Literal[
    "created",
    "reactivated",
    "already_exists",
    "missing_user",
    "missing_feed",
    "missing_feed_url",
    "missing_feed_type",
    "unsupported_feed_type",
    "inactive_user",
    "subscription_failed",
]


@dataclass(frozen=True)
class FeedSubscriptionResult:
    """Outcome for creating a feed subscription."""

    created: bool
    status: FeedSubscriptionStatus
    config_id: int | None = None
    backfill_task_id: int | None = None
    error_message: str | None = None


def load_active_feed_urls(
    db: Session,
    *,
    user_id: int,
    feed_type: str | None = None,
) -> set[str]:
    """Return canonical URLs for one user's active feed subscriptions."""
    query = db.query(UserScraperConfig.feed_url).filter(
        UserScraperConfig.user_id == user_id,
        UserScraperConfig.is_active.is_(True),
        UserScraperConfig.feed_url.is_not(None),
    )
    if feed_type is not None:
        query = query.filter(UserScraperConfig.scraper_type == feed_type)

    return {
        canonicalize_feed_url(feed_url)
        for (feed_url,) in query.all()
        if isinstance(feed_url, str) and feed_url.strip()
    }


def is_feed_already_subscribed(
    db: Session,
    user_id: int,
    feed_type: str,
    feed_url: str,
) -> bool:
    """Check whether the user already has an active config for the feed."""
    if not feed_url.strip():
        return False

    return canonicalize_feed_url(feed_url) in load_active_feed_urls(
        db,
        user_id=user_id,
        feed_type=feed_type,
    )


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

    active_user_id = lock_active_user(db, user_id)
    if active_user_id is None:
        return False

    return not is_feed_already_subscribed(db, active_user_id, feed_type, feed_url)


def subscribe_to_detected_feed(
    db: Session,
    user_id: int | None,
    detected_feed: dict[str, Any] | None,
    *,
    display_name: str | None = None,
) -> tuple[bool, FeedSubscriptionStatus]:
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
    active_user_id = lock_active_user(db, user_id)
    if active_user_id is None:
        return FeedSubscriptionResult(created=False, status="inactive_user")
    feed_title = detected_feed.get("title")
    resolved_display_name = resolve_feed_source(
        display_name,
        feed_title if isinstance(feed_title, str) else None,
        feed_url,
    )

    payload = SubscribeToFeedRequest(
        feed_type=feed_type,
        display_name=resolved_display_name,
        feed_url=feed_url.strip(),
    )

    try:
        result = subscribe_feed_command.execute(
            db,
            user_id=active_user_id,
            payload=payload,
        )
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
        return FeedSubscriptionResult(
            created=False,
            status="subscription_failed",
            error_message=str(exc),
        )

    if result.outcome is FeedSubscriptionOutcome.ALREADY_SUBSCRIBED:
        return FeedSubscriptionResult(
            created=False,
            status="already_exists",
            config_id=result.config.id,
        )

    if result.outcome is FeedSubscriptionOutcome.REACTIVATED:
        return FeedSubscriptionResult(
            created=False,
            status="reactivated",
            config_id=result.config.id,
            backfill_task_id=result.backfill_task_id,
        )

    return FeedSubscriptionResult(
        created=True,
        status="created",
        config_id=result.config.id,
        backfill_task_id=result.backfill_task_id,
    )
