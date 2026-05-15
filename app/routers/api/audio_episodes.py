"""On-demand audio episode endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session, get_session_factory
from app.core.deps import get_current_user, require_user_id
from app.models.api.audio_episodes import (
    AudioEpisodeDelivery,
    AudioEpisodeResponse,
)
from app.models.db.users import User
from app.services.audio_episodes import (
    audio_episode_file_path,
    commit_audio_episode_delivery,
    create_content_council_episode,
    create_fast_news_digest_episode,
    get_user_audio_episode,
    is_audio_episode_processing_stale,
    present_audio_episode,
    stream_audio_episode_chunks,
)

router = APIRouter()


@router.post(
    "/audio-episodes/fast-news",
    response_model=AudioEpisodeResponse,
    summary="Create an on-demand Fast Reads podcast episode",
)
def create_fast_news_audio_episode(
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    delivery: Annotated[AudioEpisodeDelivery, Query()] = "background",
) -> AudioEpisodeResponse:
    """Create or reuse a Fast Reads digest and enqueue generation."""

    user_id = require_user_id(current_user)
    episode = create_fast_news_digest_episode(db, user_id=user_id)
    return commit_audio_episode_delivery(db, episode, delivery=delivery)


@router.post(
    "/{content_id}/audio-episodes/council",
    response_model=AudioEpisodeResponse,
    summary="Create an on-demand long-form discussion podcast episode",
)
def create_content_council_audio_episode(
    content_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    delivery: Annotated[AudioEpisodeDelivery, Query()] = "background",
) -> AudioEpisodeResponse:
    """Create or reuse a long-form expert discussion and enqueue generation."""

    user_id = require_user_id(current_user)
    episode = create_content_council_episode(db, user_id=user_id, content_id=content_id)
    return commit_audio_episode_delivery(db, episode, delivery=delivery)


@router.get(
    "/audio-episodes/{audio_episode_id}",
    response_model=AudioEpisodeResponse,
    summary="Get audio episode generation status",
)
def get_audio_episode(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AudioEpisodeResponse:
    """Return one audio episode for polling."""

    user_id = require_user_id(current_user)
    episode = get_user_audio_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Audio episode not found")
    return present_audio_episode(episode)


@router.get(
    "/audio-episodes/{audio_episode_id}/audio",
    summary="Stream generated audio episode MP3",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
def get_audio_episode_audio(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """Return generated MP3 audio bytes for one completed episode."""

    user_id = require_user_id(current_user)
    episode = get_user_audio_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Audio episode not found")
    if episode.status != "completed":
        raise HTTPException(status_code=409, detail="Audio episode is not ready")

    path = audio_episode_file_path(episode)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(
        path,
        media_type=episode.audio_content_type or "audio/mpeg",
        filename=f"audio-episode-{audio_episode_id}.mp3",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/audio-episodes/{audio_episode_id}/stream",
    summary="Stream or generate an audio episode MP3",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
def stream_audio_episode(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Stream cached audio or generate the episode inline for low-latency playback."""

    user_id = require_user_id(current_user)
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        episode = get_user_audio_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
        if episode is None:
            raise HTTPException(status_code=404, detail="Audio episode not found")

        path = audio_episode_file_path(episode)
        if episode.status == "completed" and path is not None and path.exists():
            return FileResponse(
                path,
                media_type=episode.audio_content_type or "audio/mpeg",
                filename=f"audio-episode-{audio_episode_id}.mp3",
                headers={"Cache-Control": "no-store"},
            )

        if episode.status == "processing" and not is_audio_episode_processing_stale(episode):
            raise HTTPException(status_code=409, detail="Audio episode is already generating")

    return StreamingResponse(
        stream_audio_episode_chunks(audio_episode_id=audio_episode_id, user_id=user_id),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
