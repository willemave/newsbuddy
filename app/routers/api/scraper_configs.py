"""CRUD endpoints for per-user scraper configurations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.commands import subscribe_feed as subscribe_feed_command
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.scraper_configs import (
    ScraperConfigResponse,
    ScraperConfigStatsResponse,
    SubscribeToFeedRequest,
)
from app.models.contracts import FeedSubscriptionOutcome
from app.models.db.users import User
from app.models.internal.scraper_configs import CreateUserScraperConfig, UpdateUserScraperConfig
from app.services.scraper_configs import (
    ALLOWED_SCRAPER_TYPES,
    create_user_scraper_config,
    delete_user_scraper_config,
    get_scraper_config_stats,
    list_user_scraper_configs,
    update_user_scraper_config,
)

router = APIRouter(prefix="/scrapers", tags=["scrapers"])


def _require_config_id(config_id: int | None) -> int:
    if config_id is None:
        raise ValueError("Scraper config is missing an id")
    return config_id


def _coerce_limit(config: dict[str, Any]) -> int | None:
    limit = config.get("limit")
    if isinstance(limit, int) and 1 <= limit <= 100:
        return limit
    return None


def _serialize_scraper_config(
    config,
    *,
    stats: dict[str, Any] | None = None,
    subscription_outcome: FeedSubscriptionOutcome | None = None,
    backfill_task_id: int | None = None,
) -> ScraperConfigResponse:
    config_id = _require_config_id(config.id)
    return ScraperConfigResponse(
        id=config_id,
        scraper_type=config.scraper_type,
        display_name=config.display_name,
        config=config.config or {},
        feed_url=(config.config or {}).get("feed_url"),
        limit=_coerce_limit(config.config or {}),
        is_active=config.is_active,
        created_at=config.created_at,
        stats=ScraperConfigStatsResponse(**stats) if stats is not None else None,
        subscription_outcome=subscription_outcome,
        backfill_task_id=backfill_task_id,
    )


@router.get("/", response_model=list[ScraperConfigResponse])
def list_scraper_configs(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    scraper_type: str | None = Query(None, alias="type"),
    types: str | None = Query(None, alias="types"),
    include_stats: bool = Query(True),
) -> list[ScraperConfigResponse]:
    """List scraper configurations for the current user."""
    user_id = require_user_id(current_user)
    requested_types: set[str] = set()
    if scraper_type:
        requested_types.add(scraper_type)
    if types:
        requested_types.update({t for t in types.split(",") if t})

    if requested_types:
        invalid = requested_types.difference(ALLOWED_SCRAPER_TYPES)
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported scraper types: {', '.join(sorted(invalid))}",
            )

    configs = list_user_scraper_configs(
        db,
        user_id,
        allowed_types=requested_types or None,
    )
    stats_by_config = (
        get_scraper_config_stats(db, user_id=user_id, configs=configs) if include_stats else {}
    )
    return [
        _serialize_scraper_config(
            config,
            stats=stats_by_config.get(_require_config_id(config.id)),
        )
        for config in configs
    ]


@router.post("/", response_model=ScraperConfigResponse, status_code=status.HTTP_201_CREATED)
def create_scraper_config(
    payload: CreateUserScraperConfig,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ScraperConfigResponse:
    """Create a scraper config for the current user."""
    user_id = require_user_id(current_user)
    try:
        record = create_user_scraper_config(db, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    stats_by_config = get_scraper_config_stats(db, user_id=user_id, configs=[record])
    return _serialize_scraper_config(
        record,
        stats=stats_by_config.get(_require_config_id(record.id)),
    )


@router.put("/{config_id}", response_model=ScraperConfigResponse)
def update_scraper_config(
    config_id: int,
    payload: UpdateUserScraperConfig,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ScraperConfigResponse:
    """Update a scraper config belonging to the current user."""
    user_id = require_user_id(current_user)
    try:
        record = update_user_scraper_config(db, user_id, config_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    stats_by_config = get_scraper_config_stats(db, user_id=user_id, configs=[record])
    return _serialize_scraper_config(
        record,
        stats=stats_by_config.get(_require_config_id(record.id)),
    )


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scraper_config_endpoint(
    config_id: int,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a scraper config for the current user."""
    try:
        delete_user_scraper_config(db, require_user_id(current_user), config_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/subscribe", response_model=ScraperConfigResponse, status_code=status.HTTP_201_CREATED
)
def subscribe_to_feed(
    payload: SubscribeToFeedRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ScraperConfigResponse:
    """Subscribe to a feed detected from content.

    Convenience endpoint that creates a scraper config from a detected feed.
    """
    user_id = require_user_id(current_user)
    try:
        result = subscribe_feed_command.execute(db, user_id=user_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if result.outcome in {
        FeedSubscriptionOutcome.REACTIVATED,
        FeedSubscriptionOutcome.ALREADY_SUBSCRIBED,
    }:
        response.status_code = status.HTTP_200_OK
    record = result.config
    stats_by_config = get_scraper_config_stats(db, user_id=user_id, configs=[record])
    return _serialize_scraper_config(
        record,
        stats=stats_by_config.get(_require_config_id(record.id)),
        subscription_outcome=result.outcome,
        backfill_task_id=result.backfill_task_id,
    )
