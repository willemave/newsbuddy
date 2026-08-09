"""Execute persisted onboarding audio-discovery lanes."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.onboarding import OnboardingFastDiscoverRequest
from app.models.db import OnboardingDiscoveryLane, OnboardingDiscoveryRun
from app.services.feed_research_runtime import feed_research_runtime
from app.services.onboarding.config import FAST_DISCOVER_EXA_RESULTS, FAST_DISCOVER_TIMEOUT_SECONDS
from app.services.onboarding.discovery_types import OnboardingDiscoveryExecutionResult
from app.services.onboarding.internal_models import _DiscoveryWebResult
from app.services.onboarding.llm_plans import (
    _format_discovery_prompt,
    _run_discover_output_with_fallback,
)
from app.services.onboarding.persistence import _persist_onboarding_suggestions
from app.services.onboarding.query_heuristics import (
    _normalize_lane_target,
    _select_prompt_results,
)
from app.services.onboarding.search import _run_discovery_exa_queries
from app.services.onboarding.suggestion_projection import _build_discovery_response

logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def run_audio_discovery(
    db: Session,
    run_id: int,
    *,
    user_id: int,
) -> OnboardingDiscoveryExecutionResult:
    """Run onboarding audio discovery lanes and persist suggestions."""
    started_at = time.perf_counter()
    run = (
        db.query(OnboardingDiscoveryRun)
        .filter(
            OnboardingDiscoveryRun.id == run_id,
            OnboardingDiscoveryRun.user_id == user_id,
        )
        .first()
    )
    if not run:
        return OnboardingDiscoveryExecutionResult(
            success=False,
            error_message="Discovery run not found",
        )
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
        return OnboardingDiscoveryExecutionResult(success=True)

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
                    request_timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
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
            return OnboardingDiscoveryExecutionResult(success=True)

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
        if run.user_id is None:
            raise ValueError("Onboarding discovery run is missing a user id")
        with feed_research_runtime(user_id=int(run.user_id), use_llm=False) as runtime:
            suggestions = _build_discovery_response(
                output,
                profile_summary=request.profile_summary,
                inferred_topics=request.inferred_topics,
                detector=runtime.detector,
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
        return OnboardingDiscoveryExecutionResult(success=True)
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
        return OnboardingDiscoveryExecutionResult(success=False, error_message=str(exc))
