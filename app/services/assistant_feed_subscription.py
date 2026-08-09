"""Known-feed subscription behavior for assistant chat tools."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models.contracts import FeedType
from app.models.internal.scraper_configs import (
    canonicalize_feed_url,
    normalize_feed_type_alias,
)
from app.services.feed_subscription import subscribe_to_detected_feed_result
from app.utils.url_utils import is_http_url

logger = get_logger(__name__)


def subscribe_known_feed(
    session_factory: sessionmaker[Session],
    *,
    user_id: int,
    url: str,
    title: str | None,
    feed_type: str,
) -> str:
    """Subscribe to a validated assistant option and format the tool result."""
    normalized_feed_type = normalize_feed_type_alias(feed_type)
    if normalized_feed_type not in {item.value for item in FeedType}:
        return f"Unable to subscribe: unsupported feed type {feed_type}."

    normalized_url = canonicalize_feed_url(url)
    if not is_http_url(normalized_url):
        return "Unable to subscribe: invalid feed URL."

    label = (title or normalized_url).strip()
    try:
        with session_factory() as db:
            result = subscribe_to_detected_feed_result(
                db,
                user_id,
                {"url": normalized_url, "type": normalized_feed_type, "title": title},
                display_name=title,
            )
            if result.status in {"created", "reactivated", "already_exists"}:
                db.commit()
            else:
                db.rollback()
    except Exception:  # noqa: BLE001
        logger.exception(
            "Assistant known-feed subscription failed",
            extra={
                "component": "assistant_router",
                "operation": "subscribe_known_feed",
                "user_id": user_id,
                "context_data": {
                    "feed_url": normalized_url,
                    "feed_type": normalized_feed_type,
                },
            },
        )
        return f"Unable to subscribe to {label} (temporary failure)."

    if result.status == "created":
        return f"Subscribed to {label}."
    if result.status == "reactivated":
        return f"Re-enabled {label}."
    if result.status == "already_exists":
        return f"Already subscribed to {label}."
    detail = f": {result.error_message}" if result.error_message else ""
    return f"Unable to subscribe to {label} ({result.status}){detail}."
