"""One-shot TTS helpers for content summary narration."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from importlib.util import find_spec

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
            raise ValueError("Narration text is empty")
        self._require_elevenlabs_config()

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
                    "item_id": item_id,
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
            raise ValueError("Dialogue turns are empty")
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
            raise ValueError("ElevenLabs podcast voice ids are not configured")
        if DialogueInput is None:
            raise ValueError("ElevenLabs dialogue SDK is not installed")

        model_id = self._settings.elevenlabs_dialogue_tts_model
        try:
            client = ElevenLabs(api_key=self._settings.elevenlabs_api_key)
            dialogue_inputs = [
                DialogueInput(
                    text=turn["text"],
                    voice_id=host_voice_id if turn["speaker"] == "host" else guest_voice_id,
                )
                for turn in normalized_turns
            ]
            audio_iterator = client.text_to_dialogue.convert(
                inputs=dialogue_inputs,
                model_id=model_id,
                output_format=self._settings.elevenlabs_narration_tts_output_format,
            )
            audio_bytes = self._collect_audio(audio_iterator)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Audio episode dialogue generation failed",
                extra={
                    "component": "audio_episode_tts",
                    "operation": "synthesize_dialogue_mp3",
                    "item_id": item_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": self._settings.elevenlabs_narration_tts_output_format,
                        "turn_count": len(normalized_turns),
                    },
                },
            )
            raise RuntimeError("Failed to generate audio episode dialogue") from exc

        if not audio_bytes:
            raise RuntimeError("Audio episode dialogue was empty")

        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=model_id,
            feature="audio_episode_tts",
            operation="audio_episode_tts.synthesize_dialogue_mp3",
            source="api",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "target_id": item_id,
                "host_voice_id": host_voice_id,
                "guest_voice_id": guest_voice_id,
                "output_format": self._settings.elevenlabs_narration_tts_output_format,
                "turn_count": len(normalized_turns),
                "text_chars": sum(len(turn["text"]) for turn in normalized_turns),
                "audio_bytes": len(audio_bytes),
            },
        )

        return bytes(audio_bytes)

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
            raise ValueError("Dialogue turns are empty")
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
            raise ValueError("ElevenLabs podcast voice ids are not configured")
        if DialogueInput is None:
            raise ValueError("ElevenLabs dialogue SDK is not installed")

        model_id = self._settings.elevenlabs_dialogue_tts_model
        output_format = self._settings.elevenlabs_narration_tts_output_format
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
                    "item_id": item_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "turn_count": len(normalized_turns),
                    },
                },
            )
            raise RuntimeError("Failed to stream audio episode dialogue") from exc

        audio_bytes = 0
        try:
            for chunk in audio_iterator:
                if not chunk:
                    continue
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
                    "item_id": item_id,
                    "context_data": {
                        "model_id": model_id,
                        "output_format": output_format,
                        "turn_count": len(normalized_turns),
                        "audio_bytes": audio_bytes,
                    },
                },
            )
            raise RuntimeError("Failed to stream audio episode dialogue") from exc

        if audio_bytes <= 0:
            raise RuntimeError("Audio episode dialogue stream was empty")

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
                "text_chars": sum(len(turn["text"]) for turn in normalized_turns),
                "audio_bytes": audio_bytes,
            },
        )

    def _require_elevenlabs_config(self) -> None:
        """Raise a user-facing config error if ElevenLabs cannot be used."""

        if not self._settings.elevenlabs_api_key:
            raise ValueError("ElevenLabs API key is not configured")
        if not self._settings.elevenlabs_tts_voice_id:
            raise ValueError("ElevenLabs TTS voice id is not configured")
        if find_spec("elevenlabs") is None or ElevenLabs is None or VoiceSettings is None:
            raise ValueError("ElevenLabs SDK is not installed")

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
        normalized.append(
            {
                "speaker": "host" if speaker == "host" else "guest",
                "text": text,
            }
        )
    return normalized


_content_narration_tts_service: ContentNarrationTtsService | None = None


def get_content_narration_tts_service() -> ContentNarrationTtsService:
    """Return the cached content narration TTS service."""

    global _content_narration_tts_service
    if _content_narration_tts_service is None:
        _content_narration_tts_service = ContentNarrationTtsService()
    return _content_narration_tts_service
