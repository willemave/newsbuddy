"""One-shot narration helpers for digest audio playback."""

from __future__ import annotations

from importlib.util import find_spec

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band

try:  # pragma: no cover - import availability covered by readiness checks
    from elevenlabs import VoiceSettings
    from elevenlabs.client import ElevenLabs
except Exception:  # pragma: no cover - gracefully handled at runtime
    VoiceSettings = None  # type: ignore[misc,assignment]
    ElevenLabs = None  # type: ignore[misc,assignment]

logger = get_logger(__name__)


class DigestNarrationTtsService:
    """Generate one-shot narration audio for daily digests."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def synthesize_mp3(
        self,
        *,
        text: str,
        item_id: int | None = None,
        user_id: int | None = None,
    ) -> bytes:
        """Generate MP3 narration audio for one digest.

        Args:
            text: Plain-text narration script.
            item_id: Optional digest id for structured logging.

        Returns:
            MP3 bytes for playback.

        Raises:
            ValueError: If ElevenLabs is unavailable or required config is missing.
            RuntimeError: If audio generation fails or returns empty audio.
        """

        normalized = text.strip()
        if not normalized:
            raise ValueError("Narration text is empty")
        if not self._settings.elevenlabs_api_key:
            raise ValueError("ElevenLabs API key is not configured")
        if not self._settings.elevenlabs_tts_voice_id:
            raise ValueError("ElevenLabs TTS voice id is not configured")
        if find_spec("elevenlabs") is None or ElevenLabs is None or VoiceSettings is None:
            raise ValueError("ElevenLabs SDK is not installed")

        try:
            client = ElevenLabs(api_key=self._settings.elevenlabs_api_key)
            audio_iterator = client.text_to_speech.convert(
                voice_id=self._settings.elevenlabs_tts_voice_id,
                text=normalized,
                model_id=self._settings.elevenlabs_digest_tts_model,
                output_format=self._settings.elevenlabs_digest_tts_output_format,
                voice_settings=VoiceSettings(speed=self._settings.elevenlabs_digest_tts_speed),
            )
            audio_bytes = bytearray()
            for chunk in audio_iterator:
                if chunk:
                    audio_bytes.extend(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Digest narration generation failed",
                extra={
                    "component": "digest_narration_tts",
                    "operation": "synthesize_mp3",
                    "item_id": item_id,
                    "context_data": {
                        "model_id": self._settings.elevenlabs_digest_tts_model,
                        "output_format": self._settings.elevenlabs_digest_tts_output_format,
                        "speed": self._settings.elevenlabs_digest_tts_speed,
                    },
                },
            )
            raise RuntimeError("Failed to generate digest narration audio") from exc

        if not audio_bytes:
            raise RuntimeError("Digest narration audio was empty")

        record_vendor_usage_out_of_band(
            provider="elevenlabs",
            model=self._settings.elevenlabs_digest_tts_model,
            feature="narration_tts",
            operation="digest_narration_tts.synthesize_mp3",
            source="api",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "target_id": item_id,
                "voice_id": self._settings.elevenlabs_tts_voice_id,
                "output_format": self._settings.elevenlabs_digest_tts_output_format,
                "text_chars": len(normalized),
                "audio_bytes": len(audio_bytes),
            },
        )

        return bytes(audio_bytes)


_digest_narration_tts_service: DigestNarrationTtsService | None = None


def get_digest_narration_tts_service() -> DigestNarrationTtsService:
    """Return the cached digest narration TTS service."""

    global _digest_narration_tts_service
    if _digest_narration_tts_service is None:
        _digest_narration_tts_service = DigestNarrationTtsService()
    return _digest_narration_tts_service
