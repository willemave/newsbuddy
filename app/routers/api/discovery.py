"""Discovery suggestions endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.commands import add_discovery_items as add_discovery_items_command
from app.commands import (
    subscribe_discovery_suggestions as subscribe_discovery_suggestions_command,
)
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.settings import get_settings
from app.models.api.content import PodcastEpisodeSearchResponse, PodcastEpisodeSearchResultResponse
from app.models.api.discovery import (
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
)
from app.models.db import (
    ContentKnowledgeSave,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    UserScraperConfig,
)
from app.models.db.users import User
from app.repositories.discovery_repository import list_user_suggestions_by_ids
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.podcast_search import search_podcast_episodes
from app.services.queue import TaskType

router = APIRouter()


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
def get_discovery_suggestions(
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
def get_discovery_history(
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
def search_discovery_podcast_episodes(
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
def refresh_discovery(
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
def subscribe_discovery_suggestions(
    payload: DiscoverySubscribeRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoverySubscribeResponse:
    return subscribe_discovery_suggestions_command.execute(
        db,
        user_id=require_user_id(current_user),
        payload=payload,
    )


@router.post(
    "/discovery/add-item",
    response_model=DiscoveryAddItemResponse,
    summary="Add single items from discovery suggestions",
)
def add_discovery_items(
    payload: DiscoveryAddItemRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryAddItemResponse:
    require_user_id(current_user)
    return add_discovery_items_command.execute(db, current_user=current_user, payload=payload)


@router.post(
    "/discovery/dismiss",
    response_model=DiscoveryDismissResponse,
    summary="Dismiss discovery suggestions",
)
def dismiss_discovery_suggestions(
    payload: DiscoveryDismissRequest,
    db: Session = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> DiscoveryDismissResponse:
    user_id = require_user_id(current_user)
    suggestions = list_user_suggestions_by_ids(
        db,
        user_id=user_id,
        suggestion_ids=payload.suggestion_ids,
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
def clear_discovery_suggestions(
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
