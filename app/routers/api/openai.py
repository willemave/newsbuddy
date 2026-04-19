"""OpenAI-related endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.deps import get_current_user
from app.core.settings import get_settings
from app.models.api.openai import AudioTranscriptionHealthResponse, AudioTranscriptionResponse
from app.models.user import User
from app.services.openai_llm import get_openai_transcription_service

router = APIRouter(prefix="/openai", tags=["openai"])


@router.get(
    "/transcriptions/health",
    response_model=AudioTranscriptionHealthResponse,
    summary="Check uploaded-audio transcription availability",
)
async def transcription_health(
    current_user: Annotated[User, Depends(get_current_user)],
) -> AudioTranscriptionHealthResponse:
    """Return whether backend-managed audio transcription is configured."""
    _ = current_user
    settings = get_settings()
    return AudioTranscriptionHealthResponse(available=bool(settings.openai_api_key))


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
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AudioTranscriptionResponse(transcript=transcript, language=language)
