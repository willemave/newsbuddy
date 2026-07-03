from __future__ import annotations

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
    BriefingReadMarkRequest,
    BriefingReadMarkResponse,
    BriefingRefreshResponse,
)
from app.models.db.users import User
from app.services.briefing.dig import search_fragment, summarize_fragment
from app.services.briefing.narration import create_or_reuse_briefing_narration
from app.services.briefing.presentation import get_briefing_index, get_briefing_lens
from app.services.briefing.read_marks import mark_briefing_sources_read
from app.services.briefing.refresh import enqueue_briefing_refresh_task, ensure_state

router = APIRouter(tags=["briefing"])


@router.get("/briefing", response_model=BriefingIndexResponse)
def get_index(
    response: Response,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> BriefingIndexResponse | Response:
    user_id = require_user_id(current_user)
    index = get_briefing_index(db, user_id=user_id)
    etag = f'W/"v{index.version}"'
    response.headers["ETag"] = etag
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return index


@router.get("/briefing/lenses/{lens_key}", response_model=BriefingLensResponse)
def get_lens(
    lens_key: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingLensResponse:
    lens = get_briefing_lens(db, user_id=require_user_id(current_user), lens_key=lens_key)
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
    return BriefingReadMarkResponse(marked=result.marked, version=result.version)


@router.post("/briefing/refresh", response_model=BriefingRefreshResponse)
def refresh(
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BriefingRefreshResponse:
    user_id = require_user_id(current_user)
    state = ensure_state(db, user_id=user_id)
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
    return create_or_reuse_briefing_narration(
        db,
        user_id=require_user_id(current_user),
        lens_key=request.lens_key,
        delivery=delivery,
    )
