from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.audio_episodes import AudioEpisodeDelivery, AudioEpisodeResponse
from app.models.api.briefing import (
    BriefingDigSearchRequest,
    BriefingDigSearchResponse,
    BriefingDigSummarizeRequest,
    BriefingDigSummarizeResponse,
    BriefingIndexResponse,
    BriefingLensResponse,
    BriefingNarrationRequest,
    BriefingNarrationResponse,
    BriefingReadMarkRequest,
    BriefingReadMarkResponse,
    BriefingRefreshResponse,
)
from app.models.db.users import User
from app.services.briefing.dig import search_fragment, summarize_fragment
from app.services.briefing.narration import (
    create_or_reuse_briefing_narration,
    create_or_reuse_legacy_briefing_narration,
    get_briefing_narration,
)
from app.services.briefing.presentation import (
    BRIEFING_LENS_PAGE_MAX,
    InvalidBriefingLensCursor,
    StaleBriefingLensCursor,
    get_briefing_index,
    get_briefing_index_validator,
    get_briefing_lens,
)
from app.services.briefing.read_marks import (
    mark_briefing_lens_read,
    mark_briefing_sources_read,
)
from app.services.briefing.refresh import enqueue_briefing_refresh_task
from app.services.briefing.state import ensure_briefing_state

router = APIRouter(tags=["briefing"])


def _briefing_etag(
    *,
    user_id: int,
    version: int,
    first_run_id: int = 0,
    first_run_revision: int = 0,
) -> str:
    """Return an opaque validator scoped to one user's briefing representation."""

    digest = hashlib.sha256(
        f"briefing:{user_id}:v{version}:o{first_run_id}.{first_run_revision}".encode()
    ).hexdigest()[:24]
    return f'W/"{digest}"'


def _briefing_cache_headers(*, etag: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "Vary": "Authorization",
    }


@router.get("/briefing", response_model=BriefingIndexResponse)
def get_index(
    response: Response,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> BriefingIndexResponse | Response:
    user_id = require_user_id(current_user)
    if if_none_match:
        validator = get_briefing_index_validator(db, user_id=user_id)
        validator_etag = _briefing_etag(
            user_id=user_id,
            version=validator.version,
            first_run_id=validator.first_run_id,
            first_run_revision=validator.first_run_revision,
        )
        if if_none_match == validator_etag:
            return Response(
                status_code=304,
                headers=_briefing_cache_headers(etag=validator_etag),
            )

    index = get_briefing_index(db, user_id=user_id)
    etag = _briefing_etag(
        user_id=user_id,
        version=index.version,
        first_run_id=index.first_run.run_id if index.first_run else 0,
        first_run_revision=index.first_run.revision if index.first_run else 0,
    )
    headers = _briefing_cache_headers(etag=etag)
    for name, value in headers.items():
        response.headers[name] = value
    return index


@router.get("/briefing/lenses/{lens_key}", response_model=BriefingLensResponse)
def get_lens(
    lens_key: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int | None, Query(ge=1, le=BRIEFING_LENS_PAGE_MAX)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
) -> BriefingLensResponse:
    try:
        lens = get_briefing_lens(
            db,
            user_id=require_user_id(current_user),
            lens_key=lens_key,
            limit=limit,
            cursor=cursor,
        )
    except InvalidBriefingLensCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StaleBriefingLensCursor as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if lens is None:
        raise HTTPException(status_code=404, detail="Briefing lens not found")
    return lens


@router.post("/briefing/read-marks", response_model=BriefingReadMarkResponse)
def mark_read(
    request: BriefingReadMarkRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingReadMarkResponse:
    result = mark_briefing_sources_read(
        db,
        user_id=require_user_id(current_user),
        source_keys=request.source_keys,
    )
    return BriefingReadMarkResponse(
        marked=result.marked,
        retired=result.retired,
        version=result.version,
    )


@router.post(
    "/briefing/lenses/{lens_key}/read-marks",
    response_model=BriefingReadMarkResponse,
)
def mark_lens_read(
    lens_key: str,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingReadMarkResponse:
    result = mark_briefing_lens_read(
        db,
        user_id=require_user_id(current_user),
        lens_key=lens_key,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Briefing lens not found")
    return BriefingReadMarkResponse(
        marked=result.marked,
        retired=result.retired,
        version=result.version,
    )


@router.post("/briefing/refresh", response_model=BriefingRefreshResponse)
def refresh(
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingRefreshResponse:
    user_id = require_user_id(current_user)
    state = ensure_briefing_state(db, user_id=user_id)
    enqueued = enqueue_briefing_refresh_task(db, user_id=user_id, mode="append", delay_seconds=0)
    return BriefingRefreshResponse(enqueued=enqueued, version=int(state.version or 0))


@router.post("/briefing/dig/search", response_model=BriefingDigSearchResponse)
def dig_search(
    request: BriefingDigSearchRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingDigSearchResponse:
    results, elapsed_ms = search_fragment(
        db,
        user_id=require_user_id(current_user),
        fragment=request.fragment,
    )
    return BriefingDigSearchResponse(results=results, elapsed_ms=elapsed_ms)


@router.post("/briefing/dig/summarize", response_model=BriefingDigSummarizeResponse)
def dig_summarize(
    request: BriefingDigSummarizeRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingDigSummarizeResponse:
    return summarize_fragment(
        db,
        user_id=require_user_id(current_user),
        fragment=request.fragment,
        passage_context=request.passage_context,
        results=request.results,
    )


@router.post("/briefing/narration", response_model=AudioEpisodeResponse)
def narration(
    request: BriefingNarrationRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    delivery: Annotated[AudioEpisodeDelivery, Query()] = "background",
) -> AudioEpisodeResponse:
    return create_or_reuse_legacy_briefing_narration(
        db,
        user_id=require_user_id(current_user),
        lens_key=request.lens_key,
        delivery=delivery,
    )


@router.post("/briefing/narrations", response_model=BriefingNarrationResponse)
def chaptered_narration(
    request: BriefingNarrationRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    delivery: Annotated[AudioEpisodeDelivery, Query()] = "background",
) -> BriefingNarrationResponse:
    return create_or_reuse_briefing_narration(
        db,
        user_id=require_user_id(current_user),
        lens_key=request.lens_key,
        delivery=delivery,
    )


@router.get(
    "/briefing/narrations/{episode_group_id}",
    response_model=BriefingNarrationResponse,
)
def narration_status(
    episode_group_id: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingNarrationResponse:
    narration_response = get_briefing_narration(
        db,
        user_id=require_user_id(current_user),
        episode_group_id=episode_group_id,
    )
    if narration_response is None:
        raise HTTPException(status_code=404, detail="Briefing narration not found")
    return narration_response
