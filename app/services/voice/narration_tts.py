"""One-shot TTS helpers for content summary narration."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.util import find_spec
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band

try:  # pragma: no cover - import availability covered by readiness checks
    from elevenlabs import VoiceSettings
    from elevenlabs.client import ElevenLabs
    from elevenlabs.types.dialogue_input import DialogueInput
except Exception:  # pragma: no cover - gracefully handled at runtime
    DialogueInput = None  # type: ignore[misc,assignment]
    VoiceSettings = None  # type: ignore[misc,assignment]
    ElevenLabs = None  # type: ignore[misc,assignment]

logger = get_logger(__name__)
DIALOGUE_TTS_CHUNK_TARGET_CHARS = 3_500


class PermanentNarrationTtsError(ValueError):
    """Raised when retrying cannot repair local TTS input or configuration."""


class NarrationTtsInputError(PermanentNarrationTtsError):
    """Raised when a narration request has no speakable input."""


class NarrationTtsConfigurationError(PermanentNarrationTtsError):
    """Raised when the local ElevenLabs integration is not usable."""


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
            ValueError: If ElevenLabs is unavailable or required config is missing.
            RuntimeError: If audio generation fails or returns empty audio.
        """

        normalized = text.strip()
        if not normalized:
            raise NarrationTtsInputError("Narration text is empty")
        self._require_elevenlabs_config()

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
                    "model_id": self._settings.elevenlabs_narration_tts_model,
                    "output_format": self._settings.elevenlabs_narration_tts_output_format,
                    "text_chars": len(normalized),
                },
            },
        )
        try:
            client = ElevenLabs(api_key=self._settings.elevenlabs_api_key)
            audio_iterator = client.text_to_speech.convert(
                voice_id=self._settings.elevenlabs_tts_voice_id,
                text=normalized,
                model_id=self._settings.elevenlabs_narration_tts_model,
                output_format=self._settings.elevenlabs_narration_tts_output_format,
                voice_settings=VoiceSettings(speed=self._settings.elevenlabs_narration_tts_speed),
            )
            audio_bytes = self._collect_audio(audio_iterator)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Content narration generation failed",
                extra={
                    "component": "content_narration_tts",
                    "operation": "synthesize_mp3",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": item_id,
                    "user_id": user_id,
                    "context_data": {
                        "model_id": self._settings.elevenlabs_narration_tts_model,
                        "output_format": self._settings.elevenlabs_narration_tts_output_format,
                        "speed": self._settings.elevenlabs_narration_tts_speed,
                    },
                },
            )
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
                    "model_id": self._settings.elevenlabs_narration_tts_model,
                    "output_format": self._settings.elevenlabs_narration_tts_output_format,
                    "text_chars": len(normalized),
                    "audio_bytes": len(audio_bytes),
                },
            },
        )

        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=self._settings.elevenlabs_narration_tts_model,
            feature="narration_tts",
            operation="content_narration_tts.synthesize_mp3",
            source="api",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "target_id": item_id,
                "voice_id": self._settings.elevenlabs_tts_voice_id,
                "output_format": self._settings.elevenlabs_narration_tts_output_format,
                "text_chars": len(normalized),
                "audio_bytes": len(audio_bytes),
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
        self._require_elevenlabs_config()

        host_voice_id = (
            self._settings.elevenlabs_podcast_host_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        guest_voice_id = (
            self._settings.elevenlabs_podcast_guest_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        if not host_voice_id or not guest_voice_id:
            raise NarrationTtsConfigurationError("ElevenLabs podcast voice ids are not configured")
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
        try:
            turn_results = [b""] * len(normalized_turns)
            turn_durations_ms: list[float] = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        self._synthesize_dialogue_turn_mp3,
                        turn=turn,
                        host_voice_id=host_voice_id,
                        guest_voice_id=guest_voice_id,
                        model_id=model_id,
                        output_format=output_format,
                    ): index
                    for index, turn in enumerate(normalized_turns)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    audio_chunk, turn_duration_ms = future.result()
                    turn_results[index] = audio_chunk
                    turn_durations_ms.append(turn_duration_ms)

            audio_bytes, stitch_duration_ms = self._stitch_dialogue_turns_mp3(
                turn_results,
                item_id=item_id,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
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

        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=model_id,
            feature="audio_episode_tts",
            operation="audio_episode_tts.synthesize_dialogue_mp3",
            source="api",
            usage={"request_count": len(normalized_turns)},
            user_id=user_id,
            metadata={
                "target_id": item_id,
                "host_voice_id": host_voice_id,
                "guest_voice_id": guest_voice_id,
                "output_format": output_format,
                "mode": "parallel_turns",
                "max_workers": max_workers,
                "turn_count": len(normalized_turns),
                "text_chars": text_chars,
                "stitch_duration_ms": stitch_duration_ms,
                "audio_bytes": len(audio_bytes),
            },
        )

        return bytes(audio_bytes)

    def _synthesize_dialogue_turn_mp3(
        self,
        *,
        turn: Mapping[str, str],
        host_voice_id: str,
        guest_voice_id: str,
        model_id: str,
        output_format: str,
    ) -> tuple[bytes, float]:
        started_at = time.perf_counter()
        voice_id = host_voice_id if turn["speaker"] == "host" else guest_voice_id
        client = ElevenLabs(api_key=self._settings.elevenlabs_api_key)
        audio_iterator = client.text_to_speech.convert(
            voice_id=voice_id,
            text=turn["text"],
            model_id=model_id,
            output_format=output_format,
            voice_settings=VoiceSettings(speed=self._settings.elevenlabs_narration_tts_speed),
        )
        audio_bytes = self._collect_audio(audio_iterator)
        if not audio_bytes:
            raise RuntimeError("Audio episode dialogue turn was empty")
        return bytes(audio_bytes), _duration_ms(started_at)

    def _stitch_dialogue_turns_mp3(
        self,
        chunks: list[bytes],
        *,
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> tuple[bytes, float]:
        """Stitch independently generated MP3 turns into one valid MP3 asset."""

        if len(chunks) == 1:
            return chunks[0], 0.0

        ffmpeg_binary = shutil.which("ffmpeg")
        if ffmpeg_binary is None:
            raise NarrationTtsConfigurationError(
                "ffmpeg is required to stitch audio episode dialogue turns"
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
                timeout=30,
            )
            if (
                result.returncode != 0
                or not output_path.exists()
                or output_path.stat().st_size <= 0
            ):
                logger.error(
                    "Audio episode dialogue stitch failed",
                    extra={
                        "component": "audio_episode_tts",
                        "operation": "stitch_dialogue_turns_mp3",
                        "duration_ms": _duration_ms(started_at),
                        "item_id": item_id,
                        "user_id": user_id,
                        "context_data": {
                            "turn_count": len(chunks),
                            "stderr": (result.stderr or "")[-500:],
                        },
                    },
                )
                raise RuntimeError("Failed to stitch audio episode dialogue")
            audio_bytes = output_path.read_bytes()

        duration_ms = _duration_ms(started_at)
        logger.info(
            "Audio episode dialogue stitch completed",
            extra={
                "component": "audio_episode_tts",
                "operation": "stitch_dialogue_turns_mp3",
                "status": "completed",
                "duration_ms": duration_ms,
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "turn_count": len(chunks),
                    "audio_bytes": len(audio_bytes),
                },
            },
        )
        return audio_bytes, duration_ms

    def stream_dialogue_mp3(
        self,
        *,
        turns: Iterable[Mapping[str, str]],
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> Iterator[bytes]:
        """Stream MP3 audio chunks for a multi-speaker podcast script."""

        normalized_turns = _normalize_dialogue_turns(turns)
        if not normalized_turns:
            raise NarrationTtsInputError("Dialogue turns are empty")
        self._require_elevenlabs_config()

        host_voice_id = (
            self._settings.elevenlabs_podcast_host_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        guest_voice_id = (
            self._settings.elevenlabs_podcast_guest_voice_id
            or self._settings.elevenlabs_tts_voice_id
        )
        if not host_voice_id or not guest_voice_id:
            raise NarrationTtsConfigurationError("ElevenLabs podcast voice ids are not configured")
        if DialogueInput is None:
            raise NarrationTtsConfigurationError("ElevenLabs dialogue SDK is not installed")

        model_id = self._settings.elevenlabs_dialogue_tts_model
        output_format = self._settings.elevenlabs_narration_tts_output_format
        started_at = time.perf_counter()
        text_chars = sum(len(turn["text"]) for turn in normalized_turns)
        logger.info(
            "Audio episode dialogue stream setup started",
            extra={
                "component": "audio_episode_tts",
                "operation": "stream_dialogue_mp3",
                "status": "setup_started",
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "turn_count": len(normalized_turns),
                    "text_chars": text_chars,
                },
            },
        )
        try:
            client = ElevenLabs(api_key=self._settings.elevenlabs_api_key)
            dialogue_inputs = [
                DialogueInput(
                    text=turn["text"],
                    voice_id=host_voice_id if turn["speaker"] == "host" else guest_voice_id,
                )
                for turn in normalized_turns
            ]
            audio_iterator = client.text_to_dialogue.stream(
                inputs=dialogue_inputs,
                model_id=model_id,
                output_format=output_format,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Audio episode dialogue stream failed to start",
                extra={
                    "component": "audio_episode_tts",
                    "operation": "stream_dialogue_mp3",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": item_id,
                    "user_id": user_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "turn_count": len(normalized_turns),
                    },
                },
            )
            raise RuntimeError("Failed to stream audio episode dialogue") from exc

        audio_bytes = 0
        first_chunk_ms: float | None = None
        logger.info(
            "Audio episode dialogue stream iterator ready",
            extra={
                "component": "audio_episode_tts",
                "operation": "stream_dialogue_mp3",
                "status": "iterator_ready",
                "duration_ms": _duration_ms(started_at),
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "turn_count": len(normalized_turns),
                },
            },
        )
        try:
            for chunk in audio_iterator:
                if not chunk:
                    continue
                if first_chunk_ms is None:
                    first_chunk_ms = _duration_ms(started_at)
                    logger.info(
                        "Audio episode dialogue stream first chunk",
                        extra={
                            "component": "audio_episode_tts",
                            "operation": "stream_dialogue_mp3",
                            "status": "first_chunk",
                            "duration_ms": first_chunk_ms,
                            "item_id": item_id,
                            "user_id": user_id,
                            "context_data": {
                                "model_id": model_id,
                                "output_format": output_format,
                                "turn_count": len(normalized_turns),
                            },
                        },
                    )
                audio_bytes += len(chunk)
                yield bytes(chunk)
        except GeneratorExit:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Audio episode dialogue stream failed",
                extra={
                    "component": "audio_episode_tts",
                    "operation": "stream_dialogue_mp3",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": item_id,
                    "user_id": user_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "turn_count": len(normalized_turns),
                        "audio_bytes": audio_bytes,
                        "time_to_first_chunk_ms": first_chunk_ms or 0,
                    },
                },
            )
            raise RuntimeError("Failed to stream audio episode dialogue") from exc

        if audio_bytes <= 0:
            raise RuntimeError("Audio episode dialogue stream was empty")

        logger.info(
            "Audio episode dialogue stream completed",
            extra={
                "component": "audio_episode_tts",
                "operation": "stream_dialogue_mp3",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": item_id,
                "user_id": user_id,
                "context_data": {
                    "model_id": model_id,
                    "output_format": output_format,
                    "turn_count": len(normalized_turns),
                    "text_chars": text_chars,
                    "audio_bytes": audio_bytes,
                    "time_to_first_chunk_ms": first_chunk_ms or 0,
                },
            },
        )

        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=model_id,
            feature="audio_episode_tts",
            operation="audio_episode_tts.stream_dialogue_mp3",
            source="api",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "target_id": item_id,
                "host_voice_id": host_voice_id,
                "guest_voice_id": guest_voice_id,
                "output_format": output_format,
                "turn_count": len(normalized_turns),
                "text_chars": text_chars,
                "audio_bytes": audio_bytes,
            },
        )

    def _require_elevenlabs_config(self) -> None:
        """Raise a user-facing config error if ElevenLabs cannot be used."""

        if not self._settings.elevenlabs_api_key:
            raise NarrationTtsConfigurationError("ElevenLabs API key is not configured")
        if not self._settings.elevenlabs_tts_voice_id:
            raise NarrationTtsConfigurationError("ElevenLabs TTS voice id is not configured")
        if find_spec("elevenlabs") is None or ElevenLabs is None or VoiceSettings is None:
            raise NarrationTtsConfigurationError("ElevenLabs SDK is not installed")

    @staticmethod
    def _collect_audio(audio_iterator: Iterable[bytes]) -> bytearray:
        audio_bytes = bytearray()
        for chunk in audio_iterator:
            if chunk:
                audio_bytes.extend(chunk)
        return audio_bytes


def _normalize_dialogue_turns(turns: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        speaker = str(turn.get("speaker") or "guest").strip().lower()
        normalized_speaker = "host" if speaker == "host" else "guest"
        normalized.extend(
            {"speaker": normalized_speaker, "text": chunk} for chunk in _chunk_dialogue_text(text)
        )
    return normalized


def _chunk_dialogue_text(text: str) -> list[str]:
    """Split provider-bound dialogue without changing its text or whitespace."""

    remaining = text
    chunks: list[str] = []
    while len(remaining) > DIALOGUE_TTS_CHUNK_TARGET_CHARS:
        window = remaining[: DIALOGUE_TTS_CHUNK_TARGET_CHARS + 1]
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
            cut = DIALOGUE_TTS_CHUNK_TARGET_CHARS
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _ffmpeg_concat_line(path: Path) -> str:
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'"


_content_narration_tts_service: ContentNarrationTtsService | None = None


def get_content_narration_tts_service() -> ContentNarrationTtsService:
    """Return the cached content narration TTS service."""

    global _content_narration_tts_service
    if _content_narration_tts_service is None:
        _content_narration_tts_service = ContentNarrationTtsService()
    return _content_narration_tts_service
