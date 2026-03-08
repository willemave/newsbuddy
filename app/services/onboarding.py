"""Service helpers for agentic onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.core.logging import get_logger
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentStatusEntry,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    OnboardingDiscoverySuggestion,
)
from app.models.user import User
from app.repositories.content_repository import apply_visibility_filters, build_visibility_context
from app.routers.api.models import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse,
    OnboardingAudioLanePreview,
    OnboardingAudioLanePreviewResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    OnboardingDiscoveryLaneStatus,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverRequest,
    OnboardingFastDiscoverResponse,
    OnboardingProfileRequest,
    OnboardingProfileResponse,
    OnboardingSelectedSource,
    OnboardingSuggestion,
    OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
)
from app.scraping.atom_unified import load_atom_feeds
from app.scraping.substack_unified import load_substack_feeds
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.llm_agents import get_basic_agent
from app.services.long_form_images import enqueue_visible_long_form_images_for_content_ids
from app.services.queue import TaskType
from app.services.scraper_configs import CreateUserScraperConfig, create_user_scraper_config
from app.services.x_integration import normalize_twitter_username
from app.utils.paths import resolve_config_path

logger = get_logger(__name__)

ONBOARDING_PRIMARY_MODEL = "cerebras:zai-glm-4.7"
PROFILE_MODEL = ONBOARDING_PRIMARY_MODEL
FAST_DISCOVER_MODEL = ONBOARDING_PRIMARY_MODEL
VOICE_PARSE_MODEL = ONBOARDING_PRIMARY_MODEL
AUDIO_PLAN_MODEL = ONBOARDING_PRIMARY_MODEL
DISCOVERY_FALLBACK_MODELS = (
    "google-gla:gemini-2.5-flash",
    "openai:gpt-5-mini",
)
AUDIO_PLAN_FALLBACK_MODELS = (
    "google-gla:gemini-2.5-flash",
    "openai:gpt-5-mini",
)

PROFILE_TIMEOUT_SECONDS = 8
FAST_DISCOVER_TIMEOUT_SECONDS = 12
VOICE_PARSE_TIMEOUT_SECONDS = 6
AUDIO_PLAN_TIMEOUT_SECONDS = 8
ENRICH_TIMEOUT_SECONDS = 25

FAST_DISCOVER_MAX_QUERIES = 6
PROFILE_EXA_RESULTS = 3
FAST_DISCOVER_EXA_RESULTS = 12
ENRICH_MAX_QUERIES = 10
ENRICH_EXA_RESULTS = 12
DISCOVERY_PROMPT_MAX_WEB_RESULTS = 200
DISCOVERY_PROMPT_SNIPPET_CHARS = 280
DISCOVERY_PROMPT_MAX_FILL_IN_FEEDS = 8
DISCOVERY_PROMPT_MAX_FILL_IN_PODCASTS = 6
DISCOVERY_PROMPT_MAX_FILL_IN_REDDIT = 8
ONBOARDING_FEED_SUGGESTION_LIMIT = 10
EXA_DISCOVERY_MAX_WORKERS = 8

DEFAULT_SOURCE_LIMITS = {
    "substack": 8,
    "podcast_rss": 6,
    "atom": 6,
    "reddit": 8,
}
NEWS_SEED_LIMIT = 100
FEED_CONTENT_SEED_LIMIT = 30

SCRAPER_SOURCE_BY_TYPE = {
    "substack": "Substack",
    "podcast_rss": "Podcast",
    "atom": "Atom",
    "reddit": "Reddit",
}

PROFILE_SYSTEM_PROMPT = (
    "You are building a short onboarding profile for a user. "
    "Use the provided interests and web snippets to infer a concise profile summary "
    "and 3-6 topical interests. "
    "Do not invent interests that contradict the user-provided topics. "
    "Return structured output only."
)

FAST_DISCOVER_SYSTEM_PROMPT = (
    "You are selecting high-quality sources for a new user. "
    "Use the profile summary, topics, search snippets, and curated fill-in candidates "
    "to suggest Substack/Atom feeds, podcast RSS feeds, and relevant subreddits. "
    "Prioritize sources grounded in web_results first; "
    "use curated_fill_ins as backups when needed. "
    "Every suggestion must include a concise, specific rationale sentence. "
    "Prefer sources with clear RSS URLs when possible. "
    "For feed-like sources, always provide a best-effort feed_url when available. "
    "If uncertain, include candidate_feed_url and set is_likely_feed plus feed_confidence (0-1). "
    "For reddit entries, include subreddit. "
    "Return structured output only."
)

VOICE_PARSE_SYSTEM_PROMPT = (
    "You extract onboarding fields from a transcript. "
    "Return a first name if explicitly stated and a concise list of interest topics. "
    "Do not guess missing information. "
    "Return structured output only."
)

AUDIO_PLAN_SYSTEM_PROMPT = (
    "You design onboarding discovery lanes based on a user's spoken interests. "
    "Return a concise topic_summary, 3-6 inferred_topics, and 3-5 lanes. "
    "Each lane must include name, goal, target (feeds, podcasts, reddit), "
    "and 2-4 web search queries. Queries must be varied and specific: each query should be "
    "a compact search phrase (5-10 words) with concrete keywords tied to the lane goal, "
    "and avoid repeating the same wording pattern. "
    "Include at least one reddit lane. "
    "Return structured output only."
)

class _ProfileOutput(BaseModel):
    """LLM output for onboarding profile creation."""

    profile_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    candidate_sources: list[str] = Field(default_factory=list)

class _DiscoverSuggestion(BaseModel):
    """LLM output suggestion seed."""

    title: str | None = None
    site_url: str | None = None
    feed_url: str | None = None
    candidate_feed_url: str | None = None
    is_likely_feed: bool | None = None
    feed_confidence: float | None = Field(default=None, ge=0, le=1)
    subreddit: str | None = None
    rationale: str | None = None
    score: float | None = None


class _DiscoverOutput(BaseModel):
    """LLM output for onboarding discovery."""

    substacks: list[_DiscoverSuggestion] = Field(default_factory=list)
    podcasts: list[_DiscoverSuggestion] = Field(default_factory=list)
    subreddits: list[_DiscoverSuggestion] = Field(default_factory=list)


class _DiscoveryWebResult(BaseModel):
    """Web result used for onboarding discovery prompting."""

    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None
    query: str | None = None
    lane_name: str | None = None
    lane_target: Literal["feeds", "podcasts", "reddit"] | None = None


class _VoiceParseOutput(BaseModel):
    """LLM output for onboarding voice parsing."""

    first_name: str | None = None
    interest_topics: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class _AudioLane(BaseModel):
    """LLM output for a single onboarding discovery lane."""

    name: str
    goal: str
    target: Literal["feeds", "podcasts", "reddit"]
    queries: list[str] = Field(default_factory=list)


class _AudioPlanOutput(BaseModel):
    """LLM output for onboarding audio discovery planning."""

    topic_summary: str
    inferred_topics: list[str] = Field(default_factory=list)
    lanes: list[_AudioLane] = Field(default_factory=list)


def build_onboarding_profile(request: OnboardingProfileRequest) -> OnboardingProfileResponse:
    """Build a quick profile from name + interest topics using Exa + LLM.

    Args:
        request: OnboardingProfileRequest payload.

    Returns:
        OnboardingProfileResponse with summary and inferred topics.
    """
    queries = _build_profile_queries(request)
    results = _run_exa_queries(queries, num_results=PROFILE_EXA_RESULTS, include_social=False)

    if not results:
        fallback_summary = _build_profile_fallback_summary(
            request.first_name, request.interest_topics
        )
        return OnboardingProfileResponse(
            profile_summary=fallback_summary,
            inferred_topics=_merge_topics(request.interest_topics),
            candidate_sources=[],
        )

    try:
        prompt = _format_profile_prompt(request, results)
        agent = get_basic_agent(PROFILE_MODEL, _ProfileOutput, PROFILE_SYSTEM_PROMPT)
        result = agent.run_sync(prompt, model_settings={"timeout": PROFILE_TIMEOUT_SECONDS})
        output = _get_agent_output(result)
        merged_topics = _merge_topics(output.inferred_topics, request.interest_topics)
        return OnboardingProfileResponse(
            profile_summary=output.profile_summary,
            inferred_topics=merged_topics,
            candidate_sources=output.candidate_sources,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding profile build failed",
            extra={
                "component": "onboarding",
                "operation": "profile_build",
                "context_data": {"error": str(exc)},
            },
        )
        fallback_summary = _build_profile_fallback_summary(
            request.first_name, request.interest_topics
        )
        return OnboardingProfileResponse(
            profile_summary=fallback_summary,
            inferred_topics=_merge_topics(request.interest_topics),
            candidate_sources=[],
        )


def parse_onboarding_voice(request: OnboardingVoiceParseRequest) -> OnboardingVoiceParseResponse:
    """Parse a voice transcript into onboarding fields.

    Args:
        request: OnboardingVoiceParseRequest payload.

    Returns:
        OnboardingVoiceParseResponse with extracted fields.
    """
    transcript = request.transcript.strip()
    if not transcript:
        return OnboardingVoiceParseResponse(
            first_name=None,
            interest_topics=[],
            confidence=0,
            missing_fields=["first_name", "interest_topics"],
        )

    try:
        prompt = _format_voice_parse_prompt(transcript, request.locale)
        agent = get_basic_agent(VOICE_PARSE_MODEL, _VoiceParseOutput, VOICE_PARSE_SYSTEM_PROMPT)
        result = agent.run_sync(prompt, model_settings={"timeout": VOICE_PARSE_TIMEOUT_SECONDS})
        output = _get_agent_output(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding voice parse failed",
            extra={
                "component": "onboarding",
                "operation": "voice_parse",
                "context_data": {"error": str(exc)},
            },
        )
        return OnboardingVoiceParseResponse(
            first_name=None,
            interest_topics=[],
            confidence=0,
            missing_fields=["first_name", "interest_topics"],
        )

    first_name = (output.first_name or "").strip() or None
    topics = _merge_topics(output.interest_topics)
    missing_fields: list[str] = []
    if not first_name:
        missing_fields.append("first_name")
    if not topics:
        missing_fields.append("interest_topics")

    return OnboardingVoiceParseResponse(
        first_name=first_name,
        interest_topics=topics,
        confidence=output.confidence,
        missing_fields=missing_fields,
    )


async def preview_audio_lane_plan(
    request: OnboardingAudioDiscoverRequest,
) -> OnboardingAudioLanePreviewResponse:
    """Preview generated audio discovery lanes for admin debugging.

    Args:
        request: OnboardingAudioDiscoverRequest payload.

    Returns:
        OnboardingAudioLanePreviewResponse with generated lanes and fallback metadata.
    """
    transcript = request.transcript.strip()
    if not transcript:
        raise ValueError("Transcript is required")

    plan, used_fallback, fallback_reason = await _build_audio_lane_plan_with_metadata(
        transcript, request.locale
    )
    return OnboardingAudioLanePreviewResponse(
        topic_summary=plan.topic_summary,
        inferred_topics=plan.inferred_topics,
        lanes=[_serialize_audio_lane_preview(lane) for lane in plan.lanes],
        used_fallback=used_fallback,
        fallback_reason=fallback_reason,
    )


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
    transcript = request.transcript.strip()
    if not transcript:
        raise ValueError("Transcript is required")

    plan = await _build_audio_lane_plan(transcript, request.locale)

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

    queue_gateway = get_task_queue_gateway()
    queue_gateway.enqueue(
        TaskType.ONBOARDING_DISCOVER,
        payload={"user_id": user_id, "run_id": run.id},
    )

    return OnboardingAudioDiscoverResponse(
        run_id=run.id,
        run_status=run.status,
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

    lanes = (
        db.query(OnboardingDiscoveryLane)
        .filter(OnboardingDiscoveryLane.run_id == run.id)
        .order_by(OnboardingDiscoveryLane.id.asc())
        .all()
    )

    suggestions: OnboardingFastDiscoverResponse | None = None
    if run.status == "completed":
        suggestions = _load_onboarding_suggestions(db, run.id)

    return OnboardingDiscoveryStatusResponse(
        run_id=run.id,
        run_status=run.status,
        topic_summary=run.topic_summary,
        inferred_topics=list(run.inferred_topics or []),
        lanes=[_serialize_lane_status(lane) for lane in lanes],
        suggestions=suggestions,
        error_message=run.error_message,
    )


def fast_discover(request: OnboardingFastDiscoverRequest) -> OnboardingFastDiscoverResponse:
    """Run fast discovery to return onboarding suggestions.

    Args:
        request: OnboardingFastDiscoverRequest payload.

    Returns:
        OnboardingFastDiscoverResponse with grouped recommendations.
    """
    curated = _load_curated_defaults()
    queries = _build_discovery_queries(request)
    results = _run_discovery_exa_queries(queries, num_results=FAST_DISCOVER_EXA_RESULTS)
    prompt_results = _select_prompt_results(results)

    if not prompt_results:
        return _fast_discover_from_defaults(
            curated,
            profile_summary=request.profile_summary,
            inferred_topics=request.inferred_topics,
        )

    try:
        prompt = _format_discovery_prompt(
            request, prompt_results, _curated_fill_in_candidates(curated)
        )
        output = _run_discover_output_with_fallback(
            prompt=prompt,
            timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
            operation="fast_discover",
        )
        return _build_discovery_response(
            output,
            curated,
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
        return _fast_discover_from_defaults(
            curated,
            profile_summary=request.profile_summary,
            inferred_topics=request.inferred_topics,
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
    should_update_twitter_username = request.twitter_username is not None
    if should_update_twitter_username:
        normalized_username = normalize_twitter_username(request.twitter_username)

    created_types: set[str] = set()
    selections = request.selected_sources

    if not selections:
        curated = _load_curated_defaults()
        selections = _defaults_to_selected_sources(curated)

    for selection in selections:
        config_payload = {**(selection.config or {})}
        if not config_payload.get("feed_url"):
            config_payload["feed_url"] = selection.feed_url
        if "limit" not in config_payload:
            config_payload["limit"] = DEFAULT_NEW_FEED_LIMIT

        try:
            create_user_scraper_config(
                db,
                user_id=user_id,
                data=CreateUserScraperConfig(
                    scraper_type=selection.suggestion_type,
                    display_name=selection.title,
                    config=config_payload,
                ),
            )
            created_types.add(selection.suggestion_type)
        except ValueError as exc:
            if "already exists" in str(exc):
                created_types.add(selection.suggestion_type)
                continue
            logger.error(
                "Failed to create onboarding scraper config",
                extra={
                    "component": "onboarding",
                    "operation": "create_scraper_config",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected error creating scraper config",
                extra={
                    "component": "onboarding",
                    "operation": "create_scraper_config",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc)},
                },
            )

    if request.selected_subreddits:
        created_types.add("reddit")
        _create_reddit_configs(db, user_id, request.selected_subreddits)

    sources_to_scrape = _resolve_scraper_sources(created_types)
    task_id = None
    queue_gateway = get_task_queue_gateway()
    if sources_to_scrape:
        task_id = queue_gateway.enqueue(
            TaskType.SCRAPE,
            payload={"sources": sources_to_scrape},
        )

    if request.profile_summary:
        queue_gateway.enqueue(
            TaskType.ONBOARDING_DISCOVER,
            payload={
                "user_id": user_id,
                "profile_summary": request.profile_summary,
                "inferred_topics": request.inferred_topics or [],
            },
        )

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
        _seed_default_feed_content_for_user(db, user_id, selections)
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
    if user:
        if should_update_twitter_username and user.twitter_username != normalized_username:
            user.twitter_username = normalized_username
        user.has_completed_onboarding = True
        db.commit()

    inbox_count = _estimate_inbox_count(db, user_id)
    inbox_count_estimate = max(inbox_count, 100)

    return OnboardingCompleteResponse(
        status="queued",
        task_id=task_id,
        inbox_count_estimate=inbox_count_estimate,
        longform_status="loading",
        has_completed_onboarding=True,
        has_completed_new_user_tutorial=_get_tutorial_flag(db, user_id),
    )


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
    curated = _load_curated_defaults()
    queries = _build_discovery_queries(request, max_queries=ENRICH_MAX_QUERIES)
    results = _run_discovery_exa_queries(queries, num_results=ENRICH_EXA_RESULTS)
    prompt_results = _select_prompt_results(results)
    if not prompt_results:
        return None

    try:
        prompt = _format_discovery_prompt(
            request, prompt_results, _curated_fill_in_candidates(curated)
        )
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
        curated,
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
    run = db.query(OnboardingDiscoveryRun).filter(OnboardingDiscoveryRun.id == run_id).first()
    if not run:
        raise ValueError("Discovery run not found")
    if run.status == "completed":
        return

    try:
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
            lane.status = "processing"
            lane.completed_queries = 0
            lane.query_count = len(lane.queries or [])
            db.commit()

            for idx, query in enumerate(lane.queries or []):
                results.extend(
                    _run_discovery_exa_queries(
                        [query],
                        num_results=FAST_DISCOVER_EXA_RESULTS,
                        include_social=(lane.target == "reddit"),
                        lane_name=lane.lane_name,
                        lane_target=lane.target,
                    )
                )
                lane.completed_queries = idx + 1
                db.commit()

            lane.status = "completed"
            db.commit()

        curated = _load_curated_defaults()
        prompt_results = _select_prompt_results(results, lane_balanced=True)
        if not prompt_results:
            suggestions = _fast_discover_from_defaults(
                curated,
                profile_summary=run.topic_summary,
                inferred_topics=list(run.inferred_topics or []),
            )
            _persist_onboarding_suggestions(db, run, suggestions)
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            db.commit()
            return

        request = OnboardingFastDiscoverRequest(
            profile_summary=run.topic_summary or "News interests",
            inferred_topics=list(run.inferred_topics or []),
        )
        prompt = _format_discovery_prompt(
            request, prompt_results, _curated_fill_in_candidates(curated)
        )
        output = _run_discover_output_with_fallback(
            prompt=prompt,
            timeout_seconds=FAST_DISCOVER_TIMEOUT_SECONDS,
            operation="audio_discover_suggestions",
            item_id=str(run_id),
        )
        suggestions = _build_discovery_response(
            output,
            curated,
            profile_summary=request.profile_summary,
            inferred_topics=request.inferred_topics,
        )
        _persist_onboarding_suggestions(db, run, suggestions)
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding audio discovery failed",
            extra={
                "component": "onboarding",
                "operation": "audio_discover",
                "item_id": str(run_id),
                "context_data": {"error": str(exc)},
            },
        )
        run.status = "failed"
        run.error_message = str(exc)
        db.query(OnboardingDiscoveryLane).filter(OnboardingDiscoveryLane.run_id == run.id).update(
            {"status": "failed"}, synchronize_session=False
        )
        db.commit()


def mark_tutorial_complete(db: Session, user_id: int) -> bool:
    """Mark the onboarding tutorial as completed for a user.

    Args:
        db: Database session.
        user_id: Current user id.

    Returns:
        Updated completion flag.
    """
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.has_completed_new_user_tutorial = True
    db.commit()
    return True


def _build_profile_queries(request: OnboardingProfileRequest) -> list[str]:
    topics = _merge_topics(request.interest_topics)
    queries: list[str] = []
    for topic in topics:
        queries.append(f"{topic} newsletter")
        queries.append(f"{topic} podcast")
        queries.append(f"{topic} substack")
        if len(queries) >= 4:
            break
    if not queries:
        queries.append(f"{request.first_name} newsletter")
    return queries[:4]


def _build_discovery_queries(
    request: OnboardingFastDiscoverRequest, max_queries: int = FAST_DISCOVER_MAX_QUERIES
) -> list[str]:
    topics = [topic.strip() for topic in request.inferred_topics if topic.strip()]
    topics = topics[:4] if topics else []

    queries: list[str] = []
    if request.profile_summary:
        queries.append(f"{request.profile_summary} newsletter")

    for topic in topics:
        queries.append(f"{topic} substack")
        queries.append(f"{topic} podcast rss")
        queries.append(f"{topic} best newsletters")
        if len(queries) >= max_queries:
            break

    return queries[:max_queries]


def _run_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
) -> list[ExaSearchResult]:
    results: list[ExaSearchResult] = []
    exclude_domains = [] if include_social else None
    for query in queries:
        results.extend(
            exa_search(
                query,
                num_results=num_results,
                max_characters=1200,
                exclude_domains=exclude_domains,
            )
        )
    return results


def _run_discovery_exa_queries(
    queries: Iterable[str],
    *,
    num_results: int,
    include_social: bool = False,
    lane_name: str | None = None,
    lane_target: Literal["feeds", "podcasts", "reddit"] | None = None,
) -> list[_DiscoveryWebResult]:
    """Run Exa queries and attach onboarding discovery metadata."""
    results: list[_DiscoveryWebResult] = []
    exclude_domains = [] if include_social else None
    cleaned_queries = [
        query.strip()
        for query in queries
        if isinstance(query, str) and query.strip()
    ]
    if not cleaned_queries:
        return results

    max_workers = min(EXA_DISCOVERY_MAX_WORKERS, len(cleaned_queries))

    def _search_query(query: str) -> tuple[str, list[ExaSearchResult]]:
        return (
            query,
            exa_search(
                query,
                num_results=num_results,
                max_characters=1200,
                exclude_domains=exclude_domains,
            ),
        )

    # Preserve query order while still running network-bound Exa calls concurrently.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        query_results = list(executor.map(_search_query, cleaned_queries))

    for query, raw_results in query_results:
        for item in raw_results:
            # Preserve each Exa result and include lane/query context for prompt balancing.
            results.append(
                _DiscoveryWebResult(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    published_date=item.published_date,
                    query=query,
                    lane_name=lane_name,
                    lane_target=lane_target,
                )
            )
    return results


def _select_prompt_results(
    results: list[_DiscoveryWebResult],
    *,
    lane_balanced: bool = False,
) -> list[_DiscoveryWebResult]:
    """Select and deduplicate discovery results for prompt construction."""
    deduped: list[_DiscoveryWebResult] = []
    seen_urls: set[str] = set()
    for result in results:
        url_key = result.url.strip().lower()
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        deduped.append(result)

    if not lane_balanced:
        return deduped[:DISCOVERY_PROMPT_MAX_WEB_RESULTS]

    grouped: dict[str, list[_DiscoveryWebResult]] = {}
    group_order: list[str] = []
    for result in deduped:
        lane_key = result.lane_name or "general"
        if lane_key not in grouped:
            grouped[lane_key] = []
            group_order.append(lane_key)
        grouped[lane_key].append(result)

    selected: list[_DiscoveryWebResult] = []
    indices = {lane_key: 0 for lane_key in group_order}
    while len(selected) < DISCOVERY_PROMPT_MAX_WEB_RESULTS:
        advanced = False
        for lane_key in group_order:
            lane_results = grouped[lane_key]
            lane_index = indices[lane_key]
            if lane_index >= len(lane_results):
                continue
            selected.append(lane_results[lane_index])
            indices[lane_key] = lane_index + 1
            advanced = True
            if len(selected) >= DISCOVERY_PROMPT_MAX_WEB_RESULTS:
                break
        if not advanced:
            break

    return selected


def _prompt_snippet(snippet: str | None) -> str:
    if not snippet:
        return ""
    return snippet.strip().replace("\n", " ")[:DISCOVERY_PROMPT_SNIPPET_CHARS]


def _format_profile_prompt(
    request: OnboardingProfileRequest, results: list[ExaSearchResult]
) -> str:
    lines = [
        f"first_name: {request.first_name}",
        f"interest_topics: {', '.join(request.interest_topics)}",
        "",
        "web_results:",
    ]
    for idx, item in enumerate(results[:10], start=1):
        lines.append(f"{idx}. {item.title}\nurl: {item.url}\nsummary: {item.snippet or ''}")
    return "\n".join(lines)


def _format_voice_parse_prompt(transcript: str, locale: str | None) -> str:
    locale_value = locale or "unknown"
    return (
        "Extract the user's first name (if stated) and the topics of news they want to read. "
        "Return concise topic phrases (2-5 words) and avoid guessing. "
        f"locale: {locale_value}\n"
        f"transcript: {transcript}"
    )


async def _build_audio_lane_plan(transcript: str, locale: str | None) -> _AudioPlanOutput:
    plan, _, _ = await _build_audio_lane_plan_with_metadata(transcript, locale)
    return plan


async def _build_audio_lane_plan_with_metadata(
    transcript: str, locale: str | None
) -> tuple[_AudioPlanOutput, bool, str | None]:
    try:
        prompt = _format_audio_plan_prompt(transcript, locale)
        output = await _run_audio_plan_with_fallback(
            prompt=prompt,
            timeout_seconds=AUDIO_PLAN_TIMEOUT_SECONDS,
        )
        normalized_plan, used_fallback = _normalize_audio_lane_plan_with_metadata(
            output, transcript
        )
        fallback_reason = "Generated lanes were empty or invalid." if used_fallback else None
        return normalized_plan, used_fallback, fallback_reason
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Onboarding audio lane plan failed",
            extra={
                "component": "onboarding",
                "operation": "audio_plan",
                "context_data": {"error": str(exc)},
            },
        )
        return _fallback_audio_lane_plan(transcript), True, str(exc)


def _format_audio_plan_prompt(transcript: str, locale: str | None) -> str:
    locale_value = locale or "unknown"
    return f"locale: {locale_value}\ntranscript: {transcript}"


def _candidate_models(primary: str, fallbacks: tuple[str, ...]) -> list[str]:
    models: list[str] = []
    for model in (primary, *fallbacks):
        if model in models:
            continue
        models.append(model)
    return models


def _run_discover_output_with_fallback(
    *,
    prompt: str,
    timeout_seconds: int,
    operation: str,
    item_id: str | None = None,
) -> _DiscoverOutput:
    last_error: Exception | None = None
    models = _candidate_models(FAST_DISCOVER_MODEL, DISCOVERY_FALLBACK_MODELS)

    for attempt_index, model_spec in enumerate(models, start=1):
        try:
            agent = get_basic_agent(model_spec, _DiscoverOutput, FAST_DISCOVER_SYSTEM_PROMPT)
            result = agent.run_sync(prompt, model_settings={"timeout": timeout_seconds})
            output = _get_agent_output(result)
            if attempt_index > 1:
                logger.warning(
                    "Onboarding discovery succeeded on fallback model",
                    extra={
                        "component": "onboarding",
                        "operation": operation,
                        "item_id": item_id,
                        "context_data": {
                            "model": model_spec,
                            "attempt": attempt_index,
                            "models_tried": models,
                        },
                    },
                )
            return output
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Onboarding discovery model attempt failed",
                extra={
                    "component": "onboarding",
                    "operation": operation,
                    "item_id": item_id,
                    "context_data": {
                        "model": model_spec,
                        "attempt": attempt_index,
                        "models_tried": models,
                        "error": str(exc),
                    },
                },
            )

    if last_error:
        raise last_error
    raise RuntimeError("No discovery models configured")


async def _run_audio_plan_with_fallback(
    *,
    prompt: str,
    timeout_seconds: int,
) -> _AudioPlanOutput:
    last_error: Exception | None = None
    models = _candidate_models(AUDIO_PLAN_MODEL, AUDIO_PLAN_FALLBACK_MODELS)

    for attempt_index, model_spec in enumerate(models, start=1):
        try:
            agent = get_basic_agent(model_spec, _AudioPlanOutput, AUDIO_PLAN_SYSTEM_PROMPT)
            if hasattr(agent, "run"):
                result = await agent.run(prompt, model_settings={"timeout": timeout_seconds})
            else:
                result = agent.run_sync(prompt, model_settings={"timeout": timeout_seconds})
            output = _get_agent_output(result)
            if attempt_index > 1:
                logger.warning(
                    "Onboarding audio plan succeeded on fallback model",
                    extra={
                        "component": "onboarding",
                        "operation": "audio_plan",
                        "context_data": {
                            "model": model_spec,
                            "attempt": attempt_index,
                            "models_tried": models,
                        },
                    },
                )
            return output
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Onboarding audio plan model attempt failed",
                extra={
                    "component": "onboarding",
                    "operation": "audio_plan",
                    "context_data": {
                        "model": model_spec,
                        "attempt": attempt_index,
                        "models_tried": models,
                        "error": str(exc),
                    },
                },
            )

    if last_error:
        raise last_error
    raise RuntimeError("No audio plan models configured")


def _get_agent_output(result: Any) -> Any:
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    raise AttributeError("Agent result missing output")


def _normalize_audio_lane_plan(plan: _AudioPlanOutput, transcript: str) -> _AudioPlanOutput:
    normalized_plan, _ = _normalize_audio_lane_plan_with_metadata(plan, transcript)
    return normalized_plan


def _normalize_audio_lane_plan_with_metadata(
    plan: _AudioPlanOutput, transcript: str
) -> tuple[_AudioPlanOutput, bool]:
    topic_summary = (plan.topic_summary or "").strip()
    if not topic_summary:
        topic_summary = _fallback_topic_summary(transcript)

    inferred_topics = _merge_topics(plan.inferred_topics, max_topics=6)
    lanes: list[_AudioLane] = []
    seen_names: set[str] = set()
    has_reddit = False

    for lane in plan.lanes:
        name = (lane.name or "").strip()
        if not name:
            continue
        normalized_name = name.lower()
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)

        goal = (lane.goal or "").strip()
        queries = _refine_lane_queries(
            target=lane.target,
            queries=lane.queries,
            lane_goal=goal,
            inferred_topics=inferred_topics,
            topic_summary=topic_summary,
        )
        if len(queries) < 2:
            continue

        target = lane.target
        if target == "reddit":
            has_reddit = True

        lanes.append(
            _AudioLane(
                name=name,
                goal=goal,
                target=target,
                queries=queries[:4],
            )
        )
        if len(lanes) >= 5:
            break

    if not lanes:
        return _fallback_audio_lane_plan(transcript), True

    if not has_reddit:
        reddit_lane = _fallback_reddit_lane(transcript, inferred_topics, topic_summary)
        if lanes:
            existing_names = {lane.name.lower() for lane in lanes if lane.name}
            if reddit_lane.name.lower() in existing_names:
                reddit_lane = _AudioLane(
                    name=f"{reddit_lane.name} Suggestions",
                    goal=reddit_lane.goal,
                    target=reddit_lane.target,
                    queries=reddit_lane.queries,
                )
        if len(lanes) >= 5:
            lanes[-1] = reddit_lane
        else:
            lanes.append(reddit_lane)

    if len(lanes) < 3:
        lanes.extend(_fallback_core_lanes(transcript, inferred_topics, existing=lanes))

    return (
        _AudioPlanOutput(
            topic_summary=topic_summary,
            inferred_topics=inferred_topics,
            lanes=lanes[:5],
        ),
        False,
    )


def _fallback_audio_lane_plan(transcript: str) -> _AudioPlanOutput:
    inferred_topics = _merge_topics([_fallback_topic_summary(transcript)], max_topics=3)
    lanes = _fallback_core_lanes(transcript, inferred_topics, existing=[])
    return _AudioPlanOutput(
        topic_summary=_fallback_topic_summary(transcript),
        inferred_topics=inferred_topics,
        lanes=lanes,
    )


def _fallback_core_lanes(
    transcript: str,
    inferred_topics: list[str],
    *,
    existing: list[_AudioLane],
) -> list[_AudioLane]:
    seed = _seed_phrase(transcript, inferred_topics)
    topic_summary = _fallback_topic_summary(transcript)
    lanes = list(existing)
    if len(lanes) < 3:
        goal = "Find newsletters and RSS feeds aligned with the user's interests."
        lanes.append(
            _AudioLane(
                name="Newsletters & Feeds",
                goal=goal,
                target="feeds",
                queries=_refine_lane_queries(
                    target="feeds",
                    queries=[
                        f"{seed} newsletter",
                        f"{seed} RSS feed",
                        f"best {seed} Substack",
                    ],
                    lane_goal=goal,
                    inferred_topics=inferred_topics,
                    topic_summary=topic_summary,
                ),
            )
        )
    if len(lanes) < 3:
        goal = "Find podcast feeds covering the user's interests."
        lanes.append(
            _AudioLane(
                name="Podcasts",
                goal=goal,
                target="podcasts",
                queries=_refine_lane_queries(
                    target="podcasts",
                    queries=[
                        f"{seed} podcast",
                        f"{seed} podcast RSS",
                        f"best {seed} podcasts",
                    ],
                    lane_goal=goal,
                    inferred_topics=inferred_topics,
                    topic_summary=topic_summary,
                ),
            )
        )
    if not any(lane.target == "reddit" for lane in lanes):
        lanes.append(_fallback_reddit_lane(transcript, inferred_topics, topic_summary))
    return lanes


def _fallback_reddit_lane(
    transcript: str, inferred_topics: list[str], topic_summary: str | None = None
) -> _AudioLane:
    seed = _seed_phrase(transcript, inferred_topics)
    goal = "Find active subreddits for the user's interests."
    return _AudioLane(
        name="Reddit",
        goal=goal,
        target="reddit",
        queries=_refine_lane_queries(
            target="reddit",
            queries=[
                f"{seed} subreddit",
                f"best subreddits for {seed}",
                f"{seed} reddit community",
            ],
            lane_goal=goal,
            inferred_topics=inferred_topics,
            topic_summary=topic_summary or _fallback_topic_summary(transcript),
        ),
    )


def _fallback_topic_summary(transcript: str) -> str:
    cleaned = transcript.strip().strip(".")
    if not cleaned:
        return "general news interests"
    words = cleaned.split()
    return " ".join(words[:10])


def _seed_phrase(transcript: str, inferred_topics: list[str]) -> str:
    if inferred_topics:
        return inferred_topics[0]
    summary = _fallback_topic_summary(transcript)
    return summary or "technology news"


def _clean_queries(queries: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if not isinstance(query, str):
            continue
        normalized = query.strip().strip(".")
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
        if len(cleaned) >= 4:
            break
    return cleaned


def _refine_lane_queries(
    *,
    target: Literal["feeds", "podcasts", "reddit"],
    queries: Iterable[str],
    lane_goal: str,
    inferred_topics: list[str],
    topic_summary: str,
) -> list[str]:
    cleaned = _clean_queries(queries)
    keyword_pool = _merge_topics(inferred_topics, max_topics=6)
    if not keyword_pool:
        keyword_pool = _merge_topics([lane_goal], [topic_summary], max_topics=4)
    patterns = _query_patterns_for_target(target)

    if not cleaned:
        cleaned = [keyword_pool[0] if keyword_pool else lane_goal or "current developments"]

    refined: list[str] = []
    for idx, query in enumerate(cleaned[:4]):
        template = patterns[idx % len(patterns)]
        focus = _query_focus_phrase(query, keyword_pool, idx)
        candidate = template.format(focus=focus)
        refined.append(_enforce_query_word_range(candidate, target))

    while len(refined) < 3:
        idx = len(refined)
        template = patterns[idx % len(patterns)]
        focus_seed = keyword_pool[idx % len(keyword_pool)] if keyword_pool else lane_goal
        focus = _query_focus_phrase(focus_seed, keyword_pool, idx)
        candidate = template.format(focus=focus)
        refined.append(_enforce_query_word_range(candidate, target))

    normalized = _clean_queries(refined)
    if len(normalized) >= 2:
        return normalized[:4]

    fallback_focus = keyword_pool[0] if keyword_pool else "high-signal sources"
    return [
        _enforce_query_word_range(
            f"best {_target_query_keyword(target)} for {fallback_focus}",
            target,
        ),
        _enforce_query_word_range(
            f"top {_target_query_keyword(target)} about {fallback_focus}",
            target,
        ),
    ]


def _query_focus_phrase(query: str, keyword_pool: list[str], index: int) -> str:
    focus = query.strip().strip(".,;:!?")
    if not focus:
        focus = keyword_pool[index % len(keyword_pool)] if keyword_pool else "current trends"

    focus_tokens = [token for token in focus.split() if token]
    while focus_tokens and focus_tokens[0].lower() in {
        "best",
        "top",
        "popular",
        "weekly",
        "find",
        "search",
        "discover",
        "identify",
    }:
        focus_tokens.pop(0)

    deduped_focus_tokens: list[str] = []
    seen_focus: set[str] = set()
    for token in focus_tokens:
        lowered = token.lower()
        if lowered in seen_focus:
            continue
        seen_focus.add(lowered)
        deduped_focus_tokens.append(token)
    focus_tokens = deduped_focus_tokens

    if len(focus_tokens) < 2 and keyword_pool:
        keyword = keyword_pool[index % len(keyword_pool)]
        keyword_tokens = [token for token in keyword.split() if token]
        for token in keyword_tokens:
            if len(focus_tokens) >= 4:
                break
            if token.lower() in {existing.lower() for existing in focus_tokens}:
                continue
            focus_tokens.append(token)

    if not focus_tokens:
        return "current developments"
    return " ".join(focus_tokens[:4])


def _query_patterns_for_target(
    target: Literal["feeds", "podcasts", "reddit"],
) -> list[str]:
    if target == "podcasts":
        return [
            "best {focus} podcast episodes",
            "top {focus} podcast rss feeds",
            "weekly {focus} interview podcasts",
            "{focus} long-form educational podcasts",
        ]

    if target == "reddit":
        return [
            "best subreddits for {focus}",
            "active reddit communities about {focus}",
            "top reddit threads on {focus}",
            "{focus} subreddit recommendations and discussions",
        ]

    return [
        "best {focus} newsletters and rss feeds",
        "top {focus} substack and atom feeds",
        "weekly {focus} analysis newsletter feeds",
        "credible {focus} editorial rss sources",
    ]


def _target_query_keyword(target: Literal["feeds", "podcasts", "reddit"]) -> str:
    if target == "podcasts":
        return "podcasts"
    if target == "reddit":
        return "reddit communities"
    return "newsletters and rss feeds"


def _enforce_query_word_range(
    query: str, target: Literal["feeds", "podcasts", "reddit"]
) -> str:
    tokens = [token.strip(".,;:!?") for token in query.split()]
    tokens = [token for token in tokens if token]

    deduped_tokens: list[str] = []
    seen_tokens: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered in seen_tokens:
            continue
        seen_tokens.add(lowered)
        deduped_tokens.append(token)
    tokens = deduped_tokens

    if len(tokens) > 10:
        tokens = tokens[:10]

    target_fillers = {
        "feeds": ["newsletter", "rss", "feeds"],
        "podcasts": ["podcast", "episodes"],
        "reddit": ["reddit", "communities"],
    }
    fillers = target_fillers[target]
    filler_index = 0
    while len(tokens) < 5:
        tokens.append(fillers[filler_index % len(fillers)])
        filler_index += 1

    return " ".join(tokens)


def _merge_topics(*topic_lists: Iterable[str], max_topics: int = 8) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for topics in topic_lists:
        for topic in topics:
            if not isinstance(topic, str):
                continue
            normalized = topic.strip().strip(".,;:")
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
            if len(merged) >= max_topics:
                return merged
    return merged


def _build_profile_fallback_summary(first_name: str, topics: list[str]) -> str:
    cleaned_topics = _merge_topics(topics, max_topics=3)
    if cleaned_topics:
        return f"{first_name} interested in {', '.join(cleaned_topics)}"
    return first_name


def _curated_fill_in_candidates(
    curated: dict[str, list[OnboardingSuggestion]],
) -> dict[str, list[OnboardingSuggestion]]:
    feed_defaults = curated.get("substack", []) + curated.get("atom", [])
    return {
        "feeds": feed_defaults[:DISCOVERY_PROMPT_MAX_FILL_IN_FEEDS],
        "podcasts": curated.get("podcast_rss", [])[:DISCOVERY_PROMPT_MAX_FILL_IN_PODCASTS],
        "reddit": curated.get("reddit", [])[:DISCOVERY_PROMPT_MAX_FILL_IN_REDDIT],
    }


def _format_curated_candidate(item: OnboardingSuggestion) -> str:
    if item.suggestion_type == "reddit":
        label = item.subreddit or item.title or ""
        label = label.removeprefix("r/").strip("/")
        rationale = (item.rationale or "").strip()
        if rationale:
            return f"subreddit: {label} | rationale_hint: {rationale}"
        return f"subreddit: {label}"

    title = (item.title or "").strip() or "<untitled>"
    feed_url = (item.feed_url or "").strip() or (item.site_url or "").strip() or "<missing>"
    rationale = (item.rationale or "").strip()
    if rationale:
        return f"title: {title} | feed_url: {feed_url} | rationale_hint: {rationale}"
    return f"title: {title} | feed_url: {feed_url}"


def _format_discovery_prompt(
    request: OnboardingFastDiscoverRequest,
    results: list[_DiscoveryWebResult],
    curated_fill_ins: dict[str, list[OnboardingSuggestion]] | None = None,
) -> str:
    lines = [
        f"profile_summary: {request.profile_summary}",
        f"topics: {', '.join(request.inferred_topics)}",
        "",
        "web_results:",
    ]
    for idx, item in enumerate(results[:DISCOVERY_PROMPT_MAX_WEB_RESULTS], start=1):
        lane_name = getattr(item, "lane_name", None)
        query = getattr(item, "query", None)
        lane_context = f" | lane: {lane_name}" if lane_name else ""
        query_context = f" | query: {query}" if query else ""
        lines.append(
            f"{idx}. {item.title}{lane_context}{query_context}\n"
            f"url: {item.url}\n"
            f"summary: {_prompt_snippet(item.snippet)}"
        )

    lines.extend(["", "curated_fill_ins:"])
    fill_ins = curated_fill_ins or {}
    for section_name in ("feeds", "podcasts", "reddit"):
        section_items = fill_ins.get(section_name, [])
        lines.append(f"{section_name}:")
        if not section_items:
            lines.append("  - none")
            continue
        for idx, item in enumerate(section_items, start=1):
            lines.append(f"  {idx}. {_format_curated_candidate(item)}")
    return "\n".join(lines)


def _fast_discover_from_defaults(
    curated: dict[str, list[OnboardingSuggestion]],
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> OnboardingFastDiscoverResponse:
    feed_defaults = curated.get("substack", []) + curated.get("atom", [])
    response = OnboardingFastDiscoverResponse(
        recommended_pods=curated.get("podcast_rss", []),
        recommended_substacks=feed_defaults[:ONBOARDING_FEED_SUGGESTION_LIMIT],
        recommended_subreddits=curated.get("reddit", []),
    )
    return _ensure_response_rationales(
        response,
        profile_summary=profile_summary,
        inferred_topics=inferred_topics,
    )


def _build_discovery_response(
    output: _DiscoverOutput,
    curated: dict[str, list[OnboardingSuggestion]],
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> OnboardingFastDiscoverResponse:
    feed_defaults = curated.get("substack", []) + curated.get("atom", [])
    feed_limit = ONBOARDING_FEED_SUGGESTION_LIMIT
    substacks = _merge_suggestions(
        _normalize_suggestions(output.substacks, "substack"),
        feed_defaults,
        feed_limit,
    )
    podcasts = _merge_suggestions(
        _normalize_suggestions(output.podcasts, "podcast_rss"),
        curated.get("podcast_rss", []),
        DEFAULT_SOURCE_LIMITS["podcast_rss"],
    )
    subreddits = _merge_suggestions(
        _normalize_suggestions(output.subreddits, "reddit"),
        curated.get("reddit", []),
        DEFAULT_SOURCE_LIMITS["reddit"],
    )

    response = OnboardingFastDiscoverResponse(
        recommended_pods=podcasts,
        recommended_substacks=substacks,
        recommended_subreddits=subreddits,
    )
    return _ensure_response_rationales(
        response,
        profile_summary=profile_summary,
        inferred_topics=inferred_topics,
    )


def _ensure_response_rationales(
    response: OnboardingFastDiscoverResponse,
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> OnboardingFastDiscoverResponse:
    topic_list = list(inferred_topics or [])
    for item in (
        response.recommended_substacks
        + response.recommended_pods
        + response.recommended_subreddits
    ):
        if item.rationale and item.rationale.strip():
            continue
        item.rationale = _default_rationale(
            item,
            profile_summary=profile_summary,
            inferred_topics=topic_list,
        )
    return response


def _infer_feed_url_from_site(site_url: str | None) -> str | None:
    """Infer a likely feed URL from a candidate site URL without network calls."""
    if not site_url:
        return None
    normalized = site_url.strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    feed_markers = ("/feed", ".xml", "rss", "atom", "podcast")
    if any(marker in lowered for marker in feed_markers):
        return normalized
    return None


def _normalize_suggestions(
    items: list[_DiscoverSuggestion], suggestion_type: str
) -> list[OnboardingSuggestion]:
    normalized: list[OnboardingSuggestion] = []
    for item in items:
        feed_url = (item.feed_url or "").strip()
        candidate_feed_url = (item.candidate_feed_url or "").strip()
        site_url = (item.site_url or "").strip() or None
        subreddit = _normalize_subreddit_name((item.subreddit or "").strip())

        if not feed_url and candidate_feed_url:
            feed_url = candidate_feed_url
        if suggestion_type == "substack" and not feed_url and site_url:
            feed_url = site_url.rstrip("/") + "/feed"
        if not feed_url and item.is_likely_feed:
            feed_url = _infer_feed_url_from_site(site_url)
        if suggestion_type == "reddit" and not subreddit:
            subreddit = _normalize_subreddit_name(_extract_subreddit(site_url))

        if suggestion_type == "reddit":
            if not subreddit:
                continue
            normalized.append(
                OnboardingSuggestion(
                    suggestion_type="reddit",
                    title=item.title or subreddit,
                    site_url=site_url,
                    subreddit=subreddit,
                    rationale=item.rationale,
                    score=item.score,
                    is_default=False,
                )
            )
            continue

        if not feed_url:
            continue

        normalized.append(
            OnboardingSuggestion(
                suggestion_type=suggestion_type,
                title=item.title,
                site_url=site_url,
                feed_url=feed_url,
                rationale=item.rationale,
                score=item.score,
                is_default=False,
            )
        )
    return normalized


def _merge_suggestions(
    primary: list[OnboardingSuggestion],
    defaults: list[OnboardingSuggestion],
    limit: int,
) -> list[OnboardingSuggestion]:
    merged: list[OnboardingSuggestion] = []
    seen: set[str] = set()

    for item in list(primary) + list(defaults):
        key = _suggestion_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _suggestion_key(item: OnboardingSuggestion) -> str | None:
    if item.suggestion_type == "reddit":
        return _normalize_subreddit_name(item.subreddit)
    return item.feed_url


def _normalize_subreddit_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = cleaned.removeprefix("r/").strip("/")
    return cleaned or None


def _extract_subreddit(site_url: str | None) -> str | None:
    if not site_url:
        return None
    lowered = site_url.lower()
    if "reddit.com/r/" not in lowered:
        return None
    try:
        parts = lowered.split("reddit.com/r/")
        if len(parts) < 2:
            return None
        name = parts[1].split("/")[0]
        return name.strip()
    except Exception:
        return None


def _load_curated_defaults() -> dict[str, list[OnboardingSuggestion]]:
    defaults: dict[str, list[OnboardingSuggestion]] = {
        "substack": _load_substack_defaults(),
        "podcast_rss": _load_podcast_defaults(),
        "atom": _load_atom_defaults(),
        "reddit": _load_reddit_defaults(),
    }
    return defaults


def _extract_curated_rationale(item: dict[str, Any]) -> str | None:
    for field in ("rationale", "description", "summary", "about", "notes"):
        value = item.get(field)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _load_substack_defaults() -> list[OnboardingSuggestion]:
    feeds = load_substack_feeds()
    suggestions = []
    for feed in feeds:
        feed_url = (feed.get("url") or "").strip()
        if not feed_url:
            continue
        suggestions.append(
            OnboardingSuggestion(
                suggestion_type="substack",
                title=feed.get("name"),
                feed_url=feed_url,
                site_url=feed_url,
                rationale=_extract_curated_rationale(feed) if isinstance(feed, dict) else None,
                is_default=True,
            )
        )
    return suggestions


def _load_atom_defaults() -> list[OnboardingSuggestion]:
    feeds = load_atom_feeds()
    suggestions = []
    for feed in feeds:
        feed_url = (feed.get("url") or "").strip()
        if not feed_url:
            continue
        suggestions.append(
            OnboardingSuggestion(
                suggestion_type="atom",
                title=feed.get("name"),
                feed_url=feed_url,
                site_url=feed_url,
                rationale=_extract_curated_rationale(feed) if isinstance(feed, dict) else None,
                is_default=True,
            )
        )
    return suggestions


def _load_podcast_defaults() -> list[OnboardingSuggestion]:
    path = resolve_config_path("PODCAST_CONFIG_PATH", "podcasts.yml")
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except Exception:
        logger.warning("Failed to load podcast defaults", exc_info=True)
        return []

    feeds = payload.get("feeds") or []
    suggestions: list[OnboardingSuggestion] = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        feed_url = (feed.get("url") or "").strip()
        if not feed_url:
            continue
        suggestions.append(
            OnboardingSuggestion(
                suggestion_type="podcast_rss",
                title=feed.get("name"),
                feed_url=feed_url,
                site_url=feed_url,
                rationale=_extract_curated_rationale(feed),
                is_default=True,
            )
        )
    return suggestions


def _load_reddit_defaults() -> list[OnboardingSuggestion]:
    path = resolve_config_path("REDDIT_CONFIG_PATH", "reddit.yml")
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except Exception:
        logger.warning("Failed to load reddit defaults", exc_info=True)
        return []

    subreddits = payload.get("subreddits") or []
    suggestions: list[OnboardingSuggestion] = []
    for sub in subreddits:
        if not isinstance(sub, dict):
            continue
        name = _normalize_subreddit_name((sub.get("name") or "").strip())
        if not name:
            continue
        suggestions.append(
            OnboardingSuggestion(
                suggestion_type="reddit",
                title=name,
                site_url=f"https://www.reddit.com/r/{name}/",
                subreddit=name,
                rationale=_extract_curated_rationale(sub),
                is_default=True,
            )
        )
    return suggestions


def _defaults_to_selected_sources(
    curated: dict[str, list[OnboardingSuggestion]],
) -> list[OnboardingSelectedSource]:
    selections: list[OnboardingSelectedSource] = []
    feed_selections = 0
    for suggestion_type in ("substack", "podcast_rss", "atom"):
        defaults = curated.get(suggestion_type, [])
        if suggestion_type in {"substack", "atom"}:
            limit = ONBOARDING_FEED_SUGGESTION_LIMIT - feed_selections
            if limit <= 0:
                continue
        else:
            limit = DEFAULT_SOURCE_LIMITS[suggestion_type]
        for suggestion in defaults[:limit]:
            selections.append(
                OnboardingSelectedSource(
                    suggestion_type=suggestion.suggestion_type,
                    title=suggestion.title,
                    feed_url=suggestion.feed_url or "",
                    config={"feed_url": suggestion.feed_url or ""},
                )
            )
            if suggestion_type in {"substack", "atom"}:
                feed_selections += 1
    return selections


def _create_reddit_configs(db: Session, user_id: int, subreddits: list[str]) -> None:
    for subreddit in subreddits:
        cleaned = _normalize_subreddit_name(subreddit)
        if not cleaned:
            continue
        try:
            create_user_scraper_config(
                db,
                user_id=user_id,
                data=CreateUserScraperConfig(
                    scraper_type="reddit",
                    display_name=cleaned,
                    config={"subreddit": cleaned, "limit": DEFAULT_NEW_FEED_LIMIT},
                ),
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                logger.error(
                    "Failed to create subreddit config",
                    extra={
                        "component": "onboarding",
                        "operation": "create_subreddit",
                        "item_id": str(user_id),
                        "context_data": {"error": str(exc), "subreddit": cleaned},
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected error creating subreddit config",
                extra={
                    "component": "onboarding",
                    "operation": "create_subreddit",
                    "item_id": str(user_id),
                    "context_data": {"error": str(exc), "subreddit": cleaned},
                },
            )
    return None


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
    db.commit()
    return len(news_ids)


def _seed_default_feed_content_for_user(
    db: Session,
    user_id: int,
    selections: list[OnboardingSelectedSource],
    limit: int = FEED_CONTENT_SEED_LIMIT,
) -> int:
    """Seed existing article/podcast content from selected feeds into user inbox.

    Args:
        db: Database session.
        user_id: Current user id.
        selections: Onboarding source selections (with feed_url).
        limit: Maximum number of items to seed.

    Returns:
        Number of content items seeded.
    """
    if user_id <= 0 or limit <= 0 or not selections:
        return 0

    feed_urls = list(
        {
            selection.feed_url.strip()
            for selection in selections
            if selection.feed_url and selection.feed_url.strip()
        }
    )
    if not feed_urls:
        return 0

    existing = select(ContentStatusEntry.content_id).where(
        ContentStatusEntry.user_id == user_id,
    )

    content_ids = (
        db.query(Content.id)
        .filter(
            Content.content_metadata["feed_url"].as_string().in_(feed_urls),
            Content.status == ContentStatus.COMPLETED.value,
            Content.content_type.in_(
                [ContentType.ARTICLE.value, ContentType.PODCAST.value]
            ),
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .filter(~Content.id.in_(existing))
        .order_by(Content.created_at.desc())
        .limit(limit)
        .all()
    )

    if not content_ids:
        return 0

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
    db.commit()
    enqueue_visible_long_form_images_for_content_ids(
        db,
        [content_id for (content_id,) in content_ids],
    )
    return len(content_ids)


def _get_tutorial_flag(db: Session, user_id: int) -> bool:
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    return bool(user and user.has_completed_new_user_tutorial)


def _serialize_lane_status(lane: OnboardingDiscoveryLane) -> OnboardingDiscoveryLaneStatus:
    return OnboardingDiscoveryLaneStatus(
        name=lane.lane_name,
        status=lane.status,
        completed_queries=lane.completed_queries or 0,
        query_count=lane.query_count or 0,
    )


def _serialize_audio_lane_preview(lane: _AudioLane) -> OnboardingAudioLanePreview:
    return OnboardingAudioLanePreview(
        name=lane.name,
        goal=lane.goal,
        target=lane.target,
        queries=list(lane.queries),
        include_social=lane.target == "reddit",
        exa_results_per_query=FAST_DISCOVER_EXA_RESULTS,
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
                score=item.score,
                status="new",
            )
        )
    db.commit()


def _suggestion_label(item: OnboardingSuggestion) -> str:
    if item.suggestion_type == "reddit":
        return _normalize_subreddit_name(item.subreddit) or (item.title or "subreddit")
    return item.title or "this source"


def _discovery_context_hint(
    profile_summary: str | None,
    inferred_topics: list[str] | None,
) -> str:
    merged = _merge_topics(inferred_topics or [], [profile_summary or ""], max_topics=3)
    if not merged:
        return "your interests"
    if len(merged) == 1:
        return merged[0]
    return ", ".join(merged[:2])


def _default_rationale(
    item: OnboardingSuggestion,
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> str:
    label = _suggestion_label(item)
    context_hint = _discovery_context_hint(profile_summary, inferred_topics)

    if item.suggestion_type == "podcast_rss":
        return f"Podcast covering {label} with discussions relevant to {context_hint}."
    if item.suggestion_type == "reddit":
        return f"Active subreddit for {label} with ongoing threads related to {context_hint}."
    return f"Feed focused on {label} with updates tied to {context_hint}."


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
        item = OnboardingSuggestion(
            suggestion_type=suggestion.suggestion_type,
            title=suggestion.title,
            site_url=suggestion.site_url,
            feed_url=suggestion.feed_url,
            subreddit=suggestion.subreddit,
            rationale=suggestion.rationale,
            score=suggestion.score,
            is_default=False,
        )
        if suggestion.suggestion_type == "podcast_rss":
            podcasts.append(item)
        elif suggestion.suggestion_type == "reddit":
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
                        score=suggestion.score,
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
