"""One-shot TTS helpers for content summary narration."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.util import find_spec
from pathlib import Path

import httpx

try:  # pragma: no cover - import availability covered by readiness checks
    from elevenlabs import VoiceSettings
    from elevenlabs.client import ElevenLabs
except Exception:  # pragma: no cover - gracefully handled at runtime
    VoiceSettings = None  # type: ignore[misc,assignment]
    ElevenLabs = None  # type: ignore[misc,assignment]

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band

logger = get_logger(__name__)
ELEVENLABS_FLASH_MAX_INPUT_CHARS = 40_000
TTS_CHUNK_TARGET_CHARS = 35_000
FFMPEG_STITCH_SECONDS_PER_CHUNK = 15
FFMPEG_STITCH_MAX_TIMEOUT_SECONDS = 300


class PermanentNarrationTtsError(ValueError):
    """Raised when retrying cannot repair local TTS input or configuration."""


class NarrationTtsInputError(PermanentNarrationTtsError):
    """Raised when a narration request has no speakable input."""


class NarrationTtsConfigurationError(PermanentNarrationTtsError):
    """Raised when the configured narration TTS integration is not usable."""


class EmptyNarrationAudioError(RuntimeError):
    """Raised when the speech provider returns an empty audio payload."""


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


class ContentNarrationTtsService:
    """Generate one-shot narration audio for content summaries."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def synthesize_mp3(
        self,
        *,
        text: str,
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> bytes:
        """Generate MP3 narration audio for one content summary.

        Args:
            text: Plain-text narration script.
            item_id: Optional content id for structured logging.

        Returns:
            MP3 bytes for playback.

        Raises:
            ValueError: If required ElevenLabs configuration is missing.
            RuntimeError: If audio generation fails or returns empty audio.
        """

        normalized = text.strip()
        if not normalized:
            raise NarrationTtsInputError("Narration text is empty")
        voice_id = self._settings.elevenlabs_tts_voice_id
        self._require_elevenlabs_config(voice_id)
        assert voice_id is not None
        text_chunks = _chunk_tts_text(normalized)
        model_id = self._settings.elevenlabs_narration_tts_model
        output_format = self._settings.elevenlabs_narration_tts_output_format

        started_at = time.perf_counter()
        logger.info(
            "Content narration TTS started",
            extra={
                "component": "content_narration_tts",
                "operation": "synthesize_mp3",
                "status": "started",
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "text_chars": len(normalized),
                },
            },
        )
        completed_chars = 0
        audio_chunks: list[bytes] = []
        try:
            with httpx.Client(timeout=240) as http_client:
                client = ElevenLabs(
                    api_key=self._settings.elevenlabs_api_key,
                    httpx_client=http_client,
                )
                for chunk in text_chunks:
                    audio_chunk, _ = self._synthesize_text_mp3(
                        client=client,
                        text=chunk,
                        voice_id=voice_id,
                        model_id=model_id,
                        output_format=output_format,
                        empty_audio_message="Content narration audio was empty",
                    )
                    audio_chunks.append(audio_chunk)
                    completed_chars += len(chunk)
            self._record_tts_usage(
                model_id=model_id,
                feature="narration_tts",
                operation="content_narration_tts.synthesize_mp3",
                user_id=user_id,
                request_count=len(audio_chunks),
                text_chars=completed_chars,
                metadata={
                    "target_id": item_id,
                    "voice_id": voice_id,
                    "output_format": output_format,
                },
            )
            audio_bytes, _ = self._stitch_mp3_chunks(
                audio_chunks,
                item_id=item_id,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            if completed_chars and completed_chars < len(normalized):
                self._record_tts_usage(
                    model_id=model_id,
                    feature="narration_tts",
                    operation="content_narration_tts.synthesize_mp3",
                    user_id=user_id,
                    request_count=len(audio_chunks),
                    text_chars=completed_chars,
                    metadata={
                        "target_id": item_id,
                        "voice_id": voice_id,
                        "output_format": output_format,
                        "synthesis_status": "partial",
                    },
                )
            logger.exception(
                "Content narration generation failed",
                extra={
                    "component": "content_narration_tts",
                    "operation": "synthesize_mp3",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": item_id,
                    "user_id": user_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "speed": self._settings.elevenlabs_narration_tts_speed,
                    },
                },
            )
            if isinstance(exc, EmptyNarrationAudioError):
                raise
            raise RuntimeError("Failed to generate content narration audio") from exc

        if not audio_bytes:
            raise RuntimeError("Content narration audio was empty")

        logger.info(
            "Content narration TTS completed",
            extra={
                "component": "content_narration_tts",
                "operation": "synthesize_mp3",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "text_chars": len(normalized),
                    "audio_bytes": len(audio_bytes),
                },
            },
        )

        return bytes(audio_bytes)

    def synthesize_dialogue_mp3(
        self,
        *,
        turns: Iterable[Mapping[str, str]],
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> bytes:
        """Generate MP3 audio for a multi-speaker podcast script."""

        normalized_turns = _normalize_dialogue_turns(turns)
        if not normalized_turns:
            raise NarrationTtsInputError("Dialogue turns are empty")
        host_voice_id = (
            self._settings.elevenlabs_podcast_host_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        guest_voice_id = (
            self._settings.elevenlabs_podcast_guest_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        self._require_elevenlabs_config(host_voice_id, guest_voice_id)
        assert host_voice_id is not None
        assert guest_voice_id is not None
        model_id = self._settings.elevenlabs_narration_tts_model
        output_format = self._settings.elevenlabs_narration_tts_output_format
        max_workers = min(
            len(normalized_turns),
            self._settings.elevenlabs_audio_episode_tts_max_workers,
        )
        started_at = time.perf_counter()
        text_chars = sum(len(turn["text"]) for turn in normalized_turns)
        logger.info(
            "Audio episode dialogue TTS started",
            extra={
                "component": "audio_episode_tts",
                "operation": "synthesize_dialogue_mp3",
                "status": "started",
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "mode": "parallel_turns",
                    "max_workers": max_workers,
                    "turn_count": len(normalized_turns),
                    "text_chars": text_chars,
                },
            },
        )
        completed_chars = 0
        completed_requests = 0
        usage_recorded = False
        try:
            turn_results = [b""] * len(normalized_turns)
            turn_durations_ms: list[float] = []
            first_error: Exception | None = None
            with (
                httpx.Client(timeout=240) as http_client,
                ThreadPoolExecutor(max_workers=max_workers) as executor,
            ):
                client = ElevenLabs(
                    api_key=self._settings.elevenlabs_api_key,
                    httpx_client=http_client,
                )
                futures = {
                    executor.submit(
                        self._synthesize_dialogue_turn_mp3,
                        client=client,
                        turn=turn,
                        host_voice_id=host_voice_id,
                        guest_voice_id=guest_voice_id,
                        model_id=model_id,
                        output_format=output_format,
                    ): (index, len(turn["text"]))
                    for index, turn in enumerate(normalized_turns)
                }
                for future in as_completed(futures):
                    index, turn_chars = futures[future]
                    try:
                        audio_chunk, turn_duration_ms = future.result()
                    except Exception as exc:  # noqa: BLE001
                        if first_error is None:
                            first_error = exc
                        continue
                    turn_results[index] = audio_chunk
                    turn_durations_ms.append(turn_duration_ms)
                    completed_requests += 1
                    completed_chars += turn_chars

            if completed_requests:
                self._record_tts_usage(
                    model_id=model_id,
                    feature="audio_episode_tts",
                    operation="audio_episode_tts.synthesize_dialogue_mp3",
                    user_id=user_id,
                    request_count=completed_requests,
                    text_chars=completed_chars,
                    metadata={
                        "target_id": item_id,
                        "host_voice_id": host_voice_id,
                        "guest_voice_id": guest_voice_id,
                        "output_format": output_format,
                        "mode": "parallel_turns",
                        "max_workers": max_workers,
                        "turn_count": len(normalized_turns),
                        "synthesis_status": "partial" if first_error else "completed",
                    },
                )
                usage_recorded = True
            if first_error is not None:
                raise first_error

            audio_bytes, stitch_duration_ms = self._stitch_mp3_chunks(
                turn_results,
                item_id=item_id,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            if completed_requests and not usage_recorded:
                self._record_tts_usage(
                    model_id=model_id,
                    feature="audio_episode_tts",
                    operation="audio_episode_tts.synthesize_dialogue_mp3",
                    user_id=user_id,
                    request_count=completed_requests,
                    text_chars=completed_chars,
                    metadata={
                        "target_id": item_id,
                        "host_voice_id": host_voice_id,
                        "guest_voice_id": guest_voice_id,
                        "output_format": output_format,
                        "synthesis_status": "partial",
                    },
                )
            logger.exception(
                "Audio episode dialogue generation failed",
                extra={
                    "component": "audio_episode_tts",
                    "operation": "synthesize_dialogue_mp3",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": item_id,
                    "user_id": user_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "mode": "parallel_turns",
                        "max_workers": max_workers,
                        "turn_count": len(normalized_turns),
                    },
                },
            )
            raise RuntimeError("Failed to generate audio episode dialogue") from exc

        if not audio_bytes:
            raise RuntimeError("Audio episode dialogue was empty")

        logger.info(
            "Audio episode dialogue TTS completed",
            extra={
                "component": "audio_episode_tts",
                "operation": "synthesize_dialogue_mp3",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "mode": "parallel_turns",
                    "max_workers": max_workers,
                    "turn_count": len(normalized_turns),
                    "text_chars": text_chars,
                    "stitch_duration_ms": stitch_duration_ms,
                    "turn_duration_ms_max": round(max(turn_durations_ms), 2)
                    if turn_durations_ms
                    else 0,
                    "audio_bytes": len(audio_bytes),
                },
            },
        )

        return bytes(audio_bytes)

    def _synthesize_dialogue_turn_mp3(
        self,
        *,
        client: ElevenLabs,
        turn: Mapping[str, str],
        host_voice_id: str,
        guest_voice_id: str,
        model_id: str,
        output_format: str,
    ) -> tuple[bytes, float]:
        is_host = turn["speaker"] == "host"
        return self._synthesize_text_mp3(
            client=client,
            text=turn["text"],
            voice_id=host_voice_id if is_host else guest_voice_id,
            model_id=model_id,
            output_format=output_format,
            empty_audio_message="Audio episode dialogue turn was empty",
        )

    def _synthesize_text_mp3(
        self,
        *,
        client: ElevenLabs,
        text: str,
        voice_id: str,
        model_id: str,
        output_format: str,
        empty_audio_message: str,
    ) -> tuple[bytes, float]:
        started_at = time.perf_counter()
        audio_iterator = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=model_id,
            output_format=output_format,
            voice_settings=VoiceSettings(speed=self._settings.elevenlabs_narration_tts_speed),
        )
        audio_bytes = self._collect_audio(audio_iterator)
        if not audio_bytes:
            raise EmptyNarrationAudioError(empty_audio_message)
        return bytes(audio_bytes), _duration_ms(started_at)

    def _stitch_mp3_chunks(
        self,
        chunks: list[bytes],
        *,
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> tuple[bytes, float]:
        """Stitch independently generated MP3 chunks into one valid MP3 asset."""

        if len(chunks) == 1:
            return chunks[0], 0.0

        ffmpeg_binary = shutil.which("ffmpeg")
        if ffmpeg_binary is None:
            raise NarrationTtsConfigurationError(
                "ffmpeg is required to stitch narration audio chunks"
            )

        started_at = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="newsly-audio-episode-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            input_paths: list[Path] = []
            for index, chunk in enumerate(chunks):
                input_path = temp_dir / f"turn-{index:03d}.mp3"
                input_path.write_bytes(chunk)
                input_paths.append(input_path)

            list_path = temp_dir / "inputs.txt"
            list_path.write_text(
                "\n".join(_ffmpeg_concat_line(path) for path in input_paths),
                encoding="utf-8",
            )
            output_path = temp_dir / "stitched.mp3"
            result = subprocess.run(
                [
                    ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-vn",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=_stitch_timeout_seconds(len(chunks)),
            )
            if (
                result.returncode != 0
                or not output_path.exists()
                or output_path.stat().st_size <= 0
            ):
                logger.error(
                    "Narration audio stitch failed",
                    extra={
                        "component": "narration_tts",
                        "operation": "stitch_mp3_chunks",
                        "duration_ms": _duration_ms(started_at),
                        "item_id": item_id,
                        "user_id": user_id,
                        "context_data": {
                            "chunk_count": len(chunks),
                            "stderr": (result.stderr or "")[-500:],
                        },
                    },
                )
                raise RuntimeError("Failed to stitch narration audio")
            audio_bytes = output_path.read_bytes()

        duration_ms = _duration_ms(started_at)
        logger.info(
            "Narration audio stitch completed",
            extra={
                "component": "narration_tts",
                "operation": "stitch_mp3_chunks",
                "status": "completed",
                "duration_ms": duration_ms,
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "chunk_count": len(chunks),
                    "audio_bytes": len(audio_bytes),
                },
            },
        )
        return audio_bytes, duration_ms

    @staticmethod
    def _record_tts_usage(
        *,
        model_id: str,
        feature: str,
        operation: str,
        user_id: int | None,
        request_count: int,
        text_chars: int,
        metadata: dict[str, object],
    ) -> None:
        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=model_id,
            feature=feature,
            operation=operation,
            source="api",
            usage={"request_count": request_count, "resource_count": text_chars},
            user_id=user_id,
            metadata={**metadata, "text_chars": text_chars},
        )

    def _require_elevenlabs_config(self, *voices: str | None) -> None:
        """Raise a user-facing config error if ElevenLabs speech cannot be used."""

        if not self._settings.elevenlabs_api_key:
            raise NarrationTtsConfigurationError("ElevenLabs API key is not configured")
        if not self._settings.elevenlabs_narration_tts_model:
            raise NarrationTtsConfigurationError("ElevenLabs narration model is not configured")
        if any(not voice for voice in voices):
            raise NarrationTtsConfigurationError("ElevenLabs narration voice is not configured")
        if find_spec("elevenlabs") is None or ElevenLabs is None or VoiceSettings is None:
            raise NarrationTtsConfigurationError("ElevenLabs SDK is not installed")

    @staticmethod
    def _collect_audio(audio_iterator: Iterable[bytes]) -> bytes:
        return b"".join(chunk for chunk in audio_iterator if chunk)


def _normalize_dialogue_turns(turns: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        speaker = str(turn.get("speaker") or "guest").strip().lower()
        normalized_speaker = "host" if speaker == "host" else "guest"
        normalized.extend(
            {"speaker": normalized_speaker, "text": chunk} for chunk in _chunk_tts_text(text)
        )
    return normalized


def _chunk_tts_text(text: str) -> list[str]:
    """Split provider-bound speech input without changing its text or whitespace."""

    remaining = text
    chunks: list[str] = []
    while len(remaining) > TTS_CHUNK_TARGET_CHARS:
        window = remaining[: TTS_CHUNK_TARGET_CHARS + 1]
        sentence_cut = max(
            (
                index
                for index in range(1, len(window))
                if window[index - 1] in ".!?" and window[index].isspace()
            ),
            default=0,
        )
        word_cut = max(
            (index for index, character in enumerate(window[:-1], start=1) if character.isspace()),
            default=0,
        )
        cut = sentence_cut or word_cut
        if cut <= 0:
            cut = TTS_CHUNK_TARGET_CHARS
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _ffmpeg_concat_line(path: Path) -> str:
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'"


def _stitch_timeout_seconds(chunk_count: int) -> int:
    return min(
        max(chunk_count, 2) * FFMPEG_STITCH_SECONDS_PER_CHUNK,
        FFMPEG_STITCH_MAX_TIMEOUT_SECONDS,
    )


_content_narration_tts_service: ContentNarrationTtsService | None = None


def get_content_narration_tts_service() -> ContentNarrationTtsService:
    """Return the cached content narration TTS service."""

    global _content_narration_tts_service
    if _content_narration_tts_service is None:
        _content_narration_tts_service = ContentNarrationTtsService()
    return _content_narration_tts_service
