"""OpenAI-related endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.models.user import User
from app.routers.api.models import RealtimeTokenResponse
from app.services import openai_realtime
from app.services.openai_llm import get_openai_transcription_service

router = APIRouter(prefix="/openai", tags=["openai"])


class AudioTranscriptionResponse(BaseModel):
    """Transcription payload returned for uploaded audio."""

    transcript: str
    language: str | None = None


@router.post(
    "/realtime/token",
    response_model=RealtimeTokenResponse,
    summary="Create OpenAI Realtime token",
)
async def create_realtime_token(
    current_user: Annotated[User, Depends(get_current_user)],
) -> RealtimeTokenResponse:
    """Create a short-lived token for OpenAI Realtime sessions."""
    _ = current_user
    try:
        token, expires_at, model = openai_realtime.create_transcription_session_token()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RealtimeTokenResponse(
        token=token,
        expires_at=expires_at,
        model=model,
        session_type="transcription",
    )


@router.post(
    "/transcriptions",
    response_model=AudioTranscriptionResponse,
    summary="Transcribe uploaded audio via the backend",
)
def transcribe_audio(
    current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> AudioTranscriptionResponse:
    """Transcribe uploaded audio without exposing provider API keys to the client."""
    _ = current_user
    filename = file.filename or "audio.m4a"
    try:
        transcript, language = get_openai_transcription_service().transcribe_audio_from_buffer(
            file.file,
            filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AudioTranscriptionResponse(transcript=transcript, language=language)
