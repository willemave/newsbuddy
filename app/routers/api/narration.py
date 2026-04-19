"""Unified narration endpoint for content."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_readonly_db_session
from app.core.deps import get_current_user
from app.models.api.common import NarrationResponse
from app.models.user import User
from app.queries import get_narration as get_narration_query
from app.services.voice.narration_tts import get_digest_narration_tts_service

router = APIRouter()


def _require_user_id(current_user: User) -> int:
    user_id = current_user.id
    if user_id is None:
        raise ValueError("Authenticated user is missing an id")
    return user_id


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
    payload = get_narration_query.execute(
        db,
        user_id=_require_user_id(current_user),
        target_type=target_type,
        target_id=target_id,
    )

    if _prefers_audio(request):
        try:
            audio_bytes = get_digest_narration_tts_service().synthesize_mp3(
                text=payload.narration_text,
                item_id=payload.target_id,
                user_id=current_user.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="{payload.audio_filename}"',
            },
        )

    return NarrationResponse(
        target_type=payload.target_type,
        target_id=payload.target_id,
        title=payload.title,
        narration_text=payload.narration_text,
    )
