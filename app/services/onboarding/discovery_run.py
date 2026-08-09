"""Fast and background feed discovery for onboarding."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.onboarding import OnboardingFastDiscoverRequest, OnboardingFastDiscoverResponse
from app.services.feed_research_runtime import (
    FeedResearchDeadlineExceeded,
    FeedResearchRuntimeError,
    feed_research_runtime,
)
from app.services.onboarding.config import (
    ENRICH_EXA_RESULTS,
    ENRICH_MAX_QUERIES,
    ENRICH_TIMEOUT_SECONDS,
    FAST_DISCOVER_EXA_RESULTS,
    FAST_DISCOVER_TIMEOUT_SECONDS,
)
from app.services.onboarding.discovery_types import OnboardingDiscoveryExecutionResult
from app.services.onboarding.llm_plans import (
    _format_discovery_prompt,
    _run_discover_output_with_fallback,
)
from app.services.onboarding.persistence import _persist_discovery_run
from app.services.onboarding.query_heuristics import (
    _build_discovery_queries,
    _select_prompt_results,
)
from app.services.onboarding.search import _run_discovery_exa_queries
from app.services.onboarding.suggestion_projection import _build_discovery_response

logger = get_logger(__name__)


def fast_discover(
    request: OnboardingFastDiscoverRequest,
    *,
    user_id: int,
) -> OnboardingFastDiscoverResponse:
    """Return fast onboarding suggestions from the user's profile."""
    queries = _build_discovery_queries(request)
    results = _run_discovery_exa_queries(
        queries,
        num_results=FAST_DISCOVER_EXA_RESULTS,
        request_timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
    )
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
        with feed_research_runtime(user_id=user_id, use_llm=False) as runtime:
            return _build_discovery_response(
                output,
                profile_summary=request.profile_summary,
                inferred_topics=request.inferred_topics,
                detector=runtime.detector,
            )
    except (FeedResearchRuntimeError, FeedResearchDeadlineExceeded):
        logger.exception(
            "Fast onboarding feed validation is unavailable",
            extra={
                "component": "onboarding",
                "operation": "fast_discover",
            },
        )
        raise
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
) -> OnboardingDiscoveryExecutionResult:
    """Run asynchronous profile discovery and persist its suggestions."""
    if not profile_summary:
        return OnboardingDiscoveryExecutionResult(success=True)

    try:
        request = OnboardingFastDiscoverRequest(
            profile_summary=profile_summary,
            inferred_topics=list(inferred_topics or [])[:12],
        )
    except Exception as exc:  # noqa: BLE001
        return OnboardingDiscoveryExecutionResult(success=False, error_message=str(exc))

    queries = _build_discovery_queries(request, max_queries=ENRICH_MAX_QUERIES)
    results = _run_discovery_exa_queries(
        queries,
        num_results=ENRICH_EXA_RESULTS,
        telemetry={
            "feature": "onboarding",
            "operation": "onboarding.discover_enrich.search",
            "user_id": user_id,
        },
        request_timeout_seconds=ENRICH_TIMEOUT_SECONDS,
    )
    prompt_results = _select_prompt_results(results)
    if not prompt_results:
        return OnboardingDiscoveryExecutionResult(success=True)

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
        return OnboardingDiscoveryExecutionResult(success=False, error_message=str(exc))

    try:
        with feed_research_runtime(user_id=user_id, use_llm=False) as runtime:
            suggestions = _build_discovery_response(
                output,
                profile_summary=request.profile_summary,
                inferred_topics=request.inferred_topics,
                detector=runtime.detector,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding discover feed validation failed",
            extra={
                "component": "onboarding",
                "operation": "discover_enrich_feed_validation",
                "item_id": str(user_id),
                "context_data": {"error": str(exc)},
            },
        )
        return OnboardingDiscoveryExecutionResult(success=False, error_message=str(exc))

    run_id = _persist_discovery_run(db, user_id, suggestions)
    return OnboardingDiscoveryExecutionResult(success=True, run_id=run_id)


__all__ = [
    "fast_discover",
    "run_discover_enrich",
]
