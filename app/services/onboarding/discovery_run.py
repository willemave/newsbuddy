"""Fast and background discovery execution for onboarding."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.onboarding import OnboardingFastDiscoverRequest, OnboardingFastDiscoverResponse
from app.models.db import OnboardingDiscoveryLane, OnboardingDiscoveryRun
from app.services.onboarding.config import (
    ENRICH_EXA_RESULTS,
    ENRICH_MAX_QUERIES,
    ENRICH_TIMEOUT_SECONDS,
    FAST_DISCOVER_EXA_RESULTS,
    FAST_DISCOVER_TIMEOUT_SECONDS,
)
from app.services.onboarding.internal_models import _DiscoveryWebResult
from app.services.onboarding.llm_plans import (
    _format_discovery_prompt,
    _run_discover_output_with_fallback,
)
from app.services.onboarding.persistence import (
    _persist_discovery_run,
    _persist_onboarding_suggestions,
)
from app.services.onboarding.query_heuristics import (
    _build_discovery_queries,
    _normalize_lane_target,
    _select_prompt_results,
)
from app.services.onboarding.search import _run_discovery_exa_queries
from app.services.onboarding.suggestion_projection import _build_discovery_response

logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def fast_discover(request: OnboardingFastDiscoverRequest) -> OnboardingFastDiscoverResponse:
    """Run fast discovery to return onboarding suggestions.

    Args:
        request: OnboardingFastDiscoverRequest payload.

    Returns:
        OnboardingFastDiscoverResponse with grouped recommendations.
    """
    queries = _build_discovery_queries(request)
    results = _run_discovery_exa_queries(queries, num_results=FAST_DISCOVER_EXA_RESULTS)
    prompt_results = _select_prompt_results(results)

    if not prompt_results:
        return OnboardingFastDiscoverResponse()

    try:
        prompt = _format_discovery_prompt(request, prompt_results)
        output = _run_discover_output_with_fallback(
            prompt=prompt,
            timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
            operation="fast_discover",
        )
        return _build_discovery_response(
            output,
            profile_summary=request.profile_summary,
            inferred_topics=request.inferred_topics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Fast onboarding discovery failed",
            extra={
                "component": "onboarding",
                "operation": "fast_discover",
                "context_data": {"error": str(exc)},
            },
        )
        return OnboardingFastDiscoverResponse()


def run_discover_enrich(
    db: Session,
    user_id: int,
    profile_summary: str,
    inferred_topics: list[str] | None,
) -> int | None:
    """Run async enrich discovery and persist suggestions.

    Args:
        db: Database session.
        user_id: Current user id.
        profile_summary: Profile summary for queries.
        inferred_topics: Optional topic list.

    Returns:
        Discovery run id if created, otherwise None.
    """
    if not profile_summary:
        return None

    try:
        topics = list(inferred_topics or [])[:12]
        request = OnboardingFastDiscoverRequest(
            profile_summary=profile_summary,
            inferred_topics=topics,
        )
    except Exception:  # noqa: BLE001
        return None
    queries = _build_discovery_queries(request, max_queries=ENRICH_MAX_QUERIES)
    results = _run_discovery_exa_queries(
        queries,
        num_results=ENRICH_EXA_RESULTS,
        telemetry={
            "feature": "onboarding",
            "operation": "onboarding.discover_enrich.search",
            "user_id": user_id,
        },
    )
    prompt_results = _select_prompt_results(results)
    if not prompt_results:
        return None

    try:
        prompt = _format_discovery_prompt(request, prompt_results)
        output = _run_discover_output_with_fallback(
            prompt=prompt,
            timeout_seconds=ENRICH_TIMEOUT_SECONDS,
            operation="discover_enrich",
            item_id=str(user_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding discover enrich failed",
            extra={
                "component": "onboarding",
                "operation": "discover_enrich",
                "item_id": str(user_id),
                "context_data": {"error": str(exc)},
            },
        )
        return None

    suggestions = _build_discovery_response(
        output,
        profile_summary=request.profile_summary,
        inferred_topics=request.inferred_topics,
    )
    return _persist_discovery_run(db, user_id, suggestions)


def run_audio_discovery(db: Session, run_id: int) -> None:
    """Run onboarding audio discovery lanes and persist suggestions.

    Args:
        db: Database session.
        run_id: Onboarding discovery run id.
    """
    started_at = time.perf_counter()
    run = db.query(OnboardingDiscoveryRun).filter(OnboardingDiscoveryRun.id == run_id).first()
    if not run:
        raise ValueError("Discovery run not found")
    if run.status == "completed":
        logger.info(
            "Onboarding audio discovery skipped",
            extra={
                "component": "onboarding",
                "operation": "audio_discover",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": str(run_id),
                "user_id": run.user_id,
                "context_data": {"reason": "already_completed"},
            },
        )
        return

    try:
        logger.info(
            "Onboarding audio discovery started",
            extra={
                "component": "onboarding",
                "operation": "audio_discover",
                "status": "started",
                "item_id": str(run_id),
                "user_id": run.user_id,
            },
        )
        run.status = "processing"
        db.commit()

        lanes = (
            db.query(OnboardingDiscoveryLane)
            .filter(OnboardingDiscoveryLane.run_id == run.id)
            .order_by(OnboardingDiscoveryLane.id.asc())
            .all()
        )

        results: list[_DiscoveryWebResult] = []
        for lane in lanes:
            lane_started_at = time.perf_counter()
            lane.status = "processing"
            lane.completed_queries = 0
            lane.query_count = len(lane.queries or [])
            db.commit()
            logger.info(
                "Onboarding audio discovery lane started",
                extra={
                    "component": "onboarding",
                    "operation": "audio_discover_lane",
                    "status": "started",
                    "item_id": str(run_id),
                    "user_id": run.user_id,
                    "context_data": {
                        "lane_id": lane.id,
                        "lane_name": lane.lane_name,
                        "lane_target": lane.target,
                        "query_count": lane.query_count,
                    },
                },
            )

            for idx, query in enumerate(lane.queries or []):
                query_started_at = time.perf_counter()
                query_results = _run_discovery_exa_queries(
                    [query],
                    num_results=FAST_DISCOVER_EXA_RESULTS,
                    include_social=(lane.target == "reddit"),
                    lane_name=lane.lane_name,
                    lane_target=_normalize_lane_target(lane.target),
                    telemetry={
                        "feature": "onboarding",
                        "operation": "onboarding.audio_discovery.search",
                        "user_id": run.user_id,
                        "metadata": {"lane_name": lane.lane_name, "lane_target": lane.target},
                    },
                )
                results.extend(query_results)
                lane.completed_queries = idx + 1
                db.commit()
                logger.info(
                    "Onboarding audio discovery query completed",
                    extra={
                        "component": "onboarding",
                        "operation": "audio_discover_query",
                        "status": "completed",
                        "duration_ms": _duration_ms(query_started_at),
                        "item_id": str(run_id),
                        "user_id": run.user_id,
                        "context_data": {
                            "lane_id": lane.id,
                            "lane_name": lane.lane_name,
                            "query_index": idx + 1,
                            "query_count": lane.query_count,
                            "result_count": len(query_results),
                        },
                    },
                )

            lane.status = "completed"
            db.commit()
            logger.info(
                "Onboarding audio discovery lane completed",
                extra={
                    "component": "onboarding",
                    "operation": "audio_discover_lane",
                    "status": "completed",
                    "duration_ms": _duration_ms(lane_started_at),
                    "item_id": str(run_id),
                    "user_id": run.user_id,
                    "context_data": {
                        "lane_id": lane.id,
                        "lane_name": lane.lane_name,
                        "query_count": lane.query_count,
                        "completed_queries": lane.completed_queries,
                    },
                },
            )

        prompt_results = _select_prompt_results(results, lane_balanced=True)
        if not prompt_results:
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            db.commit()
            logger.info(
                "Onboarding audio discovery completed without suggestions",
                extra={
                    "component": "onboarding",
                    "operation": "audio_discover",
                    "status": "completed",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": str(run_id),
                    "user_id": run.user_id,
                    "context_data": {
                        "lane_count": len(lanes),
                        "search_result_count": len(results),
                        "suggestion_source": "none",
                    },
                },
            )
            return

        request = OnboardingFastDiscoverRequest(
            profile_summary=run.topic_summary or "News interests",
            inferred_topics=list(run.inferred_topics or []),
        )
        prompt = _format_discovery_prompt(request, prompt_results)
        suggestions_started_at = time.perf_counter()
        output = _run_discover_output_with_fallback(
            prompt=prompt,
            timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
            operation="audio_discover_suggestions",
            item_id=str(run_id),
        )
        suggestions_duration_ms = _duration_ms(suggestions_started_at)
        suggestions = _build_discovery_response(
            output,
            profile_summary=request.profile_summary,
            inferred_topics=request.inferred_topics,
        )
        _persist_onboarding_suggestions(db, run, suggestions)
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        db.commit()
        logger.info(
            "Onboarding audio discovery completed",
            extra={
                "component": "onboarding",
                "operation": "audio_discover",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": str(run_id),
                "user_id": run.user_id,
                "context_data": {
                    "lane_count": len(lanes),
                    "search_result_count": len(results),
                    "prompt_result_count": len(prompt_results),
                    "suggestions_duration_ms": suggestions_duration_ms,
                    "suggestion_count": (
                        len(suggestions.recommended_pods)
                        + len(suggestions.recommended_substacks)
                        + len(suggestions.recommended_subreddits)
                    ),
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding audio discovery failed",
            extra={
                "component": "onboarding",
                "operation": "audio_discover",
                "duration_ms": _duration_ms(started_at),
                "item_id": str(run_id),
                "user_id": run.user_id,
                "context_data": {"error": str(exc)},
            },
        )
        run.status = "failed"
        run.error_message = str(exc)
        db.query(OnboardingDiscoveryLane).filter(OnboardingDiscoveryLane.run_id == run.id).update(
            {"status": "failed"}, synchronize_session=False
        )
        db.commit()


__all__ = [
    "fast_discover",
    "run_audio_discovery",
    "run_discover_enrich",
]
