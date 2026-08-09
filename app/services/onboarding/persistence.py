"""Persistence and database projections for onboarding workflows."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import (
    DEFAULT_NEW_FEED_LIMIT,
    SUPPORTED_AGGREGATOR_KEYS,
)
from app.core.logging import get_logger
from app.models.api.onboarding import (
    OnboardingDiscoveryLaneStatus,
    OnboardingFastDiscoverResponse,
    OnboardingSelectedAggregator,
    OnboardingSelectedSource,
    OnboardingSuggestion,
)
from app.models.contracts import ContentStatus, ContentType, OnboardingSuggestionType
from app.models.db import (
    Content,
    ContentStatusEntry,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    OnboardingDiscoverySuggestion,
    UserScraperConfig,
)
from app.models.internal.scraper_configs import CreateUserScraperConfig
from app.repositories.content_repository import apply_visibility_filters, build_visibility_context
from app.services.feed_research_runtime import FeedResearchRuntimeError
from app.services.onboarding.config import (
    FEED_CONTENT_SEED_LIMIT,
    NEWS_SEED_LIMIT,
    SCRAPER_SOURCE_BY_TYPE,
)
from app.services.onboarding.suggestion_projection import (
    _default_rationale,
    _normalize_score,
    _normalize_subreddit_name,
    _normalize_suggestion_type,
    _suggestion_key,
)
from app.services.scraper_configs import (
    ScraperConfigAlreadyExistsError,
    create_user_scraper_config_in_session,
)

logger = get_logger(__name__)


def _require_run_id(run: OnboardingDiscoveryRun) -> int:
    """Return a persisted discovery run ID or raise."""
    run_id = run.id
    if run_id is None:
        raise ValueError("Onboarding discovery run must be persisted before use")
    return run_id


def _require_run_status(run: OnboardingDiscoveryRun) -> str:
    """Return a discovery run status or raise."""
    status = run.status
    if not isinstance(status, str) or not status:
        raise ValueError("Onboarding discovery run missing status")
    return status


def _persist_scraper_config_idempotent(
    db: Session,
    *,
    user_id: int,
    data: CreateUserScraperConfig,
    operation: str,
    log_context: dict[str, Any],
    raise_on_error: bool = False,
) -> UserScraperConfig | None:
    """Create idempotently, optionally propagating errors for atomic callers."""
    try:
        return create_user_scraper_config_in_session(db, user_id=user_id, data=data)
    except ScraperConfigAlreadyExistsError as exc:
        if exc.existing_config is not None:
            return exc.existing_config
        return (
            db.query(UserScraperConfig)
            .filter(UserScraperConfig.user_id == user_id)
            .filter(UserScraperConfig.scraper_type == data.scraper_type)
            .filter(UserScraperConfig.feed_url == data.config.get("feed_url"))
            .first()
        )
    except FeedResearchRuntimeError:
        raise
    except ValueError as exc:
        logger.error(
            "Failed to create onboarding scraper config",
            extra={
                "component": "onboarding",
                "operation": operation,
                "item_id": str(user_id),
                "context_data": {"error": str(exc), **log_context},
            },
        )
        if raise_on_error:
            raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected error creating onboarding scraper config",
            extra={
                "component": "onboarding",
                "operation": operation,
                "item_id": str(user_id),
                "context_data": {"error": str(exc), **log_context},
            },
        )
        if raise_on_error:
            raise
    return None


def _create_reddit_configs(db: Session, user_id: int, subreddits: list[str]) -> int:
    configured_count = 0
    for subreddit in subreddits:
        cleaned = _normalize_subreddit_name(subreddit)
        if not cleaned:
            continue
        if (
            _persist_scraper_config_idempotent(
                db,
                user_id=user_id,
                data=CreateUserScraperConfig(
                    scraper_type="reddit",
                    display_name=cleaned,
                    config={"subreddit": cleaned, "limit": DEFAULT_NEW_FEED_LIMIT},
                ),
                operation="create_subreddit",
                log_context={"subreddit": cleaned},
            )
            is not None
        ):
            configured_count += 1
    return configured_count


def _create_aggregator_configs(
    db: Session,
    user_id: int,
    aggregators: list[OnboardingSelectedAggregator],
) -> int:
    """Persist user fast-news aggregator subscriptions.

    Each pick becomes a ``user_scraper_configs`` row with
    ``scraper_type='aggregator'`` and ``feed_url='aggregator://<key>'``. Topics
    (Brutalist Report) are stored on ``config.topics``.
    """
    configured_count = 0
    for selection in aggregators:
        key = selection.key.strip().lower()
        if key not in SUPPORTED_AGGREGATOR_KEYS:
            continue
        config_payload: dict[str, Any] = {"key": key, "limit": DEFAULT_NEW_FEED_LIMIT}
        if selection.topics:
            config_payload["topics"] = selection.topics
        if (
            _persist_scraper_config_idempotent(
                db,
                user_id=user_id,
                data=CreateUserScraperConfig(
                    scraper_type="aggregator",
                    display_name=selection.title or key,
                    config=config_payload,
                ),
                operation="create_aggregator",
                log_context={"aggregator": key},
            )
            is not None
        ):
            configured_count += 1
    return configured_count


def _resolve_scraper_sources(types: set[str]) -> list[str]:
    sources = [
        SCRAPER_SOURCE_BY_TYPE[type_name]
        for type_name in types
        if type_name in SCRAPER_SOURCE_BY_TYPE
    ]
    return sorted(set(sources))


def _estimate_inbox_count(db: Session, user_id: int) -> int:
    context = build_visibility_context(user_id)
    count_query = db.query(func.count(Content.id))
    count_query = apply_visibility_filters(count_query, context)
    count_query = count_query.filter(~context.is_read)
    return count_query.scalar() or 0


def _seed_recent_news_for_user(db: Session, user_id: int, limit: int = NEWS_SEED_LIMIT) -> int:
    """Seed recent news items into a user's inbox."""
    if user_id <= 0 or limit <= 0:
        return 0

    existing = select(ContentStatusEntry.content_id).where(ContentStatusEntry.user_id == user_id)
    news_ids = (
        db.query(Content.id)
        .filter(
            Content.content_type == ContentType.NEWS.value,
            Content.status == ContentStatus.COMPLETED.value,
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .filter(~Content.id.in_(existing))
        .order_by(Content.created_at.desc())
        .limit(limit)
        .all()
    )

    if not news_ids:
        return 0

    db.bulk_save_objects(
        [
            ContentStatusEntry(
                user_id=user_id,
                content_id=content_id,
                status="inbox",
            )
            for (content_id,) in news_ids
        ]
    )
    db.flush()
    return len(news_ids)


def _seed_selected_feed_content_for_user(
    db: Session,
    user_id: int,
    selections: list[OnboardingSelectedSource],
    limit: int = FEED_CONTENT_SEED_LIMIT,
) -> list[int]:
    """Seed selected feed content and return ids for image-task projection."""
    if user_id <= 0 or limit <= 0 or not selections:
        return []

    feed_urls = list(
        {
            selection.feed_url.strip()
            for selection in selections
            if selection.feed_url and selection.feed_url.strip()
        }
    )
    if not feed_urls:
        return []

    existing = select(ContentStatusEntry.content_id).where(
        ContentStatusEntry.user_id == user_id,
    )

    content_ids = (
        db.query(Content.id)
        .filter(
            Content.content_metadata["feed_url"].as_string().in_(feed_urls),
            Content.status == ContentStatus.COMPLETED.value,
            Content.content_type.in_([ContentType.ARTICLE.value, ContentType.PODCAST.value]),
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .filter(~Content.id.in_(existing))
        .order_by(Content.created_at.desc())
        .limit(limit)
        .all()
    )

    if not content_ids:
        return []

    seeded_content_ids = [int(content_id) for (content_id,) in content_ids]

    db.bulk_save_objects(
        [
            ContentStatusEntry(
                user_id=user_id,
                content_id=content_id,
                status="inbox",
            )
            for (content_id,) in content_ids
        ]
    )
    db.flush()
    return seeded_content_ids


def _get_tutorial_flag(db: Session, user_id: int) -> bool:
    from app.models.db.users import User

    user = db.query(User).filter(User.id == user_id).first()
    return bool(user and user.has_completed_new_user_tutorial)


def _serialize_lane_status(lane: OnboardingDiscoveryLane) -> OnboardingDiscoveryLaneStatus:
    return OnboardingDiscoveryLaneStatus(
        name=lane.lane_name or "",
        status=lane.status or "queued",
        completed_queries=lane.completed_queries or 0,
        query_count=lane.query_count or 0,
    )


def _persist_onboarding_suggestions(
    db: Session,
    run: OnboardingDiscoveryRun,
    suggestions: OnboardingFastDiscoverResponse,
) -> None:
    db.query(OnboardingDiscoverySuggestion).filter(
        OnboardingDiscoverySuggestion.run_id == run.id
    ).delete(synchronize_session=False)

    seen: set[str] = set()
    candidates = (
        suggestions.recommended_substacks
        + suggestions.recommended_pods
        + suggestions.recommended_subreddits
    )
    for item in candidates:
        key = _suggestion_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        if item.suggestion_type == "reddit" and not item.subreddit:
            continue
        if item.suggestion_type != "reddit" and not item.feed_url:
            continue
        if not item.rationale or not item.rationale.strip():
            item.rationale = _default_rationale(
                item,
                profile_summary=run.topic_summary,
                inferred_topics=list(run.inferred_topics or []),
            )
        db.add(
            OnboardingDiscoverySuggestion(
                run_id=run.id,
                user_id=run.user_id,
                suggestion_type=item.suggestion_type,
                site_url=item.site_url,
                feed_url=item.feed_url,
                subreddit=item.subreddit,
                title=item.title,
                rationale=item.rationale,
                score=cast(Any, _normalize_score(item.score)),
                status="new",
            )
        )
    db.commit()


def _load_onboarding_suggestions(db: Session, run_id: int) -> OnboardingFastDiscoverResponse:
    suggestions = (
        db.query(OnboardingDiscoverySuggestion)
        .filter(
            OnboardingDiscoverySuggestion.run_id == run_id,
            OnboardingDiscoverySuggestion.status == "new",
        )
        .order_by(func.coalesce(OnboardingDiscoverySuggestion.score, 0).desc())
        .all()
    )

    feeds: list[OnboardingSuggestion] = []
    podcasts: list[OnboardingSuggestion] = []
    subreddits: list[OnboardingSuggestion] = []

    for suggestion in suggestions:
        suggestion_type = _normalize_suggestion_type(suggestion.suggestion_type)
        if suggestion_type is None:
            continue
        item = OnboardingSuggestion(
            suggestion_type=OnboardingSuggestionType(suggestion_type),
            title=suggestion.title,
            site_url=suggestion.site_url,
            feed_url=suggestion.feed_url,
            subreddit=suggestion.subreddit,
            rationale=suggestion.rationale,
            score=_normalize_score(suggestion.score),
            is_default=False,
        )
        if suggestion_type == "podcast_rss":
            podcasts.append(item)
        elif suggestion_type == "reddit":
            subreddits.append(item)
        else:
            feeds.append(item)

    return OnboardingFastDiscoverResponse(
        recommended_pods=podcasts,
        recommended_substacks=feeds,
        recommended_subreddits=subreddits,
    )


def _persist_discovery_run(
    db: Session, user_id: int, suggestions: OnboardingFastDiscoverResponse
) -> int | None:
    run = FeedDiscoveryRun(
        user_id=user_id,
        status="completed",
        direction_summary="onboarding_enrich",
        seed_content_ids=[],
    )
    db.add(run)
    db.flush()

    persisted = 0
    candidate_feed_urls = [
        suggestion.feed_url.strip()
        for suggestion in suggestions.recommended_substacks + suggestions.recommended_pods
        if suggestion.feed_url and suggestion.feed_url.strip()
    ]
    existing_feed_urls: set[str] = set()
    if candidate_feed_urls:
        existing_feed_urls = {
            row[0]
            for row in db.query(FeedDiscoverySuggestion.feed_url)
            .filter(
                FeedDiscoverySuggestion.user_id == user_id,
                FeedDiscoverySuggestion.feed_url.in_(candidate_feed_urls),
            )
            .all()
        }
    pending_feed_urls = set(existing_feed_urls)
    for suggestion in suggestions.recommended_substacks + suggestions.recommended_pods:
        feed_url = (suggestion.feed_url or "").strip()
        if not feed_url:
            continue
        if feed_url in pending_feed_urls:
            continue

        pending_feed_urls.add(feed_url)

        try:
            with db.begin_nested():
                db.add(
                    FeedDiscoverySuggestion(
                        run_id=run.id,
                        user_id=user_id,
                        suggestion_type=suggestion.suggestion_type,
                        site_url=suggestion.site_url,
                        feed_url=feed_url,
                        title=suggestion.title,
                        rationale=suggestion.rationale,
                        score=cast(Any, _normalize_score(suggestion.score)),
                        status="new",
                        config={"feed_url": feed_url},
                    )
                )
                db.flush()
            persisted += 1
        except IntegrityError:
            # Keep onboarding discovery idempotent if another worker/run already inserted this feed.
            pending_feed_urls.discard(feed_url)
            logger.warning(
                "Skipping duplicate discovery suggestion during persistence",
                extra={
                    "component": "onboarding",
                    "operation": "persist_discovery_run",
                    "item_id": str(user_id),
                    "context_data": {"feed_url": feed_url},
                },
            )
            continue

    if not persisted:
        db.rollback()
        return None

    db.commit()
    return run.id


__all__: list[str] = []
