"""Application command for subscribing to discovery suggestions."""

from __future__ import annotations

from typing import Literal, cast

from sqlalchemy.orm import Session

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.core.logging import get_logger
from app.models.api.discovery import DiscoverySubscribeRequest, DiscoverySubscribeResponse
from app.models.internal.scraper_configs import CreateUserScraperConfig
from app.repositories.discovery_repository import list_user_suggestions_by_ids
from app.services.scraper_configs import (
    ScraperConfigAlreadyExistsError,
    create_user_scraper_config,
)

logger = get_logger(__name__)

ScraperTypeLiteral = Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]
SUBSCRIBABLE_TYPES = {"substack", "atom", "podcast_rss", "youtube", "reddit"}


def _is_youtube_watch_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "youtube.com/watch" in lowered or "youtu.be/" in lowered


def execute(
    db: Session,
    *,
    user_id: int,
    payload: DiscoverySubscribeRequest,
) -> DiscoverySubscribeResponse:
    """Subscribe to selected discovery suggestions."""
    suggestions = list_user_suggestions_by_ids(
        db,
        user_id=user_id,
        suggestion_ids=payload.suggestion_ids,
    )

    subscribed: list[int] = []
    skipped: list[int] = []
    errors: list[dict[str, str]] = []

    for suggestion in suggestions:
        suggestion_id = suggestion.id
        if suggestion_id is None:
            continue
        if suggestion.status == "subscribed":
            skipped.append(suggestion_id)
            continue
        if suggestion.suggestion_type == "youtube" and _is_youtube_watch_url(suggestion.feed_url):
            skipped.append(suggestion_id)
            errors.append(
                {"id": str(suggestion_id), "error": "youtube_watch_url_requires_add_item"}
            )
            continue

        try:
            scraper_type = suggestion.suggestion_type
            if scraper_type not in SUBSCRIBABLE_TYPES:
                errors.append({"id": str(suggestion_id), "error": "invalid_suggestion_type"})
                continue
            config_payload = {**(suggestion.config or {})}
            if suggestion.feed_url and not config_payload.get("feed_url"):
                config_payload["feed_url"] = suggestion.feed_url
            if "limit" not in config_payload:
                config_payload["limit"] = DEFAULT_NEW_FEED_LIMIT
            create_user_scraper_config(
                db,
                user_id=user_id,
                data=CreateUserScraperConfig(
                    scraper_type=cast(ScraperTypeLiteral, scraper_type),
                    display_name=suggestion.title,
                    config=config_payload,
                ),
            )
            suggestion.status = "subscribed"
            subscribed.append(suggestion_id)
        except ScraperConfigAlreadyExistsError:
            suggestion.status = "subscribed"
            subscribed.append(suggestion_id)
        except ValueError as exc:
            errors.append({"id": str(suggestion_id), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to subscribe discovery suggestion",
                extra={
                    "component": "feed_discovery",
                    "operation": "subscribe",
                    "item_id": str(suggestion_id),
                    "context_data": {"error": str(exc)},
                },
            )
            errors.append({"id": str(suggestion_id), "error": str(exc)})

    db.commit()
    return DiscoverySubscribeResponse(subscribed=subscribed, skipped=skipped, errors=errors)
