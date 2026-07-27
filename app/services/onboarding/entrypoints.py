"""Router and admin entrypoints for onboarding workflows."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.constants import (
    DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    DEFAULT_NEW_FEED_LIMIT,
    SUPPORTED_AGGREGATOR_KEYS,
)
from app.core.logging import get_logger
from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverResponse,
)
from app.models.contracts import ReadingExperience
from app.models.db import OnboardingDiscoveryLane, OnboardingDiscoveryRun
from app.models.db.users import User
from app.models.internal.feed_backfill import FeedBatchBackfillRequest
from app.models.internal.scraper_configs import CreateUserScraperConfig
from app.services.briefing.first_run import complete_first_edition, start_first_edition
from app.services.briefing.refresh import enqueue_briefing_refresh_task
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.onboarding.config import FEED_SUGGESTION_TYPES
from app.services.onboarding.discovery_run import fast_discover
from app.services.onboarding.llm_plans import (
    _build_audio_lane_plan,
    build_onboarding_profile,
    parse_onboarding_voice,
    preview_audio_lane_plan,
)
from app.services.onboarding.persistence import (
    _create_aggregator_configs,
    _create_reddit_configs,
    _estimate_inbox_count,
    _get_tutorial_flag,
    _load_onboarding_suggestions,
    _persist_scraper_config_idempotent,
    _require_run_id,
    _require_run_status,
    _resolve_scraper_sources,
    _seed_recent_news_for_user,
    _seed_selected_feed_content_for_user,
    _serialize_lane_status,
)
from app.services.queue import TaskType
from app.services.x_integration import normalize_twitter_username

logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


async def start_audio_discovery(
    db: Session, user_id: int, request: OnboardingAudioDiscoverRequest
) -> OnboardingAudioDiscoverResponse:
    """Start onboarding discovery from an audio transcript.

    Args:
        db: Database session.
        user_id: Current user id.
        request: OnboardingAudioDiscoverRequest payload.

    Returns:
        OnboardingAudioDiscoverResponse with run and lane status.
    """
    started_at = time.perf_counter()
    transcript = request.transcript.strip()
    if not transcript:
        raise ValueError("Transcript is required")

    logger.info(
        "Onboarding audio discovery start requested",
        extra={
            "component": "onboarding",
            "operation": "audio_discover_start",
            "status": "started",
            "user_id": user_id,
            "context_data": {
                "locale": request.locale,
                "transcript_chars": len(transcript),
            },
        },
    )
    plan_started_at = time.perf_counter()
    plan = await _build_audio_lane_plan(transcript, request.locale)
    plan_duration_ms = _duration_ms(plan_started_at)

    run = OnboardingDiscoveryRun(
        user_id=user_id,
        status="pending",
        topic_summary=plan.topic_summary,
        inferred_topics=plan.inferred_topics,
    )
    db.add(run)
    db.flush()

    lanes: list[OnboardingDiscoveryLane] = []
    for lane in plan.lanes:
        lane_row = OnboardingDiscoveryLane(
            run_id=run.id,
            lane_name=lane.name,
            goal=lane.goal,
            target=lane.target,
            status="queued",
            query_count=len(lane.queries),
            completed_queries=0,
            queries=lane.queries,
        )
        db.add(lane_row)
        lanes.append(lane_row)

    db.commit()
    run_id = _require_run_id(run)
    run_status = _require_run_status(run)

    queue_gateway = get_task_queue_gateway()
    enqueue_started_at = time.perf_counter()
    task_id = queue_gateway.enqueue(
        TaskType.ONBOARDING_DISCOVER,
        payload={"user_id": user_id, "run_id": run_id},
    )
    logger.info(
        "Onboarding audio discovery queued",
        extra={
            "component": "onboarding",
            "operation": "audio_discover_start",
            "status": "queued",
            "duration_ms": _duration_ms(started_at),
            "task_id": task_id,
            "task_type": TaskType.ONBOARDING_DISCOVER.value,
            "user_id": user_id,
            "context_data": {
                "run_id": run_id,
                "lane_count": len(lanes),
                "inferred_topic_count": len(plan.inferred_topics),
                "plan_duration_ms": plan_duration_ms,
                "enqueue_duration_ms": _duration_ms(enqueue_started_at),
            },
        },
    )

    return OnboardingAudioDiscoverResponse(
        run_id=run_id,
        run_status=run_status,
        topic_summary=run.topic_summary,
        inferred_topics=list(run.inferred_topics or []),
        lanes=[_serialize_lane_status(lane) for lane in lanes],
    )


def get_onboarding_discovery_status(
    db: Session, user_id: int, run_id: int
) -> OnboardingDiscoveryStatusResponse:
    """Return the latest onboarding discovery status for a run.

    Args:
        db: Database session.
        user_id: Current user id.
        run_id: Discovery run id.

    Returns:
        OnboardingDiscoveryStatusResponse with lane status and suggestions when ready.
    """
    run = (
        db.query(OnboardingDiscoveryRun)
        .filter(OnboardingDiscoveryRun.id == run_id, OnboardingDiscoveryRun.user_id == user_id)
        .first()
    )
    if not run:
        raise ValueError("Discovery run not found")
    run_id_value = _require_run_id(run)
    run_status = _require_run_status(run)

    lanes = (
        db.query(OnboardingDiscoveryLane)
        .filter(OnboardingDiscoveryLane.run_id == run_id_value)
        .order_by(OnboardingDiscoveryLane.id.asc())
        .all()
    )

    suggestions: OnboardingFastDiscoverResponse | None = None
    if run_status == "completed":
        suggestions = _load_onboarding_suggestions(db, run_id_value)

    return OnboardingDiscoveryStatusResponse(
        run_id=run_id_value,
        run_status=run_status,
        topic_summary=run.topic_summary,
        inferred_topics=list(run.inferred_topics or []),
        lanes=[_serialize_lane_status(lane) for lane in lanes],
        suggestions=suggestions,
        error_message=run.error_message,
    )


def complete_onboarding(
    db: Session, user_id: int, request: OnboardingCompleteRequest
) -> OnboardingCompleteResponse:
    """Finalize onboarding selections, create scraper configs, and queue crawlers.

    Args:
        db: Database session.
        user_id: Current user id.
        request: OnboardingCompleteRequest payload.

    Returns:
        OnboardingCompleteResponse with status and inbox count.
    """
    normalized_username: str | None = None
    configured_source_count = 0
    feed_config_ids_for_backfill: list[int] = []
    should_update_twitter_username = request.twitter_username is not None
    if should_update_twitter_username:
        normalized_username = normalize_twitter_username(request.twitter_username)

    created_types: set[str] = set()
    selections = request.selected_sources

    for selection in selections:
        config_payload = {**(selection.config or {})}
        if not config_payload.get("feed_url"):
            config_payload["feed_url"] = selection.feed_url
        if "limit" not in config_payload:
            config_payload["limit"] = DEFAULT_NEW_FEED_LIMIT
        create_data = CreateUserScraperConfig(
            scraper_type=selection.suggestion_type.value,
            display_name=selection.title,
            config=config_payload,
        )
        config = _persist_scraper_config_idempotent(
            db,
            user_id=user_id,
            data=create_data,
            operation="create_scraper_config",
            log_context={"feed_url": create_data.config.get("feed_url")},
        )
        if config is None:
            continue
        created_types.add(selection.suggestion_type)
        configured_source_count += 1
        if selection.suggestion_type in FEED_SUGGESTION_TYPES and config.id is not None:
            feed_config_ids_for_backfill.append(config.id)

    if request.selected_subreddits:
        created_types.add("reddit")
        configured_source_count += _create_reddit_configs(db, user_id, request.selected_subreddits)

    if request.selected_aggregators:
        configured_source_count += _create_aggregator_configs(
            db, user_id, request.selected_aggregators
        )

    unique_feed_config_ids = list(dict.fromkeys(feed_config_ids_for_backfill))
    sources_to_scrape = _resolve_scraper_sources(created_types - FEED_SUGGESTION_TYPES)
    for aggregator_selection in request.selected_aggregators:
        source = aggregator_selection.key.strip().lower()
        if source in SUPPORTED_AGGREGATOR_KEYS and source not in sources_to_scrape:
            sources_to_scrape.append(source)
    discovery_payload: dict[str, Any] | None = None
    if request.profile_summary:
        discovery_payload = {
            "user_id": user_id,
            "profile_summary": request.profile_summary,
            "inferred_topics": request.inferred_topics or [],
        }

    try:
        _seed_recent_news_for_user(db, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to seed onboarding news",
            extra={
                "component": "onboarding",
                "operation": "seed_news",
                "item_id": str(user_id),
                "context_data": {"error": str(exc)},
            },
        )

    try:
        _seed_selected_feed_content_for_user(db, user_id, selections)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to seed feed content for onboarding",
            extra={
                "component": "onboarding",
                "operation": "seed_feed_content",
                "item_id": str(user_id),
                "context_data": {"error": str(exc)},
            },
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise ValueError("User not found")
    if should_update_twitter_username and user.twitter_username != normalized_username:
        user.twitter_username = normalized_username
    user.has_completed_onboarding = True
    user.reading_experience = ReadingExperience.BRIEFING.value
    first_edition_run = start_first_edition(db, user_id=user_id)
    if first_edition_run.id is None:
        raise RuntimeError("First-edition run was not persisted")
    first_edition_run_id = first_edition_run.id
    enqueue_briefing_refresh_task(db, user_id=user_id, mode="append", delay_seconds=0)
    db.commit()

    queue_gateway = get_task_queue_gateway()
    task_id = None
    if unique_feed_config_ids:
        task_id = queue_gateway.enqueue(
            TaskType.BACKFILL_FEEDS,
            payload=FeedBatchBackfillRequest(
                user_id=user_id,
                config_ids=unique_feed_config_ids,
                count=DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
                first_edition_run_id=first_edition_run_id,
            ).model_dump(exclude_none=True),
            dedupe=True,
        )
    if sources_to_scrape:
        scrape_task_id = queue_gateway.enqueue(
            TaskType.SCRAPE,
            payload={
                "sources": sources_to_scrape,
                "first_edition_run_id": first_edition_run_id,
            },
        )
        if task_id is None:
            task_id = scrape_task_id

    if discovery_payload is not None:
        queue_gateway.enqueue(
            TaskType.ONBOARDING_DISCOVER,
            payload=discovery_payload,
        )

    inbox_count = _estimate_inbox_count(db, user_id)
    inbox_count_estimate = max(inbox_count, 100)

    return OnboardingCompleteResponse(
        status="queued",
        task_id=task_id,
        inbox_count_estimate=inbox_count_estimate,
        configured_source_count=configured_source_count,
        longform_status="loading",
        has_completed_onboarding=True,
        has_completed_new_user_tutorial=_get_tutorial_flag(db, user_id),
    )


def mark_tutorial_complete(db: Session, user_id: int) -> bool:
    """Mark the onboarding tutorial as completed for a user.

    Args:
        db: Database session.
        user_id: Current user id.

    Returns:
        Updated completion flag.
    """
    from app.models.db.users import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.has_completed_new_user_tutorial = True
    complete_first_edition(db, user_id=user_id)
    db.commit()
    return True


__all__ = [
    "build_onboarding_profile",
    "complete_onboarding",
    "fast_discover",
    "get_onboarding_discovery_status",
    "mark_tutorial_complete",
    "parse_onboarding_voice",
    "preview_audio_lane_plan",
    "start_audio_discovery",
]
