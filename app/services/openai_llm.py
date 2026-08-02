"""OpenAI services (summarization via pydantic-ai and audio transcription)."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.prompt_library import load_prompt
from app.services.vendor_costs import record_vendor_usage_out_of_band

logger = get_logger(__name__)
settings = get_settings()

# Transcription constants
MAX_FILE_SIZE_MB = 25
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
CHUNK_DURATION_SECONDS = 10 * 60  # 10 minutes in seconds


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


class OpenAITranscriptionService:
    """OpenAI service for completed-file audio transcription."""

    def __init__(self):
        openai_api_key = getattr(settings, "openai_api_key", None)
        if not openai_api_key:
            raise ValueError("OpenAI API key is required for transcription service")

        self.client = OpenAI(api_key=openai_api_key)
        self.model_name = "gpt-transcribe"
        logger.info("Initialized OpenAI provider for transcription")

    def _get_audio_format(self, file_path: Path) -> str:
        """Determine audio format from file extension."""
        extension = file_path.suffix.lower()
        format_map = {
            ".mp3": "mp3",
            ".mp4": "mp4",
            ".m4a": "mp4",
            ".wav": "wav",
            ".webm": "webm",
            ".ogg": "ogg",
            ".opus": "opus",
            ".flac": "flac",
        }
        return format_map.get(extension, "mp3")

    def _check_file_size(self, file_path: Path) -> bool:
        """Check if file is within size limit."""
        file_size = os.path.getsize(file_path)
        return file_size <= MAX_FILE_SIZE_BYTES

    def _get_audio_duration(self, file_path: Path) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-i",
                str(file_path),
                "-show_entries",
                "format=duration",
                "-v",
                "quiet",
                "-of",
                "csv=p=0",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError) as e:
            logger.error(f"Failed to get audio duration: {e}")
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            estimated_duration = file_size_mb * 60  # Very rough estimate
            logger.warning(f"Using estimated duration: {estimated_duration:.1f} seconds")
            return estimated_duration

    def _split_audio_file_ffmpeg(self, file_path: Path) -> list[Path]:
        """Split large audio file into chunks using ffmpeg directly."""
        logger.info(f"Splitting large audio file using ffmpeg: {file_path}")

        duration = self._get_audio_duration(file_path)
        num_chunks = int((duration + CHUNK_DURATION_SECONDS - 1) // CHUNK_DURATION_SECONDS)

        logger.info(f"Audio duration: {duration:.1f}s, will split into {num_chunks} chunks")

        temp_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_"))
        chunk_paths = []
        audio_format = self._get_audio_format(file_path)

        try:
            for i in range(num_chunks):
                start_time = i * CHUNK_DURATION_SECONDS
                chunk_filename = f"chunk_{i:03d}.{audio_format}"
                chunk_path = temp_dir / chunk_filename

                cmd = [
                    "ffmpeg",
                    "-i",
                    str(file_path),
                    "-ss",
                    str(start_time),
                    "-t",
                    str(CHUNK_DURATION_SECONDS),
                    "-acodec",
                    "copy",
                    "-y",
                    str(chunk_path),
                ]

                logger.info(f"Creating chunk {i + 1}/{num_chunks}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"ffmpeg failed: {result.stderr}")

                chunk_paths.append(chunk_path)
                if not chunk_path.exists() or os.path.getsize(chunk_path) == 0:
                    raise RuntimeError(f"Failed to create chunk: {chunk_path}")

                logger.info(
                    f"Created chunk {i + 1}/{num_chunks}: "
                    f"{os.path.getsize(chunk_path) / (1024 * 1024):.1f}MB"
                )

            return chunk_paths

        except Exception as e:
            for chunk_path in chunk_paths:
                if chunk_path.exists():
                    chunk_path.unlink()
            if temp_dir.exists():
                temp_dir.rmdir()
            raise e

    def _check_ffmpeg_available(self) -> bool:
        """Check if ffmpeg is available on the system."""
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_transcription_prompt(self, file_path: Path) -> str:
        """Generate a contextual prompt based on the file name and podcast context."""
        file_name = file_path.stem
        prompt = load_prompt("audio/transcription#default")

        if "interview" in file_name.lower():
            prompt = load_prompt("audio/transcription#interview")
        elif "tech" in file_name.lower() or "ai" in file_name.lower():
            prompt = load_prompt("audio/transcription#tech")
        elif "news" in file_name.lower():
            prompt = load_prompt("audio/transcription#news")
        elif any(term in file_name.lower() for term in ["bg2", "bill", "gurley", "gerstner"]):
            prompt = load_prompt("audio/transcription#bg2")

        return prompt

    def _get_transcription_keywords(self, file_path: Path) -> list[str]:
        """Return literal terms that are likely to be spoken in known recordings."""
        file_name = file_path.stem.lower()
        if any(term in file_name for term in ["bg2", "bill", "gurley", "gerstner"]):
            return ["BG2", "Bill Gurley", "Brad Gerstner"]
        return []

    @staticmethod
    def _get_detected_language(transcription: object) -> str | None:
        """Return the first language detected by GPT-Transcribe, when available."""
        languages = getattr(transcription, "languages", None) or []
        if not languages:
            return None

        code = getattr(languages[0], "code", None)
        return code if isinstance(code, str) and code else None

    def _record_transcription_usage(
        self,
        *,
        file_path: Path,
        language: str | None,
        prompt: str,
        user_id: int | None,
        chunk_count: int = 1,
    ) -> None:
        """Persist one transcription usage record."""
        record_vendor_usage_out_of_band(
            provider="openai",
            model=self.model_name,
            feature="transcription",
            operation="transcription.openai",
            source="api",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "file_name": file_path.name,
                "audio_format": self._get_audio_format(file_path),
                "audio_size_bytes": os.path.getsize(file_path),
                "language": language,
                "chunk_count": chunk_count,
                "prompt_chars": len(prompt),
            },
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _transcribe_single_file(
        self,
        file_path: Path,
        prompt: str,
        keywords: list[str],
    ) -> tuple[str, str | None]:
        """Transcribe a single audio file."""
        with open(file_path, "rb") as audio_file:
            started_at = time.perf_counter()
            file_size = os.path.getsize(file_path)
            logger.info(
                "Sending audio file to OpenAI for transcription",
                extra={
                    "component": "openai_transcription",
                    "operation": "provider_transcribe",
                    "status": "started",
                    "provider": "openai",
                    "model": self.model_name,
                    "context_data": {
                        "file_name": file_path.name,
                        "audio_size_bytes": file_size,
                        "prompt_chars": len(prompt),
                    },
                },
            )

            if keywords:
                transcription = self.client.audio.transcriptions.create(
                    model=self.model_name,
                    file=audio_file,
                    response_format="json",
                    prompt=prompt,
                    keywords=keywords,
                )
            else:
                transcription = self.client.audio.transcriptions.create(
                    model=self.model_name,
                    file=audio_file,
                    response_format="json",
                    prompt=prompt,
                )

            transcript = transcription.text
            language = self._get_detected_language(transcription)

            logger.info(
                "Successfully transcribed audio",
                extra={
                    "component": "openai_transcription",
                    "operation": "provider_transcribe",
                    "status": "completed",
                    "duration_ms": _duration_ms(started_at),
                    "provider": "openai",
                    "model": self.model_name,
                    "context_data": {
                        "file_name": file_path.name,
                        "audio_size_bytes": file_size,
                        "transcript_chars": len(transcript),
                        "language": language,
                    },
                },
            )

            return transcript, language

    def transcribe_audio(
        self,
        audio_file_path: Path,
        *,
        user_id: int | None = None,
        context_prompt: str | None = None,
        context_keywords: list[str] | None = None,
    ) -> tuple[str, str | None]:
        """Transcribe a completed audio file with GPT-Transcribe."""
        started_at = time.perf_counter()
        try:
            prompt = context_prompt or self._get_transcription_prompt(audio_file_path)
            keywords = (
                context_keywords
                if context_keywords is not None
                else self._get_transcription_keywords(audio_file_path)
            )
            audio_size_bytes = os.path.getsize(audio_file_path)
            logger.info(
                "OpenAI audio transcription started",
                extra={
                    "component": "openai_transcription",
                    "operation": "transcribe_audio",
                    "status": "started",
                    "user_id": user_id,
                    "provider": "openai",
                    "model": self.model_name,
                    "context_data": {
                        "file_name": audio_file_path.name,
                        "audio_size_bytes": audio_size_bytes,
                        "prompt_chars": len(prompt),
                    },
                },
            )

            if self._check_file_size(audio_file_path):
                transcript, language = self._transcribe_single_file(
                    audio_file_path,
                    prompt,
                    keywords,
                )
                self._record_transcription_usage(
                    file_path=audio_file_path,
                    language=language,
                    prompt=prompt,
                    user_id=user_id,
                )
                logger.info(
                    "OpenAI audio transcription completed",
                    extra={
                        "component": "openai_transcription",
                        "operation": "transcribe_audio",
                        "status": "completed",
                        "duration_ms": _duration_ms(started_at),
                        "user_id": user_id,
                        "provider": "openai",
                        "model": self.model_name,
                        "context_data": {
                            "file_name": audio_file_path.name,
                            "audio_size_bytes": audio_size_bytes,
                            "chunk_count": 1,
                            "transcript_chars": len(transcript),
                            "language": language,
                        },
                    },
                )
                return transcript, language

            logger.info(
                "Audio file exceeds transcription size limit, splitting into chunks",
                extra={
                    "component": "openai_transcription",
                    "operation": "transcribe_audio",
                    "status": "splitting",
                    "user_id": user_id,
                    "provider": "openai",
                    "model": self.model_name,
                    "context_data": {
                        "file_name": audio_file_path.name,
                        "audio_size_bytes": audio_size_bytes,
                        "max_file_size_bytes": MAX_FILE_SIZE_BYTES,
                    },
                },
            )

            if not self._check_ffmpeg_available():
                raise RuntimeError(
                    "Audio file exceeds 25MB limit but ffmpeg is not available for splitting. "
                    "Please install ffmpeg (e.g., 'brew install ffmpeg' on macOS) "
                    "or use audio files smaller than 25MB."
                )

            split_started_at = time.perf_counter()
            chunk_paths = self._split_audio_file_ffmpeg(audio_file_path)
            split_duration_ms = _duration_ms(split_started_at)

            try:
                transcripts = []
                detected_language = None

                for i, chunk_path in enumerate(chunk_paths):
                    chunk_started_at = time.perf_counter()
                    logger.info(
                        "Transcribing audio chunk",
                        extra={
                            "component": "openai_transcription",
                            "operation": "transcribe_audio_chunk",
                            "status": "started",
                            "user_id": user_id,
                            "provider": "openai",
                            "model": self.model_name,
                            "context_data": {
                                "file_name": audio_file_path.name,
                                "chunk_index": i + 1,
                                "chunk_count": len(chunk_paths),
                                "chunk_size_bytes": os.path.getsize(chunk_path),
                            },
                        },
                    )

                    chunk_prompt = prompt
                    if i > 0:
                        chunk_prompt += f" {load_prompt('audio/transcription#continuation_suffix')}"

                    chunk_transcript, chunk_language = self._transcribe_single_file(
                        chunk_path,
                        chunk_prompt,
                        keywords,
                    )

                    transcripts.append(chunk_transcript)
                    if detected_language is None and chunk_language:
                        detected_language = chunk_language
                    logger.info(
                        "Transcribed audio chunk",
                        extra={
                            "component": "openai_transcription",
                            "operation": "transcribe_audio_chunk",
                            "status": "completed",
                            "duration_ms": _duration_ms(chunk_started_at),
                            "user_id": user_id,
                            "provider": "openai",
                            "model": self.model_name,
                            "context_data": {
                                "file_name": audio_file_path.name,
                                "chunk_index": i + 1,
                                "chunk_count": len(chunk_paths),
                                "transcript_chars": len(chunk_transcript),
                                "language": chunk_language,
                            },
                        },
                    )

                full_transcript = " ".join(transcripts)

                logger.info(
                    "OpenAI chunked audio transcription completed",
                    extra={
                        "component": "openai_transcription",
                        "operation": "transcribe_audio",
                        "status": "completed",
                        "duration_ms": _duration_ms(started_at),
                        "user_id": user_id,
                        "provider": "openai",
                        "model": self.model_name,
                        "context_data": {
                            "file_name": audio_file_path.name,
                            "audio_size_bytes": audio_size_bytes,
                            "chunk_count": len(chunk_paths),
                            "split_duration_ms": split_duration_ms,
                            "transcript_chars": len(full_transcript),
                            "language": detected_language,
                        },
                    },
                )

                self._record_transcription_usage(
                    file_path=audio_file_path,
                    language=detected_language,
                    prompt=prompt,
                    user_id=user_id,
                    chunk_count=len(chunk_paths),
                )
                return full_transcript, detected_language

            finally:
                for chunk_path in chunk_paths:
                    if chunk_path.exists():
                        chunk_path.unlink()

                if chunk_paths:
                    temp_dir = chunk_paths[0].parent
                    if temp_dir.exists() and temp_dir.name.startswith("audio_chunks_"):
                        with contextlib.suppress(OSError):
                            temp_dir.rmdir()

        except Exception as e:  # noqa: BLE001
            logger.error(
                "Error transcribing audio with OpenAI",
                extra={
                    "component": "openai_transcription",
                    "operation": "transcribe_audio",
                    "duration_ms": _duration_ms(started_at),
                    "user_id": user_id,
                    "provider": "openai",
                    "model": self.model_name,
                    "context_data": {
                        "file_name": audio_file_path.name,
                        "error": str(e),
                    },
                },
            )
            raise

    def transcribe_audio_from_buffer(
        self,
        audio_buffer: BinaryIO,
        filename: str,
        *,
        user_id: int | None = None,
    ) -> tuple[str, str | None]:
        """Persist and transcribe a completed audio upload with GPT-Transcribe."""
        started_at = time.perf_counter()
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=Path(filename).suffix, delete=False
            ) as tmp_file:
                tmp_file.write(audio_buffer.read())
                tmp_path = Path(tmp_file.name)
            logger.info(
                "Audio transcription buffer persisted",
                extra={
                    "component": "openai_transcription",
                    "operation": "transcribe_audio_from_buffer",
                    "status": "buffer_persisted",
                    "duration_ms": _duration_ms(started_at),
                    "user_id": user_id,
                    "context_data": {
                        "filename": filename,
                        "audio_size_bytes": tmp_path.stat().st_size,
                    },
                },
            )

            try:
                transcript, language = self.transcribe_audio(
                    tmp_path,
                    user_id=user_id,
                    context_prompt=load_prompt("audio/transcription#voice_dictation"),
                    context_keywords=[],
                )
                logger.info(
                    "Audio transcription buffer completed",
                    extra={
                        "component": "openai_transcription",
                        "operation": "transcribe_audio_from_buffer",
                        "status": "completed",
                        "duration_ms": _duration_ms(started_at),
                        "user_id": user_id,
                        "context_data": {
                            "filename": filename,
                            "transcript_chars": len(transcript),
                            "language": language,
                        },
                    },
                )
                return transcript, language
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

        except Exception as e:  # noqa: BLE001
            logger.error(
                "Error transcribing audio buffer with OpenAI",
                extra={
                    "component": "openai_transcription",
                    "operation": "transcribe_audio_from_buffer",
                    "duration_ms": _duration_ms(started_at),
                    "user_id": user_id,
                    "context_data": {
                        "filename": filename,
                        "tmp_path_created": tmp_path is not None,
                        "error": str(e),
                    },
                },
            )
            raise


_openai_transcription_service: OpenAITranscriptionService | None = None


def get_openai_transcription_service() -> OpenAITranscriptionService:
    """Get the global OpenAI transcription service instance."""
    global _openai_transcription_service
    if _openai_transcription_service is None:
        _openai_transcription_service = OpenAITranscriptionService()
    return _openai_transcription_service
