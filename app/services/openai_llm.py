"""OpenAI services (summarization via pydantic-ai, transcription via Whisper)."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.services.vendor_costs import record_vendor_usage_out_of_band

try:
    from langfuse.openai import OpenAI
except Exception:  # noqa: BLE001
    from openai import OpenAI

logger = get_logger(__name__)
settings = get_settings()

# Summarization defaults
SUMMARY_MODEL_SPEC = "gpt-5.4-mini"

# Transcription constants
MAX_FILE_SIZE_MB = 25
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
CHUNK_DURATION_SECONDS = 10 * 60  # 10 minutes in seconds


class OpenAISummarizationService(ContentSummarizer):
    """OpenAI summarization wrapper using the shared ContentSummarizer."""

    def __init__(self) -> None:
        if not getattr(settings, "openai_api_key", None):
            raise ValueError("OpenAI API key is required for LLM service")
        super().__init__(provider_hint="openai", model_hint=SUMMARY_MODEL_SPEC)
        logger.info("Initialized OpenAI summarization service (pydantic-ai)")


class OpenAITranscriptionService:
    """OpenAI service for audio transcription using Whisper API."""

    def __init__(self):
        openai_api_key = getattr(settings, "openai_api_key", None)
        if not openai_api_key:
            raise ValueError("OpenAI API key is required for transcription service")

        self.client = OpenAI(api_key=openai_api_key)
        self.model_name = "gpt-4o-transcribe"
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
        prompt = (
            "This is a podcast episode. Please transcribe accurately, "
            "including speaker names when mentioned."
        )

        if "interview" in file_name.lower():
            prompt = (
                "This is a podcast interview. Please transcribe accurately, "
                "noting different speakers."
            )
        elif "tech" in file_name.lower() or "ai" in file_name.lower():
            prompt = (
                "This is a technology podcast discussing AI, software, and tech innovations. "
                "Include technical terms accurately."
            )
        elif "news" in file_name.lower():
            prompt = (
                "This is a news podcast. Please transcribe accurately, "
                "including proper names and places."
            )
        elif any(term in file_name.lower() for term in ["bg2", "bill", "gurley", "gerstner"]):
            prompt = (
                "This is the BG2 podcast with Bill Gurley and Brad Gerstner discussing "
                "technology, venture capital, and market trends."
            )

        return prompt

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
    def _transcribe_single_file(self, file_path: Path, prompt: str) -> tuple[str, str | None]:
        """Transcribe a single audio file."""
        with open(file_path, "rb") as audio_file:
            logger.info(f"Sending audio file to OpenAI for transcription: {file_path}")

            with langfuse_trace_context(
                trace_name="queue.transcribe.audio",
                metadata={
                    "source": "queue",
                    "model_spec": f"openai:{self.model_name}",
                    "file_name": file_path.name,
                },
                tags=["queue", "transcription"],
            ):
                transcription = self.client.audio.transcriptions.create(
                    model=self.model_name, file=audio_file, response_format="json", prompt=prompt
                )

            transcript = transcription.text
            language = getattr(transcription, "language", None)

            logger.info(
                f"Successfully transcribed audio. "
                f"Length: {len(transcript)} chars, Language: {language}"
            )

            return transcript, language

    def transcribe_audio(
        self,
        audio_file_path: Path,
        *,
        user_id: int | None = None,
    ) -> tuple[str, str | None]:
        """Transcribe audio file using OpenAI Whisper API."""
        try:
            prompt = self._get_transcription_prompt(audio_file_path)
            logger.info(f"Using transcription prompt: {prompt}")

            if self._check_file_size(audio_file_path):
                transcript, language = self._transcribe_single_file(audio_file_path, prompt)
                self._record_transcription_usage(
                    file_path=audio_file_path,
                    language=language,
                    prompt=prompt,
                    user_id=user_id,
                )
                return transcript, language

            logger.info(f"File exceeds {MAX_FILE_SIZE_MB}MB limit, splitting into chunks")

            if not self._check_ffmpeg_available():
                raise RuntimeError(
                    "Audio file exceeds 25MB limit but ffmpeg is not available for splitting. "
                    "Please install ffmpeg (e.g., 'brew install ffmpeg' on macOS) "
                    "or use audio files smaller than 25MB."
                )

            chunk_paths = self._split_audio_file_ffmpeg(audio_file_path)

            try:
                transcripts = []
                detected_language = None

                for i, chunk_path in enumerate(chunk_paths):
                    logger.info(f"Transcribing chunk {i + 1}/{len(chunk_paths)}")

                    chunk_prompt = prompt
                    if i > 0:
                        chunk_prompt += " This is a continuation of the previous segment."

                    chunk_transcript, chunk_language = self._transcribe_single_file(
                        chunk_path, chunk_prompt
                    )

                    transcripts.append(chunk_transcript)
                    if detected_language is None and chunk_language:
                        detected_language = chunk_language

                full_transcript = " ".join(transcripts)

                logger.info(
                    f"Successfully transcribed {len(chunk_paths)} chunks. "
                    f"Total length: {len(full_transcript)} chars"
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
            logger.error(f"Error transcribing audio with OpenAI: {e}")
            raise

    def transcribe_audio_from_buffer(
        self,
        audio_buffer: BinaryIO,
        filename: str,
        *,
        user_id: int | None = None,
    ) -> tuple[str, str | None]:
        """Transcribe audio from a file buffer using OpenAI Whisper API."""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=Path(filename).suffix, delete=False
            ) as tmp_file:
                tmp_file.write(audio_buffer.read())
                tmp_path = Path(tmp_file.name)

            try:
                return self.transcribe_audio(tmp_path, user_id=user_id)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

        except Exception as e:  # noqa: BLE001
            logger.error(f"Error transcribing audio buffer with OpenAI: {e}")
            raise


_openai_transcription_service: OpenAITranscriptionService | None = None
_openai_summarization_service: OpenAISummarizationService | None = None


def get_openai_transcription_service() -> OpenAITranscriptionService:
    """Get the global OpenAI transcription service instance."""
    global _openai_transcription_service
    if _openai_transcription_service is None:
        _openai_transcription_service = OpenAITranscriptionService()
    return _openai_transcription_service


def get_openai_summarization_service() -> OpenAISummarizationService:
    """Get the global OpenAI summarization service instance."""
    global _openai_summarization_service
    if _openai_summarization_service is None:
        _openai_summarization_service = OpenAISummarizationService()
        _openai_summarization_service.default_models = get_content_summarizer().default_models
    return _openai_summarization_service
