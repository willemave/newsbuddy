"""Discovery suggestions endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import HttpUrl, TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import DEFAULT_NEW_FEED_LIMIT
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.api.common import (
    DiscoveryAddItemRequest,
    DiscoveryAddItemResponse,
    DiscoveryDismissRequest,
    DiscoveryDismissResponse,
    DiscoveryHistoryResponse,
    DiscoveryRefreshResponse,
    DiscoveryRunSuggestions,
    DiscoverySubscribeRequest,
    DiscoverySubscribeResponse,
    DiscoverySuggestionResponse,
    DiscoverySuggestionsResponse,
    PodcastEpisodeSearchResponse,
    PodcastEpisodeSearchResultResponse,
)
from app.models.content_submission import SubmitContentRequest
from app.models.internal.scraper_configs import CreateUserScraperConfig
from app.models.schema import (
    ContentKnowledgeSave,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    UserScraperConfig,
)
from app.models.user import User
from app.services.content_submission import submit_user_content
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.podcast_search import search_podcast_episodes
from app.services.queue import TaskType
from app.services.scraper_configs import (
    ScraperConfigAlreadyExistsError,
    create_user_scraper_config,
)

logger = get_logger(__name__)

router = APIRouter()

ScraperTypeLiteral = Literal["substack", "atom", "podcast_rss", "youtube", "reddit"]
_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def _require_run_id(run_id: int | None) -> int:
    if run_id is None:
        raise ValueError("Discovery run is missing an id")
    return run_id


def _require_suggestion_id(suggestion_id: int | None) -> int:
    if suggestion_id is None:
        raise ValueError("Discovery suggestion is missing an id")
    return suggestion_id


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _is_youtube_watch_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "youtube.com/watch" in lowered or "youtu.be/" in lowered


def _normalize_feed_url_for_match(feed_url: str | None) -> str | None:
    if not feed_url:
        return None

    trimmed = feed_url.strip()
    if not trimmed:
        return None

    try:
        parsed = urlparse(trimmed)
    except Exception:  # noqa: BLE001
        return trimmed.rstrip("/")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path)
    return urlunparse(normalized)


def _suggestion_to_response(suggestion: FeedDiscoverySuggestion) -> DiscoverySuggestionResponse:
    suggestion_id = _require_suggestion_id(suggestion.id)
    suggestion_type = suggestion.suggestion_type
    feed_url = suggestion.feed_url
    status = suggestion.status
    if suggestion_type is None or feed_url is None or status is None:
        raise ValueError("Discovery suggestion is missing required fields")
    return DiscoverySuggestionResponse(
        id=suggestion_id,
        suggestion_type=suggestion_type,
        site_url=suggestion.site_url,
        feed_url=feed_url,
        item_url=suggestion.item_url,
        title=suggestion.title,
        description=suggestion.description,
        channel_id=suggestion.channel_id,
        playlist_id=suggestion.playlist_id,
        rationale=suggestion.rationale,
        score=float(suggestion.score) if suggestion.score is not None else None,
        status=status,
        created_at=_serialize_dt(suggestion.created_at) or "",
    )


@router.get(
    "/discovery/suggestions",
    response_model=DiscoverySuggestionsResponse,
    summary="Get discovery suggestions",
)
async def get_discovery_suggestions(
    db: Session = Depends(get_readonly_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoverySuggestionsResponse:
    user_id = require_user_id(current_user)
    run = (
        db.query(FeedDiscoveryRun)
        .filter(FeedDiscoveryRun.user_id == user_id)
        .order_by(FeedDiscoveryRun.created_at.desc())
        .first()
    )
    if not run:
        return DiscoverySuggestionsResponse()

    suggestions = (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.run_id == run.id,
            FeedDiscoverySuggestion.status == "new",
        )
        .order_by(func.coalesce(FeedDiscoverySuggestion.score, 0).desc())
        .all()
    )

    feeds: list[DiscoverySuggestionResponse] = []
    podcasts: list[DiscoverySuggestionResponse] = []
    youtube: list[DiscoverySuggestionResponse] = []

    for suggestion in suggestions:
        try:
            response_item = _suggestion_to_response(suggestion)
        except ValueError:
            continue
        if suggestion.suggestion_type in {"atom", "substack"}:
            feeds.append(response_item)
        elif suggestion.suggestion_type == "podcast_rss":
            podcasts.append(response_item)
        elif suggestion.suggestion_type == "youtube":
            youtube.append(response_item)

    return DiscoverySuggestionsResponse(
        run_id=_require_run_id(run.id),
        run_status=run.status,
        run_created_at=_serialize_dt(run.created_at),
        direction_summary=run.direction_summary,
        feeds=feeds,
        podcasts=podcasts,
        youtube=youtube,
    )


@router.get(
    "/discovery/history",
    response_model=DiscoveryHistoryResponse,
    summary="Get discovery suggestions across recent runs",
)
async def get_discovery_history(
    limit: int = Query(6, ge=1, le=12),
    db: Session = Depends(get_readonly_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryHistoryResponse:
    user_id = require_user_id(current_user)
    runs = (
        db.query(FeedDiscoveryRun)
        .filter(FeedDiscoveryRun.user_id == user_id)
        .order_by(FeedDiscoveryRun.created_at.desc())
        .limit(limit)
        .all()
    )
    if not runs:
        return DiscoveryHistoryResponse()

    run_ids = [_require_run_id(run.id) for run in runs if run.id is not None]
    suggestions = (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.run_id.in_(run_ids),
            FeedDiscoverySuggestion.status == "new",
        )
        .order_by(func.coalesce(FeedDiscoverySuggestion.score, 0).desc())
        .all()
    )

    grouped: dict[int, dict[str, list[DiscoverySuggestionResponse]]] = {
        run_id: {"feeds": [], "podcasts": [], "youtube": []} for run_id in run_ids
    }

    for suggestion in suggestions:
        try:
            response_item = _suggestion_to_response(suggestion)
        except ValueError:
            continue
        suggestion_run_id = suggestion.run_id
        if suggestion_run_id is None:
            continue
        bucket = grouped.get(suggestion_run_id)
        if not bucket:
            continue
        if suggestion.suggestion_type in {"atom", "substack"}:
            bucket["feeds"].append(response_item)
        elif suggestion.suggestion_type == "podcast_rss":
            bucket["podcasts"].append(response_item)
        elif suggestion.suggestion_type == "youtube":
            bucket["youtube"].append(response_item)

    run_payloads: list[DiscoveryRunSuggestions] = []
    for run in runs:
        run_id = run.id
        run_status = run.status
        if run_id is None or run_status is None:
            continue
        bucket = grouped.get(run_id)
        if not bucket:
            continue
        if not (bucket["feeds"] or bucket["podcasts"] or bucket["youtube"]):
            continue
        run_payloads.append(
            DiscoveryRunSuggestions(
                run_id=run_id,
                run_status=run_status,
                run_created_at=_serialize_dt(run.created_at) or "",
                direction_summary=run.direction_summary,
                feeds=bucket["feeds"],
                podcasts=bucket["podcasts"],
                youtube=bucket["youtube"],
            )
        )

    return DiscoveryHistoryResponse(runs=run_payloads)


@router.get(
    "/discovery/search/podcasts",
    response_model=PodcastEpisodeSearchResponse,
    summary="Search podcast episodes online",
)
async def search_discovery_podcast_episodes(
    q: str = Query(
        ...,
        min_length=2,
        max_length=200,
        description="Podcast episode search query",
    ),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(get_readonly_db_session),
    current_user: User = Depends(get_current_user),
) -> PodcastEpisodeSearchResponse:
    user_id = require_user_id(current_user)
    existing_feed_rows = (
        db.query(UserScraperConfig.feed_url)
        .filter(
            UserScraperConfig.user_id == user_id,
            UserScraperConfig.scraper_type == "podcast_rss",
            UserScraperConfig.is_active.is_(True),
        )
        .all()
    )
    existing_feed_urls = {
        normalized
        for (feed_url,) in existing_feed_rows
        if (normalized := _normalize_feed_url_for_match(feed_url)) is not None
    }

    provider_limit = min(25, max(limit, limit * 3))
    provider_results = search_podcast_episodes(query=q, limit=provider_limit)

    filtered_results = []
    for item in provider_results:
        normalized_feed_url = _normalize_feed_url_for_match(item.feed_url)
        if normalized_feed_url and normalized_feed_url in existing_feed_urls:
            continue
        filtered_results.append(item)
        if len(filtered_results) >= limit:
            break

    return PodcastEpisodeSearchResponse(
        results=[
            PodcastEpisodeSearchResultResponse(
                title=item.title,
                episode_url=item.episode_url,
                podcast_title=item.podcast_title,
                source=item.source,
                snippet=item.snippet,
                feed_url=item.feed_url,
                published_at=item.published_at,
                provider=item.provider,
                score=item.score,
            )
            for item in filtered_results
        ]
    )


@router.post(
    "/discovery/refresh",
    response_model=DiscoveryRefreshResponse,
    summary="Trigger discovery refresh",
)
async def refresh_discovery(
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryRefreshResponse:
    user_id = require_user_id(current_user)
    settings = get_settings()
    knowledge_save_count = (
        db.query(func.count(ContentKnowledgeSave.id))
        .filter(ContentKnowledgeSave.user_id == user_id)
        .scalar()
        or 0
    )
    if knowledge_save_count < settings.discovery_min_favorites:
        raise HTTPException(
            status_code=400,
            detail="Not enough saved knowledge to run discovery",
        )

    task_id = get_task_queue_gateway().enqueue(
        TaskType.DISCOVER_FEEDS,
        payload={"user_id": user_id, "trigger": "manual"},
    )
    return DiscoveryRefreshResponse(status="queued", task_id=task_id)


@router.post(
    "/discovery/subscribe",
    response_model=DiscoverySubscribeResponse,
    summary="Subscribe to discovery suggestions",
)
async def subscribe_discovery_suggestions(
    payload: DiscoverySubscribeRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoverySubscribeResponse:
    user_id = require_user_id(current_user)
    suggestions = (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.id.in_(payload.suggestion_ids),
        )
        .all()
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
            if scraper_type not in {"substack", "atom", "podcast_rss", "youtube", "reddit"}:
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


@router.post(
    "/discovery/add-item",
    response_model=DiscoveryAddItemResponse,
    summary="Add single items from discovery suggestions",
)
async def add_discovery_items(
    payload: DiscoveryAddItemRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryAddItemResponse:
    user_id = require_user_id(current_user)
    suggestions = (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.id.in_(payload.suggestion_ids),
        )
        .all()
    )

    created: list[int] = []
    skipped: list[int] = []
    errors: list[dict[str, str]] = []

    for suggestion in suggestions:
        suggestion_id = suggestion.id
        item_url = suggestion.item_url
        if suggestion_id is None:
            continue
        if not item_url:
            skipped.append(suggestion_id)
            continue

        try:
            validated_item_url = _HTTP_URL_ADAPTER.validate_python(item_url)
            response = submit_user_content(
                db,
                SubmitContentRequest(
                    url=validated_item_url,
                    content_type=None,
                    title=suggestion.title,
                    platform=None,
                    instruction=None,
                    crawl_links=False,
                    subscribe_to_feed=False,
                    share_and_chat=False,
                    save_to_knowledge_and_mark_read=False,
                ),
                current_user,
            )
            if response.already_exists:
                skipped.append(suggestion_id)
            else:
                created.append(response.content_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to add discovery item",
                extra={
                    "component": "feed_discovery",
                    "operation": "add_item",
                    "item_id": str(suggestion_id),
                    "context_data": {"error": str(exc)},
                },
            )
            errors.append({"id": str(suggestion_id), "error": str(exc)})

    db.commit()
    return DiscoveryAddItemResponse(created=created, skipped=skipped, errors=errors)


@router.post(
    "/discovery/dismiss",
    response_model=DiscoveryDismissResponse,
    summary="Dismiss discovery suggestions",
)
async def dismiss_discovery_suggestions(
    payload: DiscoveryDismissRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryDismissResponse:
    user_id = require_user_id(current_user)
    suggestions = (
        db.query(FeedDiscoverySuggestion)
        .filter(
            FeedDiscoverySuggestion.user_id == user_id,
            FeedDiscoverySuggestion.id.in_(payload.suggestion_ids),
        )
        .all()
    )

    dismissed: list[int] = []
    for suggestion in suggestions:
        suggestion.status = "dismissed"
        if suggestion.id is not None:
            dismissed.append(suggestion.id)

    db.commit()
    return DiscoveryDismissResponse(dismissed=dismissed)


@router.post(
    "/discovery/clear",
    response_model=DiscoveryDismissResponse,
    summary="Clear all discovery suggestions",
)
async def clear_discovery_suggestions(
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryDismissResponse:
    user_id = require_user_id(current_user)
    suggestions = (
        db.query(FeedDiscoverySuggestion).filter(FeedDiscoverySuggestion.user_id == user_id).all()
    )

    dismissed: list[int] = []
    for suggestion in suggestions:
        if suggestion.status != "dismissed":
            suggestion.status = "dismissed"
            if suggestion.id is not None:
                dismissed.append(suggestion.id)

    db.commit()
    return DiscoveryDismissResponse(dismissed=dismissed)
