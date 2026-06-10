"""OpenAI-related endpoints."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.deps import get_current_user, require_user_id
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.api.openai import AudioTranscriptionHealthResponse, AudioTranscriptionResponse
from app.models.db.users import User
from app.services.openai_llm import get_openai_transcription_service

router = APIRouter(prefix="/openai", tags=["openai"])
logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


@router.get(
    "/transcriptions/health",
    response_model=AudioTranscriptionHealthResponse,
    summary="Check uploaded-audio transcription availability",
)
def transcription_health(
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
    started_at = time.perf_counter()
    user_id = require_user_id(current_user)
    filename = file.filename or "audio.m4a"
    logger.info(
        "Audio transcription upload received",
        extra={
            "component": "openai_transcription",
            "operation": "upload_transcription",
            "status": "started",
            "user_id": user_id,
            "context_data": {
                "filename": filename,
                "content_type": file.content_type,
            },
        },
    )
    try:
        transcript, language = get_openai_transcription_service().transcribe_audio_from_buffer(
            file.file,
            filename,
            user_id=user_id,
        )
    except ValueError as exc:
        logger.info(
            "Audio transcription upload rejected",
            extra={
                "component": "openai_transcription",
                "operation": "upload_transcription",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "user_id": user_id,
                "context_data": {"filename": filename, "reason": str(exc)},
            },
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.info(
            "Audio transcription upload failed",
            extra={
                "component": "openai_transcription",
                "operation": "upload_transcription",
                "status": "failed",
                "duration_ms": _duration_ms(started_at),
                "user_id": user_id,
                "context_data": {"filename": filename, "error": str(exc)},
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Audio transcription upload failed",
            extra={
                "component": "openai_transcription",
                "operation": "upload_transcription",
                "duration_ms": _duration_ms(started_at),
                "user_id": user_id,
                "context_data": {"filename": filename, "error": str(exc)},
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info(
        "Audio transcription upload completed",
        extra={
            "component": "openai_transcription",
            "operation": "upload_transcription",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "user_id": user_id,
            "context_data": {
                "filename": filename,
                "language": language,
                "transcript_chars": len(transcript),
            },
        },
    )
    return AudioTranscriptionResponse(transcript=transcript, language=language)
