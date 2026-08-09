"""Application command for idempotent feed subscription."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.constants import (
    DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    DEFAULT_NEW_FEED_LIMIT,
)
from app.models.api.scraper_configs import SubscribeToFeedRequest
from app.models.contracts import FeedSubscriptionOutcome
from app.models.db import UserScraperConfig
from app.models.internal.feed_backfill import (
    BACKFILL_SUPPORTED_TYPES,
    FeedBatchBackfillRequest,
)
from app.models.internal.scraper_configs import CreateUserScraperConfig, canonicalize_feed_url
from app.services.queue import QueueService, TaskEnqueueRequest, TaskType, get_queue_service
from app.services.scraper_configs import (
    ALLOWED_SCRAPER_TYPES,
    ScraperConfigAlreadyExistsError,
    create_user_scraper_config_in_session,
)

ScraperTypeLiteral = Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]


@dataclass(frozen=True)
class SubscribeFeedResult:
    """Persisted subscription plus its idempotent outcome and initial work."""

    config: UserScraperConfig
    outcome: FeedSubscriptionOutcome
    backfill_task_id: int | None


def execute(
    db: Session,
    *,
    user_id: int,
    payload: SubscribeToFeedRequest,
    enqueue_initial_backfill: bool = True,
    queue_service: QueueService | None = None,
) -> SubscribeFeedResult:
    """Create or reuse a subscription and transactionally enqueue first content."""
    if payload.feed_type not in ALLOWED_SCRAPER_TYPES:
        raise ValueError(f"Unsupported feed type: {payload.feed_type}")

    config_data: dict[str, object] = {
        "feed_url": payload.feed_url,
        "limit": DEFAULT_NEW_FEED_LIMIT,
    }
    if payload.feed_type == "reddit":
        subreddit = _subreddit_from_url(payload.feed_url)
        if subreddit is None:
            raise ValueError("Reddit subscriptions require an /r/<subreddit> URL")
        config_data = {"subreddit": subreddit, "limit": DEFAULT_NEW_FEED_LIMIT}

    create_payload = CreateUserScraperConfig(
        scraper_type=cast(ScraperTypeLiteral, payload.feed_type),
        display_name=payload.display_name,
        config=config_data,
        is_active=True,
    )

    try:
        config = create_user_scraper_config_in_session(db, user_id, create_payload)
        outcome = FeedSubscriptionOutcome.CREATED
    except ScraperConfigAlreadyExistsError as exc:
        existing_config = exc.existing_config
        if existing_config is None:
            raise RuntimeError(
                "Subscription conflict did not resolve to an existing config"
            ) from exc
        config = existing_config
        if config.is_active:
            outcome = FeedSubscriptionOutcome.ALREADY_SUBSCRIBED
        else:
            config.is_active = True
            if not isinstance(config.feed_url, str) or not config.feed_url.strip():
                raw_config = config.config if isinstance(config.config, dict) else {}
                raw_feed_url = raw_config.get("feed_url") or payload.feed_url
                if isinstance(raw_feed_url, str) and raw_feed_url.strip():
                    config.feed_url = canonicalize_feed_url(raw_feed_url)
            outcome = FeedSubscriptionOutcome.REACTIVATED

    backfill_task_id: int | None = None
    config_id = config.id
    if (
        outcome
        in {
            FeedSubscriptionOutcome.CREATED,
            FeedSubscriptionOutcome.REACTIVATED,
        }
        and enqueue_initial_backfill
        and payload.feed_type in BACKFILL_SUPPORTED_TYPES
    ):
        if config_id is None:
            raise ValueError("Scraper config is missing an id")
        request = TaskEnqueueRequest(
            task_type=TaskType.BACKFILL_FEEDS,
            payload=FeedBatchBackfillRequest(
                user_id=user_id,
                config_ids=[int(config_id)],
                count=DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
            ).model_dump(),
            dedupe=True,
            owner_user_id=user_id,
        )
        backfill_task_id = (queue_service or get_queue_service()).enqueue_many_in_session(
            db,
            [request],
        )[0]

    db.flush()
    db.refresh(config)
    return SubscribeFeedResult(
        config=config,
        outcome=outcome,
        backfill_task_id=backfill_task_id,
    )


def _subreddit_from_url(feed_url: str) -> str | None:
    """Extract a subreddit name from a canonical Reddit community URL."""
    path_parts = [part for part in urlparse(feed_url.strip()).path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "r":
        return None
    return path_parts[1]
