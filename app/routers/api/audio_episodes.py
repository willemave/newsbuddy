"""On-demand audio episode endpoints."""

from __future__ import annotations

import time
from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session, get_session_factory
from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.models.api.audio_episodes import (
    AudioEpisodeDelivery,
    AudioEpisodeResponse,
    AudioEpisodeShareResponse,
    CustomNarrationCreateRequest,
)
from app.models.db import AudioEpisode
from app.models.db.users import User
from app.services.audio_episode_tokens import (
    AudioEpisodeShareError,
    disable_audio_episode_share,
    enable_audio_episode_share,
    get_audio_episode_by_valid_share_token,
)
from app.services.audio_episodes import (
    audio_episode_file_path,
    commit_audio_episode_delivery,
    create_content_council_episode,
    create_custom_narration_episode,
    create_fast_news_digest_episode,
    follow_audio_episode_stream_chunks,
    get_user_audio_episode,
    is_audio_episode_processing_stale,
    list_custom_narration_episodes,
    mark_audio_episode_sources_read_on_play,
    present_audio_episode,
    stream_audio_episode_chunks,
)

router = APIRouter()
public_router = APIRouter(tags=["audio"])
logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


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


@router.post(
    "/audio-episodes/custom-narrations",
    response_model=AudioEpisodeResponse,
    summary="Create one combined custom narration from selected long-form content",
)
def create_custom_narration_audio_episode(
    request: CustomNarrationCreateRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    delivery: Annotated[AudioEpisodeDelivery, Query()] = "background",
) -> AudioEpisodeResponse:
    """Create or reuse a multi-source custom narration and enqueue generation."""

    user_id = require_user_id(current_user)
    episode = create_custom_narration_episode(
        db,
        user_id=user_id,
        content_ids=request.content_ids,
        news_item_ids=request.news_item_ids,
        title=request.title,
        mark_source_content_read_on_play=request.mark_source_content_read_on_play,
    )
    return commit_audio_episode_delivery(db, episode, delivery=delivery)


@router.get(
    "/audio-episodes/custom-narrations",
    response_model=list[AudioEpisodeResponse],
    summary="List custom narrations for the current user",
)
def list_custom_narration_audio_episodes(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[AudioEpisodeResponse]:
    """Return recent custom narration episodes."""

    user_id = require_user_id(current_user)
    episodes = list_custom_narration_episodes(db, user_id=user_id, limit=limit)
    return [present_audio_episode(episode) for episode in episodes]


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


@router.post(
    "/audio-episodes/{audio_episode_id}/share",
    response_model=AudioEpisodeShareResponse,
    summary="Enable public sharing for a completed custom narration",
)
def enable_audio_episode_public_share(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AudioEpisodeShareResponse:
    """Enable public page and audio links for a completed custom narration."""

    try:
        token = enable_audio_episode_share(
            db,
            user_id=require_user_id(current_user),
            audio_episode_id=audio_episode_id,
        )
    except AudioEpisodeShareError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _audio_episode_share_response(request, token=token)


@router.delete(
    "/audio-episodes/{audio_episode_id}/share",
    response_model=AudioEpisodeShareResponse,
    summary="Disable public sharing for a custom narration",
)
def disable_audio_episode_public_share(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AudioEpisodeShareResponse:
    """Disable public page and audio links for a narration."""

    try:
        disable_audio_episode_share(
            db,
            user_id=require_user_id(current_user),
            audio_episode_id=audio_episode_id,
        )
    except AudioEpisodeShareError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AudioEpisodeShareResponse(share_enabled=False)


@router.get(
    "/audio-episodes/{audio_episode_id}/audio",
    summary="Stream generated audio episode MP3",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
def get_audio_episode_audio(
    audio_episode_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """Return generated MP3 audio bytes for one completed episode."""

    started_at = time.perf_counter()
    user_id = require_user_id(current_user)
    episode = get_user_audio_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Audio episode not found")
    if episode.status != "completed":
        raise HTTPException(status_code=409, detail="Audio episode is not ready")

    path = audio_episode_file_path(episode)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    mark_audio_episode_sources_read_on_play(db, episode=episode)

    logger.info(
        "Audio episode file response ready",
        extra={
            "component": "audio_episodes",
            "operation": "audio_file",
            "status": "ready",
            "duration_ms": _duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
            "context_data": {
                "kind": episode.kind,
                "audio_bytes": path.stat().st_size if path.exists() else None,
            },
        },
    )
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

    started_at = time.perf_counter()
    user_id = require_user_id(current_user)
    logger.info(
        "Audio episode stream requested",
        extra={
            "component": "audio_episodes",
            "operation": "stream_route",
            "status": "requested",
            "item_id": audio_episode_id,
            "user_id": user_id,
        },
    )
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        episode = get_user_audio_episode(db, user_id=user_id, audio_episode_id=audio_episode_id)
        if episode is None:
            raise HTTPException(status_code=404, detail="Audio episode not found")

        mark_audio_episode_sources_read_on_play(db, episode=episode)

        path = audio_episode_file_path(episode)
        if episode.status == "completed" and path is not None and path.exists():
            logger.info(
                "Audio episode stream serving cached file",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream_route",
                    "status": "cached",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": episode.kind,
                        "audio_bytes": path.stat().st_size,
                    },
                },
            )
            return FileResponse(
                path,
                media_type=episode.audio_content_type or "audio/mpeg",
                filename=f"audio-episode-{audio_episode_id}.mp3",
                headers={"Cache-Control": "no-store"},
            )

        if episode.status == "processing" and not is_audio_episode_processing_stale(episode):
            logger.info(
                "Audio episode stream following active generator",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream_route",
                    "status": "following_active_generator",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {"kind": episode.kind},
                },
            )
            return StreamingResponse(
                follow_audio_episode_stream_chunks(
                    audio_episode_id=audio_episode_id,
                    user_id=user_id,
                ),
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

    logger.info(
        "Audio episode streaming response opened",
        extra={
            "component": "audio_episodes",
            "operation": "stream_route",
            "status": "streaming_response",
            "duration_ms": _duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
        },
    )
    return StreamingResponse(
        stream_audio_episode_chunks(audio_episode_id=audio_episode_id, user_id=user_id),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@public_router.get(
    "/audio/share/{token}/",
    name="serve_shared_audio_episode",
    include_in_schema=False,
)
def serve_shared_audio_episode(
    token: str,
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> HTMLResponse:
    """Serve a public audio episode landing page."""

    try:
        episode = get_audio_episode_by_valid_share_token(db, token=token)
        path = audio_episode_file_path(episode)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")
        audio_url = str(request.url_for("serve_shared_audio_episode_audio", token=token))
        return HTMLResponse(
            _shared_audio_episode_html(episode, audio_url=audio_url),
            headers={"Cache-Control": "no-store"},
        )
    except AudioEpisodeShareError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@public_router.get(
    "/audio/share/{token}/audio",
    name="serve_shared_audio_episode_audio",
    include_in_schema=False,
)
def serve_shared_audio_episode_audio(
    token: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> FileResponse:
    """Serve public MP3 bytes for a shared audio episode."""

    try:
        episode = get_audio_episode_by_valid_share_token(db, token=token)
    except AudioEpisodeShareError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    path = audio_episode_file_path(episode)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    episode_id = getattr(episode, "id", None) or "shared"
    return FileResponse(
        path,
        media_type=episode.audio_content_type or "audio/mpeg",
        filename=f"audio-episode-{episode_id}.mp3",
        headers={"Cache-Control": "no-store"},
    )


def _audio_episode_share_response(
    request: Request,
    *,
    token: str,
) -> AudioEpisodeShareResponse:
    return AudioEpisodeShareResponse(
        share_enabled=True,
        share_page_url=str(request.url_for("serve_shared_audio_episode", token=token)),
        share_audio_url=str(request.url_for("serve_shared_audio_episode_audio", token=token)),
    )


def _shared_audio_episode_html(episode: AudioEpisode, *, audio_url: str) -> str:
    title = escape(str(episode.title or "Shared narration"))
    audio_href = escape(audio_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f4ef;
      color: #201d1a;
    }}
    main {{
      width: min(92vw, 680px);
      padding: 32px 24px;
    }}
    h1 {{
      margin: 0 0 18px;
      font-size: clamp(1.75rem, 4vw, 3rem);
      line-height: 1.05;
    }}
    audio {{
      width: 100%;
      margin: 8px 0 18px;
    }}
    a {{
      color: #8b3a2f;
      font-weight: 650;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <audio controls preload="metadata" src="{audio_href}"></audio>
    <p><a href="{audio_href}">Open direct audio link</a></p>
  </main>
</body>
</html>"""
