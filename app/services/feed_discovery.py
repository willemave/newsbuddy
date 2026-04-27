"""Feed/podcast/YouTube discovery workflow using favorites + Exa."""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from time import perf_counter
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, RunContext
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.feed_discovery import (
    DiscoveryCandidate,
    DiscoveryCandidateBatch,
    DiscoveryDirectionPlan,
    DiscoveryLane,
    DiscoveryLanePlan,
    DiscoveryRunResult,
    FavoriteDigest,
)
from app.models.schema import (
    Content,
    ContentKnowledgeSave,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    UserScraperConfig,
)
from app.services.content_submission import normalize_url
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.feed_detection import FeedDetector
from app.services.http import HttpService
from app.services.llm_agents import get_basic_agent
from app.services.llm_models import build_pydantic_model
from app.services.vendor_usage import (
    end_usage_context,
    record_model_usage,
    snapshot_usage,
    start_usage_context,
)

logger = get_logger(__name__)

FEED_TYPES = {"atom", "substack"}
PODCAST_TYPES = {"podcast_rss"}
YOUTUBE_TYPE = "youtube"
APPLE_PODCAST_HOSTS = ("podcasts.apple.com", "itunes.apple.com")
APPLE_PODCAST_ID_REGEX = re.compile(r"/id(?P<podcast_id>\d+)")
DISCOVERY_SKIP_HOSTS = {
    "link.chtbl.com",
    "podcasts.apple.com",
    "itunes.apple.com",
    "overcast.fm",
    "pca.st",
    "open.spotify.com",
    "creators.spotify.com",
    "podcasts.google.com",
    "music.youtube.com",
}
DISCOVERY_DOMAIN_ATTEMPT_LIMIT = 3
MARKDOWN_URL_REGEX = re.compile(r"\((https?://[^)]+)\)")
HTTP_SERVICE = HttpService()


class FeedDiscoveryRequest(BaseModel):
    """Input for a discovery run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: int
    trigger: str = "cron"


@dataclass
class FeedDiscoveryDeps:
    direction_selector: Callable[[Session, int], DiscoveryDirectionPlan]
    lane_planner: Callable[[Session, int, DiscoveryDirectionPlan], DiscoveryLanePlan]
    candidate_extractor: Callable[
        [Session, int, DiscoveryLane, list[ExaSearchResult]], DiscoveryCandidateBatch
    ]
    exa_search_fn: Callable[[str, int], list[ExaSearchResult]]
    candidate_validator: Callable[
        [Session, int, Iterable[DiscoveryCandidate], str], list[DiscoveryCandidate]
    ]


@dataclass
class DiscoveryToolDeps:
    user_id: int


def _require_run_id(run: FeedDiscoveryRun) -> int:
    run_id = run.id
    if run_id is None:
        raise ValueError("Feed discovery run is missing an id")
    return int(run_id)


def _require_run_status(run: FeedDiscoveryRun) -> str:
    status = run.status
    if not isinstance(status, str) or not status:
        raise ValueError("Feed discovery run is missing a status")
    return status


def run_feed_discovery(
    user_id: int,
    trigger: str = "cron",
    *,
    deps: FeedDiscoveryDeps | None = None,
) -> DiscoveryRunResult:
    """Run feed discovery for a single user.

    Args:
        user_id: User identifier.
        trigger: Trigger source (cron|manual).

    Returns:
        DiscoveryRunResult with counts.
    """
    request = FeedDiscoveryRequest(user_id=user_id, trigger=trigger)
    return _run_feed_discovery(request, deps=deps)


def _run_feed_discovery(
    request: FeedDiscoveryRequest,
    *,
    deps: FeedDiscoveryDeps | None = None,
) -> DiscoveryRunResult:
    settings = get_settings()

    deps = deps or _default_deps(
        settings.discovery_model,
        settings.discovery_candidate_model,
    )

    logger.info(
        "Starting feed discovery",
        extra={
            "component": "feed_discovery",
            "operation": "start",
            "item_id": str(request.user_id),
            "context_data": {"trigger": request.trigger},
        },
    )

    with get_db() as db:
        favorites = _fetch_favorites(db, request.user_id)
        if len(favorites) < settings.discovery_min_favorites:
            run = _create_run(
                db,
                user_id=request.user_id,
                seed_content_ids=[fav.id for fav in favorites],
                status="failed",
                error_message="insufficient_favorites",
            )
            db.commit()
            return DiscoveryRunResult(
                run_id=_require_run_id(run),
                feeds=0,
                podcasts=0,
                youtube=0,
                status=_require_run_status(run),
            )
        if not favorites:
            run = _create_run(
                db,
                user_id=request.user_id,
                seed_content_ids=[],
                status="completed",
                error_message="no_favorites",
            )
            db.commit()
            return DiscoveryRunResult(
                run_id=_require_run_id(run),
                feeds=0,
                podcasts=0,
                youtube=0,
                status=_require_run_status(run),
            )

        selected = _select_seed_favorites(
            favorites,
            limit=settings.discovery_max_favorites,
        )

        run = _create_run(
            db,
            user_id=request.user_id,
            seed_content_ids=[fav.id for fav in selected],
            status="processing",
        )
        db.commit()

        usage_token = start_usage_context()
        run_start = perf_counter()
        timing: dict[str, float] = {}
        try:
            logger.debug(
                "Selecting discovery directions",
                extra={
                    "component": "feed_discovery",
                    "operation": "direction_select",
                    "item_id": str(request.user_id),
                    "context_data": {"favorite_count": len(favorites)},
                },
            )
            direction_start = perf_counter()
            direction_plan = deps.direction_selector(
                db,
                request.user_id,
            )
            timing["direction_ms"] = (perf_counter() - direction_start) * 1000
            logger.debug(
                "Direction selection complete",
                extra={
                    "component": "feed_discovery",
                    "operation": "direction_select",
                    "item_id": str(request.user_id),
                    "context_data": {"direction_count": len(direction_plan.directions)},
                },
            )
            lane_start = perf_counter()
            lane_plan = deps.lane_planner(db, request.user_id, direction_plan)
            timing["lane_ms"] = (perf_counter() - lane_start) * 1000
            logger.debug(
                "Lane planning complete",
                extra={
                    "component": "feed_discovery",
                    "operation": "lane_plan",
                    "item_id": str(request.user_id),
                    "context_data": {"lane_count": len(lane_plan.lanes)},
                },
            )

            candidate_start = perf_counter()
            candidates = _collect_candidates(
                db,
                request.user_id,
                lane_plan,
                deps.exa_search_fn,
                deps.candidate_extractor,
            )
            timing["candidate_extract_ms"] = (perf_counter() - candidate_start) * 1000
            logger.debug(
                "Candidate extraction complete",
                extra={
                    "component": "feed_discovery",
                    "operation": "candidate_extract",
                    "item_id": str(request.user_id),
                    "context_data": {"candidate_count": len(candidates)},
                },
            )
            validate_start = perf_counter()
            suggestions = deps.candidate_validator(
                db,
                request.user_id,
                candidates,
                settings.discovery_model,
            )
            timing["candidate_validate_ms"] = (perf_counter() - validate_start) * 1000
            logger.debug(
                "Candidate validation complete",
                extra={
                    "component": "feed_discovery",
                    "operation": "candidate_validate",
                    "item_id": str(request.user_id),
                    "context_data": {"suggestion_count": len(suggestions)},
                },
            )

            feeds, podcasts, youtube = _select_suggestions(suggestions)
            persist_start = perf_counter()
            persisted = _persist_suggestions(
                db, _require_run_id(run), request.user_id, feeds + podcasts + youtube
            )
            timing["persist_ms"] = (perf_counter() - persist_start) * 1000

            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            run.direction_summary = direction_plan.summary
            _apply_usage_to_run(run)
            _apply_timing_to_run(run, timing, total_ms=(perf_counter() - run_start) * 1000)
            db.commit()

            logger.info(
                "Feed discovery completed",
                extra={
                    "component": "feed_discovery",
                    "operation": "completed",
                    "item_id": str(request.user_id),
                    "context_data": {
                        "run_id": run.id,
                        "feeds": _count_types(persisted, FEED_TYPES),
                        "podcasts": _count_types(persisted, PODCAST_TYPES),
                        "youtube": _count_types(persisted, {YOUTUBE_TYPE}),
                        "token_input": run.token_input,
                        "token_output": run.token_output,
                        "token_total": run.token_total,
                        "duration_ms_total": run.duration_ms_total,
                        "duration_ms_direction": run.duration_ms_direction,
                        "duration_ms_lane": run.duration_ms_lane,
                        "duration_ms_candidate_extract": run.duration_ms_candidate_extract,
                        "duration_ms_candidate_validate": run.duration_ms_candidate_validate,
                        "duration_ms_persist": run.duration_ms_persist,
                    },
                },
            )

            return DiscoveryRunResult(
                run_id=_require_run_id(run),
                feeds=_count_types(persisted, FEED_TYPES),
                podcasts=_count_types(persisted, PODCAST_TYPES),
                youtube=_count_types(persisted, {YOUTUBE_TYPE}),
                status=_require_run_status(run),
            )
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(UTC)
            _apply_usage_to_run(run)
            _apply_timing_to_run(run, timing, total_ms=(perf_counter() - run_start) * 1000)
            db.commit()
            logger.exception(
                "Feed discovery failed",
                extra={
                    "component": "feed_discovery",
                    "operation": "run",
                    "item_id": str(request.user_id),
                    "context_data": {"error": str(exc)},
                },
            )
            return DiscoveryRunResult(
                run_id=_require_run_id(run),
                feeds=0,
                podcasts=0,
                youtube=0,
                status=_require_run_status(run),
            )
        finally:
            end_usage_context(usage_token)


def _default_deps(model_spec: str, candidate_model_spec: str) -> FeedDiscoveryDeps:
    return FeedDiscoveryDeps(
        direction_selector=lambda db, user_id: _select_directions_llm(db, user_id, model_spec),
        lane_planner=lambda db, user_id, directions: _plan_lanes_llm(
            db,
            user_id,
            directions,
            model_spec,
        ),
        candidate_extractor=lambda db, user_id, lane, results: _extract_candidates_llm(
            db,
            user_id,
            lane,
            results,
            candidate_model_spec,
        ),
        exa_search_fn=_run_exa_search,
        candidate_validator=lambda db, user_id, candidates, spec: _validate_and_filter_candidates(
            db, user_id, candidates, model_spec=spec
        ),
    )


def _apply_usage_to_run(run: FeedDiscoveryRun) -> None:
    usage = snapshot_usage()
    if not usage:
        return
    totals = usage.get("total", {})
    run.token_input = totals.get("input_tokens")
    run.token_output = totals.get("output_tokens")
    run.token_total = totals.get("total_tokens")
    run.token_usage = usage


def _apply_timing_to_run(
    run: FeedDiscoveryRun, timing: dict[str, float], *, total_ms: float
) -> None:
    run.duration_ms_total = cast(Any, total_ms)
    run.duration_ms_direction = cast(Any, timing.get("direction_ms"))
    run.duration_ms_lane = cast(Any, timing.get("lane_ms"))
    run.duration_ms_candidate_extract = cast(Any, timing.get("candidate_extract_ms"))
    run.duration_ms_candidate_validate = cast(Any, timing.get("candidate_validate_ms"))
    run.duration_ms_persist = cast(Any, timing.get("persist_ms"))
    run.timing_json = timing


def _fetch_favorites(db: Session, user_id: int) -> list[FavoriteDigest]:
    rows = (
        db.query(ContentKnowledgeSave, Content)
        .join(Content, Content.id == ContentKnowledgeSave.content_id)
        .filter(ContentKnowledgeSave.user_id == user_id)
        .order_by(ContentKnowledgeSave.saved_at.desc())
        .all()
    )

    favorites: list[FavoriteDigest] = []
    for _fav, content in rows:
        favorites.append(
            FavoriteDigest(
                id=content.id,
                title=content.title,
                source=content.source,
                url=content.url,
                content_type=content.content_type,
                summary=content.short_summary,
            )
        )
    return favorites


def _select_seed_favorites(
    favorites: list[FavoriteDigest],
    limit: int,
) -> list[FavoriteDigest]:
    if len(favorites) <= limit:
        return favorites

    candidates = favorites[: max(limit * 3, limit + 5)]

    selected: list[FavoriteDigest] = []
    seen_sources: set[str] = set()
    for favorite in candidates:
        source_key = (favorite.source or _domain_from_url(favorite.url) or "").lower()
        if source_key and source_key in seen_sources:
            continue
        selected.append(favorite)
        if source_key:
            seen_sources.add(source_key)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        remaining = [fav for fav in candidates if fav not in selected]
        selected.extend(remaining[: max(0, limit - len(selected))])

    return selected


@lru_cache(maxsize=8)
def _get_direction_agent(model_spec: str) -> Agent[DiscoveryToolDeps, DiscoveryDirectionPlan]:
    model, model_settings = build_pydantic_model(model_spec)
    agent: Agent[DiscoveryToolDeps, DiscoveryDirectionPlan] = Agent(
        model,
        deps_type=DiscoveryToolDeps,
        output_type=DiscoveryDirectionPlan,
        system_prompt=(
            "You are a discovery planner. Analyze the user's favorited content and "
            "propose 2-4 distinct exploration directions for discovering new feeds, "
            "podcasts, and YouTube channels."
        ),
        model_settings=model_settings,
    )

    @agent.tool
    def search_favorites(
        ctx: RunContext[DiscoveryToolDeps],
        query: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> str:
        """Search user favorites with optional query and pagination."""
        logger.debug(
            "search_favorites tool called",
            extra={
                "component": "feed_discovery",
                "operation": "search_favorites",
                "item_id": str(ctx.deps.user_id),
                "context_data": {
                    "query_present": bool(query),
                    "query_length": len(query or ""),
                    "limit": limit,
                    "offset": offset,
                },
            },
        )
        limit = max(1, min(limit, 20))
        offset = max(0, offset)

        with get_db() as db:
            base_query = (
                db.query(ContentKnowledgeSave, Content)
                .join(Content, Content.id == ContentKnowledgeSave.content_id)
                .filter(ContentKnowledgeSave.user_id == ctx.deps.user_id)
            )
            if query:
                like = f"%{query.strip()}%"
                base_query = base_query.filter(
                    or_(
                        Content.title.ilike(like),
                        Content.source.ilike(like),
                        Content.url.ilike(like),
                        cast(Any, Content.short_summary).ilike(like),
                    )
                )

            total = base_query.count()
            rows = (
                base_query.order_by(ContentKnowledgeSave.saved_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )

            formatted_rows: list[dict[str, str | None]] = []
            for _fav, content in rows:
                formatted_rows.append(
                    {
                        "id": str(content.id),
                        "title": content.title or "Untitled",
                        "content_type": content.content_type,
                        "source": content.source or "unknown",
                        "url": content.url,
                        "summary": (content.short_summary or "")[:300] or None,
                    }
                )

        if not formatted_rows:
            return f"total={total} offset={offset} limit={limit}\nNo favorites found."

        lines = [f"total={total} offset={offset} limit={limit}"]
        for row in formatted_rows:
            lines.append(f"[{row['id']}] {row['title']} | {row['content_type']} | {row['source']}")
            if row["url"]:
                lines.append(f"URL: {row['url']}")
            if row["summary"]:
                lines.append(f"Summary: {row['summary']}")
            lines.append("")
        return "\n".join(lines)

    return agent


def _select_directions_llm(
    db: Session,
    user_id: int,
    model_spec: str,
) -> DiscoveryDirectionPlan:
    queue_settings = get_settings().queue
    agent = _get_direction_agent(model_spec)
    prompt = (
        "Use search_favorites to inspect the user's favorites. "
        "Call it multiple times (using offsets) until you have enough coverage "
        "to pick 2-4 distinct exploration directions. Return JSON with summary and "
        "directions. Each direction must include a name, rationale, and favorite_ids "
        "that justify it."
    )

    logger.debug(
        "Running LLM direction selection",
        extra={
            "component": "feed_discovery",
            "operation": "direction_llm",
            "context_data": {"user_id": user_id},
        },
    )
    result = agent.run_sync(
        prompt,
        deps=DiscoveryToolDeps(user_id=user_id),
        model_settings={"timeout": queue_settings.worker_timeout_seconds},
    )
    record_model_usage(
        "direction_select",
        result,
        model_spec=model_spec,
        persist={
            "feature": "feed_discovery",
            "operation": "feed_discovery.direction_select",
            "source": "queue",
            "user_id": user_id,
        },
    )
    tool_summary = _summarize_tool_calls(result)
    logger.debug(
        "Direction selection complete",
        extra={
            "component": "feed_discovery",
            "operation": "direction_llm",
            "item_id": str(user_id),
            "context_data": {
                "direction_count": len(result.output.directions),
                "tool_calls": tool_summary["tool_calls"],
                "tool_names": tool_summary["tool_names"],
            },
        },
    )
    logger.debug(
        "Direction tool calls summary: %s",
        tool_summary,
        extra={
            "component": "feed_discovery",
            "operation": "direction_llm",
            "item_id": str(user_id),
        },
    )
    logger.debug(
        "Direction plan output: %s",
        result.output.model_dump_json(),
        extra={
            "component": "feed_discovery",
            "operation": "direction_llm",
            "item_id": str(user_id),
        },
    )
    return result.output


def _plan_lanes_llm(
    db: Session,
    user_id: int,
    direction_plan: DiscoveryDirectionPlan,
    model_spec: str,
) -> DiscoveryLanePlan:
    queue_settings = get_settings().queue
    agent = get_basic_agent(
        model_spec=model_spec,
        output_type=DiscoveryLanePlan,
        system_prompt=(
            "You design discovery lanes with targeted search queries. "
            "Create 3-6 lanes across feeds, podcasts, and YouTube. "
            "Each lane includes 2-4 concrete queries."
        ),
    )

    prompt = (
        "Use the directions below to craft lanes. Mix in smallweb and Substack where relevant. "
        "Include at least one YouTube-focused lane if any direction suggests it. "
        "Include at least two podcast-focused lanes and ensure some queries mention "
        "podcast RSS feeds. Prefer generic queries like 'podcast', 'podcast RSS', "
        "or 'RSS feed' that can surface both single episodes and full podcast feeds. "
        "Avoid platform brand names except Apple Podcasts is allowed when it helps "
        "surface show pages we can resolve to RSS.\n\n"
        f"Directions: {direction_plan.model_dump_json()}"
    )

    logger.debug(
        "Lane planning prompt: %s",
        prompt,
        extra={
            "component": "feed_discovery",
            "operation": "lane_llm",
        },
    )
    logger.debug(
        "Running LLM lane planning",
        extra={
            "component": "feed_discovery",
            "operation": "lane_llm",
            "context_data": {"direction_count": len(direction_plan.directions)},
        },
    )
    result = agent.run_sync(
        prompt,
        model_settings={"timeout": queue_settings.worker_timeout_seconds},
    )
    record_model_usage(
        "lane_plan",
        result,
        model_spec=model_spec,
        persist={
            "feature": "feed_discovery",
            "operation": "feed_discovery.lane_plan",
            "source": "queue",
            "user_id": user_id,
        },
    )
    target_counts = Counter(lane.target for lane in result.output.lanes)
    logger.debug(
        "Lane planning complete",
        extra={
            "component": "feed_discovery",
            "operation": "lane_llm",
            "context_data": {
                "lane_count": len(result.output.lanes),
                "targets": dict(target_counts),
            },
        },
    )
    return result.output


def _extract_candidates_llm(
    db: Session,
    user_id: int,
    lane: DiscoveryLane,
    results: list[ExaSearchResult],
    model_spec: str,
) -> DiscoveryCandidateBatch:
    queue_settings = get_settings().queue
    agent = get_basic_agent(
        model_spec=model_spec,
        output_type=DiscoveryCandidateBatch,
        system_prompt=(
            "You are a curator selecting candidate feeds/podcasts/YouTube channels. "
            "Use search results to propose concrete sources with rationale and a relevance "
            "score (0-1)."
        ),
    )

    prompt = (
        "Return JSON candidates with site_url, optional feed_url, optional item_url, "
        "suggestion_type, and rationale. Include channel_id or playlist_id when YouTube is "
        "relevant. Use item_url for specific episodes/videos and keep feed_url for "
        "podcast RSS or YouTube channels/playlists. Apple Podcasts show URLs are "
        "acceptable; include them as site_url so we can resolve the RSS feed.\n\n"
        f"Lane: {lane.model_dump_json()}\n\n"
        f"Search results:\n{_format_exa_results(results)}"
    )

    logger.debug(
        "Running LLM candidate extraction",
        extra={
            "component": "feed_discovery",
            "operation": "candidate_llm",
            "context_data": {"lane": lane.name, "result_count": len(results)},
        },
    )
    result = agent.run_sync(
        prompt,
        model_settings={"timeout": queue_settings.worker_timeout_seconds},
    )
    record_model_usage(
        f"candidate_extract:{lane.name}",
        result,
        model_spec=model_spec,
        persist={
            "feature": "feed_discovery",
            "operation": "feed_discovery.candidate_extract",
            "source": "queue",
            "user_id": user_id,
            "metadata": {"lane": lane.name},
        },
    )
    tool_summary = _summarize_tool_calls(result)
    logger.debug(
        "Candidate extraction complete",
        extra={
            "component": "feed_discovery",
            "operation": "candidate_llm",
            "context_data": {
                "lane": lane.name,
                "candidate_count": len(result.output.candidates),
                "tool_calls": tool_summary["tool_calls"],
            },
        },
    )
    return result.output


def _run_exa_search(query: str, num_results: int) -> list[ExaSearchResult]:
    return exa_search(query, num_results=num_results)


def _collect_candidates(
    db: Session,
    user_id: int,
    lane_plan: DiscoveryLanePlan,
    exa_search_fn: Callable[[str, int], list[ExaSearchResult]],
    candidate_extractor: Callable[
        [Session, int, DiscoveryLane, list[ExaSearchResult]], DiscoveryCandidateBatch
    ],
) -> list[DiscoveryCandidate]:
    settings = get_settings()
    all_candidates: list[DiscoveryCandidate] = []

    for lane in lane_plan.lanes:
        logger.debug(
            "Running lane searches",
            extra={
                "component": "feed_discovery",
                "operation": "lane_search",
                "context_data": {
                    "lane": lane.name,
                    "target": lane.target,
                    "query_count": len(lane.queries),
                },
            },
        )
        lane_results: list[ExaSearchResult] = []
        for query in lane.queries:
            results = exa_search_fn(query.query, settings.discovery_exa_results)
            lane_results.extend(results)
            logger.debug(
                "Exa results collected",
                extra={
                    "component": "feed_discovery",
                    "operation": "exa_search",
                    "context_data": {
                        "lane": lane.name,
                        "query": query.query,
                        "result_count": len(results),
                    },
                },
            )
        if not lane_results:
            continue
        logger.debug(
            "Extracting candidates for lane",
            extra={
                "component": "feed_discovery",
                "operation": "candidate_extract",
                "context_data": {"lane": lane.name, "result_count": len(lane_results)},
            },
        )
        batch = candidate_extractor(db, user_id, lane, lane_results)
        logger.debug(
            "Lane candidates extracted",
            extra={
                "component": "feed_discovery",
                "operation": "candidate_extract",
                "context_data": {"lane": lane.name, "candidate_count": len(batch.candidates)},
            },
        )
        all_candidates.extend(batch.candidates)

    return all_candidates


def _validate_and_filter_candidates(
    db: Session,
    user_id: int,
    candidates: Iterable[DiscoveryCandidate],
    *,
    model_spec: str,
) -> list[DiscoveryCandidate]:
    detector = FeedDetector(use_exa_search=True, use_llm=True)
    existing_feeds = _existing_feed_urls(db, user_id)
    existing_suggestions = _existing_suggestion_urls(db, user_id)
    seen_feeds: set[str] = set()
    failed_domains: set[str] = set()
    domain_attempts: Counter[str] = Counter()

    validated: list[DiscoveryCandidate] = []
    for candidate in candidates:
        normalized = _normalize_candidate(candidate)
        if not normalized:
            continue

        if normalized.suggestion_type == YOUTUBE_TYPE:
            if normalized.feed_url and normalized.feed_url in existing_feeds:
                continue
            if normalized.feed_url and normalized.feed_url in existing_suggestions:
                continue
            if normalized.feed_url and normalized.feed_url in seen_feeds:
                continue
            if normalized.feed_url:
                seen_feeds.add(normalized.feed_url)
            validated.append(normalized)
            continue

        feed_url = normalized.feed_url
        site_url = normalized.site_url
        domain = _candidate_domain(normalized)
        if domain and domain in failed_domains:
            logger.debug(
                "Skipping candidate due to prior domain failure",
                extra={
                    "component": "feed_discovery",
                    "operation": "candidate_skip",
                    "context_data": {"domain": domain},
                },
            )
            continue
        if domain and domain_attempts[domain] >= DISCOVERY_DOMAIN_ATTEMPT_LIMIT:
            logger.debug(
                "Skipping candidate due to domain attempt cap",
                extra={
                    "component": "feed_discovery",
                    "operation": "candidate_skip",
                    "context_data": {"domain": domain, "limit": DISCOVERY_DOMAIN_ATTEMPT_LIMIT},
                },
            )
            continue
        if feed_url:
            if domain:
                domain_attempts[domain] += 1
            validated_feed = detector.validate_feed_url(feed_url)
            if not validated_feed:
                if domain:
                    failed_domains.add(domain)
                continue
            classification = detector.classify_feed_type(
                feed_url=feed_url,
                page_url=site_url or feed_url,
                page_title=normalized.title,
                model_spec=model_spec,
                db=db,
                usage_persist={
                    "feature": "feed_detection",
                    "operation": "feed_detection.classify_feed_type",
                    "source": "queue",
                    "user_id": user_id,
                    "metadata": {"page_url": site_url or feed_url},
                },
            )
            normalized.feed_url = validated_feed["feed_url"]
            normalized.title = normalized.title or validated_feed.get("title")
            normalized.suggestion_type = classification.feed_type
        else:
            if domain:
                domain_attempts[domain] += 1
            detection = detector.detect_from_links(
                None,
                page_url=site_url,
                page_title=normalized.title,
                source="feed_discovery",
                content_type="article",
                model_spec=model_spec,
                force_detect=True,
                db=db,
                usage_persist={
                    "feature": "feed_detection",
                    "operation": "feed_detection.classify_feed_type",
                    "source": "queue",
                    "user_id": user_id,
                    "metadata": {"page_url": site_url},
                },
            )
            if not detection:
                if domain:
                    failed_domains.add(domain)
                continue
            detected = detection["detected_feed"]
            feed_url = detected["url"]
            normalized.feed_url = feed_url
            normalized.title = normalized.title or detected.get("title")
            normalized.suggestion_type = detected.get("type")

        if not normalized.feed_url:
            continue
        if normalized.feed_url in existing_feeds:
            continue
        if normalized.feed_url in existing_suggestions:
            continue
        if normalized.feed_url in seen_feeds:
            continue
        seen_feeds.add(normalized.feed_url)
        validated.append(normalized)

    return validated


def _normalize_candidate(candidate: DiscoveryCandidate) -> DiscoveryCandidate | None:
    site_url = _normalize_candidate_url(candidate.site_url)
    feed_url = _normalize_candidate_url(candidate.feed_url)
    item_url = _normalize_candidate_url(candidate.item_url)

    if not site_url and feed_url:
        site_url = feed_url
    if not site_url:
        return None

    candidate.site_url = site_url
    candidate.feed_url = feed_url
    candidate.item_url = item_url

    normalized_candidate = _normalize_apple_podcast_candidate(candidate)
    if normalized_candidate is None:
        return None
    candidate = normalized_candidate

    if _should_skip_candidate(candidate):
        logger.debug(
            "Skipping candidate due to skipped host",
            extra={
                "component": "feed_discovery",
                "operation": "candidate_skip",
                "context_data": {
                    "site_url": candidate.site_url,
                    "feed_url": candidate.feed_url,
                },
            },
        )
        return None

    if _is_youtube_candidate(candidate):
        return _normalize_youtube_candidate(candidate)

    return candidate


def _sanitize_candidate_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None

    cleaned = html.unescape(raw_url.strip())
    match = MARKDOWN_URL_REGEX.search(cleaned)
    if match:
        cleaned = match.group(1)

    cleaned = cleaned.strip("<> \t\r\n")
    cleaned = cleaned.rstrip(").,]>\"'\\")
    if not cleaned:
        return None

    if not cleaned.startswith(("http://", "https://")):
        return None

    return cleaned


def _normalize_candidate_url(raw_url: str | None) -> str | None:
    cleaned = _sanitize_candidate_url(raw_url)
    if not cleaned:
        return None
    try:
        return normalize_url(cleaned)
    except Exception:  # noqa: BLE001
        return None


def _candidate_domain(candidate: DiscoveryCandidate) -> str | None:
    url = candidate.feed_url or candidate.site_url
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    return host or None


def _should_skip_candidate(candidate: DiscoveryCandidate) -> bool:
    feed_host = urlparse(candidate.feed_url).netloc.lower() if candidate.feed_url else ""
    if feed_host:
        return feed_host in DISCOVERY_SKIP_HOSTS
    site_host = urlparse(candidate.site_url).netloc.lower()
    return site_host in DISCOVERY_SKIP_HOSTS


def _is_youtube_candidate(candidate: DiscoveryCandidate) -> bool:
    if candidate.suggestion_type == YOUTUBE_TYPE:
        return True
    return bool(
        (candidate.site_url and "youtube.com" in candidate.site_url)
        or (candidate.feed_url and "youtube.com" in candidate.feed_url)
    )


def _normalize_youtube_candidate(candidate: DiscoveryCandidate) -> DiscoveryCandidate | None:
    url = candidate.feed_url or candidate.site_url
    if not url:
        return None

    channel_id, playlist_id, canonical = _parse_youtube_identifiers(url)
    candidate.suggestion_type = cast(Any, YOUTUBE_TYPE)
    if _looks_like_watch_url(url) and not candidate.item_url:
        candidate.item_url = canonical
    if candidate.site_url and _looks_like_watch_url(candidate.site_url) and not candidate.item_url:
        candidate.item_url = normalize_url(candidate.site_url)
    candidate.channel_id = channel_id
    candidate.playlist_id = playlist_id
    candidate.feed_url = canonical
    if channel_id or playlist_id:
        candidate.config = {
            "feed_url": canonical,
            **({"channel_id": channel_id} if channel_id else {}),
            **({"playlist_id": playlist_id} if playlist_id else {}),
        }
    else:
        candidate.config = None
    return candidate


def _normalize_apple_podcast_candidate(
    candidate: DiscoveryCandidate,
) -> DiscoveryCandidate | None:
    url = candidate.feed_url or candidate.site_url
    if not url or not _is_apple_podcast_url(url):
        return candidate

    podcast_id = _extract_apple_podcast_id(url)
    if not podcast_id:
        return candidate

    settings = get_settings()
    feed_url = _resolve_apple_podcast_feed_url(
        podcast_id, country=settings.discovery_itunes_country
    )
    if not feed_url:
        return candidate

    try:
        candidate.feed_url = normalize_url(feed_url)
    except Exception:  # noqa: BLE001
        return candidate

    try:
        candidate.site_url = candidate.site_url or normalize_url(url)
    except Exception:  # noqa: BLE001
        candidate.site_url = candidate.site_url or url

    candidate.suggestion_type = "podcast_rss"
    candidate.config = {
        **(candidate.config or {}),
        "source": "apple_podcasts",
        "podcast_id": podcast_id,
    }
    logger.debug(
        "Resolved Apple Podcasts feed URL",
        extra={
            "component": "feed_discovery",
            "operation": "apple_podcast_lookup",
            "context_data": {"podcast_id": podcast_id, "feed_url": candidate.feed_url},
        },
    )
    return candidate


def _is_apple_podcast_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host.endswith(domain) for domain in APPLE_PODCAST_HOSTS)


def _extract_apple_podcast_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not any(host.endswith(domain) for domain in APPLE_PODCAST_HOSTS):
        return None

    match = APPLE_PODCAST_ID_REGEX.search(parsed.path)
    if match:
        return match.group("podcast_id")

    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and query_id.isdigit():
        return query_id

    return None


def _resolve_apple_podcast_feed_url(podcast_id: str, *, country: str | None) -> str | None:
    country_value = (country or "").lower()
    try:
        return _itunes_lookup_feed_url(podcast_id, country_value)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Apple Podcasts lookup failed",
            extra={
                "component": "feed_discovery",
                "operation": "apple_podcast_lookup",
                "context_data": {"podcast_id": podcast_id, "error": str(exc)},
            },
        )
        return None


@lru_cache(maxsize=256)
def _itunes_lookup_feed_url(podcast_id: str, country: str) -> str | None:
    params = {"id": podcast_id, "entity": "podcast"}
    if country:
        params["country"] = country
    lookup_url = f"https://itunes.apple.com/lookup?{urlencode(params)}"
    response = HTTP_SERVICE.fetch(lookup_url, headers={"Accept": "application/json"})
    payload = response.json()
    results = payload.get("results", [])
    for item in results:
        feed_url = item.get("feedUrl")
        if feed_url:
            return feed_url
    return None


def _parse_youtube_identifiers(url: str) -> tuple[str | None, str | None, str]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        video_id = parsed.path.strip("/")
        canonical = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
        return None, None, canonical

    if "youtube.com" not in host:
        return None, None, url

    path = parsed.path.strip("/")
    if path.startswith("playlist"):
        playlist_id = parse_qs(parsed.query).get("list", [None])[0]
        canonical = f"https://www.youtube.com/playlist?list={playlist_id}" if playlist_id else url
        return None, playlist_id, canonical

    if path.startswith("channel/"):
        channel_id = path.split("/", 1)[1]
        canonical = f"https://www.youtube.com/channel/{channel_id}"
        return channel_id, None, canonical

    if path.startswith("@") or path.startswith("c/") or path.startswith("user/"):
        canonical = f"https://www.youtube.com/{path}"
        return None, None, canonical

    return None, None, url


def _looks_like_watch_url(url: str) -> bool:
    parsed = urlparse(url)
    if "youtube.com" in (parsed.netloc or "") and parsed.path.startswith("/watch"):
        return True
    return "youtu.be" in (parsed.netloc or "")


def _select_suggestions(
    candidates: list[DiscoveryCandidate],
) -> tuple[list[DiscoveryCandidate], list[DiscoveryCandidate], list[DiscoveryCandidate]]:
    feeds = [c for c in candidates if c.suggestion_type in FEED_TYPES]
    podcasts = [c for c in candidates if c.suggestion_type in PODCAST_TYPES]
    youtube = [c for c in candidates if c.suggestion_type == YOUTUBE_TYPE]

    feeds = _top_n(feeds, 10)
    podcasts = _top_n(podcasts, 10)
    youtube = _top_n(youtube, 10)

    return feeds, podcasts, youtube


def _top_n(candidates: list[DiscoveryCandidate], limit: int) -> list[DiscoveryCandidate]:
    def _score(candidate: DiscoveryCandidate) -> float:
        return candidate.score or 0.0

    return sorted(candidates, key=_score, reverse=True)[:limit]


def _persist_suggestions(
    db: Session,
    run_id: int,
    user_id: int,
    suggestions: list[DiscoveryCandidate],
) -> list[FeedDiscoverySuggestion]:
    records: list[FeedDiscoverySuggestion] = []
    existing = _existing_suggestion_urls(db, user_id)
    for suggestion in suggestions:
        if suggestion.feed_url in existing:
            continue
        config = suggestion.config
        if not config:
            if suggestion.suggestion_type == YOUTUBE_TYPE and not (
                suggestion.channel_id or suggestion.playlist_id
            ):
                config = {"item_url": suggestion.item_url}
            else:
                config = {"feed_url": suggestion.feed_url}
        record = FeedDiscoverySuggestion(
            run_id=run_id,
            user_id=user_id,
            suggestion_type=suggestion.suggestion_type or "atom",
            site_url=suggestion.site_url,
            feed_url=suggestion.feed_url or suggestion.site_url,
            item_url=suggestion.item_url,
            title=suggestion.title,
            description=suggestion.description,
            channel_id=suggestion.channel_id,
            playlist_id=suggestion.playlist_id,
            rationale=suggestion.rationale,
            score=cast(Any, suggestion.score),
            status="new",
            config=config,
            metadata_json={"evidence_urls": suggestion.evidence_urls},
        )
        db.add(record)
        records.append(record)
        if suggestion.feed_url:
            existing.add(suggestion.feed_url)
    db.commit()
    return records


def _existing_feed_urls(db: Session, user_id: int) -> set[str]:
    rows = (
        db.query(UserScraperConfig.feed_url)
        .filter(UserScraperConfig.user_id == user_id)
        .filter(UserScraperConfig.is_active.is_(True))
        .all()
    )
    return {row[0] for row in rows if row[0]}


def _existing_suggestion_urls(db: Session, user_id: int) -> set[str]:
    rows = (
        db.query(FeedDiscoverySuggestion.feed_url)
        .filter(FeedDiscoverySuggestion.user_id == user_id)
        .all()
    )
    return {row[0] for row in rows if row[0]}


def _create_run(
    db: Session,
    *,
    user_id: int,
    seed_content_ids: list[int],
    status: str,
    error_message: str | None = None,
) -> FeedDiscoveryRun:
    run = FeedDiscoveryRun(
        user_id=user_id,
        status=status,
        seed_content_ids=seed_content_ids,
        created_at=datetime.now(UTC),
        error_message=error_message,
    )
    db.add(run)
    db.flush()
    return run


def _count_types(records: Iterable[FeedDiscoverySuggestion], types: set[str]) -> int:
    return sum(1 for record in records if record.suggestion_type in types)


def _format_exa_results(results: list[ExaSearchResult]) -> str:
    lines: list[str] = []
    for idx, result in enumerate(results, start=1):
        lines.append(f"[{idx}] {result.title}")
        lines.append(f"URL: {result.url}")
        if result.snippet:
            lines.append(result.snippet[:500])
        lines.append("")
    return "\n".join(lines)


def _domain_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.netloc if parsed.netloc else None


def _summarize_tool_calls(result: object) -> dict[str, object]:
    tool_calls = getattr(result, "tool_calls", []) or []
    tool_names: list[str] = []
    for call in tool_calls:
        name = getattr(call, "tool_name", None) or getattr(call, "name", None)
        if name:
            tool_names.append(name)
    counts = Counter(tool_names)
    return {
        "tool_calls": len(tool_calls),
        "tool_names": list(counts.keys()),
        "tool_counts": dict(counts),
    }
