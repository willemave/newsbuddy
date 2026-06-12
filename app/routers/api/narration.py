"""Unified narration endpoint for content."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.models.api.content import NarrationResponse
from app.models.contracts import NarrationTargetType
from app.models.db.users import User
from app.queries import get_narration as get_narration_query
from app.services.voice.narration_tts import get_content_narration_tts_service

router = APIRouter()
logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _prefers_audio(request: Request) -> bool:
    """Return whether the client explicitly asked for audio bytes."""

    accept_header = request.headers.get("accept", "")
    return "audio/mpeg" in accept_header.lower()


@router.get(
    "/narration/{target_type}/{target_id}",
    response_model=NarrationResponse,
    summary="Get narration text or audio for a content target",
    responses={
        200: {
            "content": {
                "audio/mpeg": {},
            }
        }
    },
)
def get_narration(
    request: Request,
    target_type: Annotated[
        get_narration_query.NarrationTargetType,
        Path(description="Narration target type"),
    ],
    target_id: Annotated[int, Path(..., gt=0, description="Target identifier")],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> NarrationResponse | Response:
    """Return narration text or MP3 audio for one target."""
    started_at = time.perf_counter()
    user_id = require_user_id(current_user)
    wants_audio = _prefers_audio(request)
    logger.info(
        "Narration request started",
        extra={
            "component": "content_narration",
            "operation": "get_narration",
            "status": "started",
            "item_id": target_id,
            "user_id": user_id,
            "context_data": {
                "target_type": target_type,
                "wants_audio": wants_audio,
            },
        },
    )
    payload = get_narration_query.execute(
        db,
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
    )

    if wants_audio:
        tts_started_at = time.perf_counter()
        try:
            audio_bytes = get_content_narration_tts_service().synthesize_mp3(
                text=payload.narration_text,
                item_id=payload.target_id,
                user_id=user_id,
            )
        except ValueError as exc:
            logger.info(
                "Narration audio rejected",
                extra={
                    "component": "content_narration",
                    "operation": "get_narration_audio",
                    "status": "rejected",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": payload.target_id,
                    "user_id": user_id,
                    "context_data": {
                        "target_type": payload.target_type,
                        "reason": str(exc),
                    },
                },
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.info(
                "Narration audio failed",
                extra={
                    "component": "content_narration",
                    "operation": "get_narration_audio",
                    "status": "failed",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": payload.target_id,
                    "user_id": user_id,
                    "context_data": {
                        "target_type": payload.target_type,
                        "error": str(exc),
                    },
                },
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        logger.info(
            "Narration audio response ready",
            extra={
                "component": "content_narration",
                "operation": "get_narration_audio",
                "status": "ready",
                "duration_ms": _duration_ms(started_at),
                "item_id": payload.target_id,
                "user_id": user_id,
                "context_data": {
                    "target_type": payload.target_type,
                    "tts_duration_ms": _duration_ms(tts_started_at),
                    "text_chars": len(payload.narration_text),
                    "audio_bytes": len(audio_bytes),
                },
            },
        )
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="{payload.audio_filename}"',
            },
        )

    logger.info(
        "Narration text response ready",
        extra={
            "component": "content_narration",
            "operation": "get_narration_text",
            "status": "ready",
            "duration_ms": _duration_ms(started_at),
            "item_id": payload.target_id,
            "user_id": user_id,
            "context_data": {
                "target_type": payload.target_type,
                "text_chars": len(payload.narration_text),
            },
        },
    )
    return NarrationResponse(
        target_type=NarrationTargetType(payload.target_type),
        target_id=payload.target_id,
        title=payload.title,
        narration_text=payload.narration_text,
    )
