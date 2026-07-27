import os
import threading
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()

# Transcription is the one CPU-bound step in the media pipeline, and the model is
# a single shared object. Media workers run several claim threads so that
# downloads and ffmpeg normalization of the next episodes overlap the current
# transcription - but only one transcription runs at a time.
_TRANSCRIPTION_SINGLE_FLIGHT = threading.Semaphore(1)
_SERVICE_INIT_LOCK = threading.Lock()


class WhisperLocalTranscriptionService:
    """Local Whisper service for audio transcription, backed by faster-whisper."""

    def __init__(self):
        self.model_name = getattr(settings, "whisper_model_size", "base")
        self.device = self._get_device()
        self.compute_type = "float16" if self.device == "cuda" else "int8"
        self.model = None
        logger.info(
            f"Whisper service: model={self.model_name}, "
            f"device={self.device}, compute_type={self.compute_type}"
        )

    def _get_device(self) -> str:
        """Determine the best device to use for inference.

        CTranslate2 runs on CPU or CUDA only - there is no MPS backend, and int8
        on CPU is what Apple Silicon ends up using.
        """
        device_setting = getattr(settings, "whisper_device", "auto")
        if device_setting != "auto":
            return "cpu" if device_setting == "mps" else device_setting

        if self._cuda_is_available():
            logger.info("CUDA available, using GPU for inference")
            return "cuda"
        logger.info("Using CPU for inference")
        return "cpu"

    @staticmethod
    def _cuda_is_available() -> bool:
        from ctranslate2 import get_cuda_device_count

        try:
            return get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            logger.debug("CUDA device probe failed; assuming CPU", exc_info=True)
            return False

    def _load_model(self):
        """Lazy load the Whisper model."""
        if self.model is not None:
            return

        from faster_whisper import WhisperModel

        logger.info(f"Loading Whisper model: {self.model_name}")
        self.model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info(f"Model loaded successfully on {self.device}")

    def transcribe_audio(self, audio_file_path: Path) -> tuple[str, str | None]:
        """Transcribe audio file using the local Whisper model.

        Args:
            audio_file_path: Path to the audio file to transcribe

        Returns:
            Tuple of (transcript, language_code)
        """
        try:
            with _TRANSCRIPTION_SINGLE_FLIGHT:
                self._load_model()

                if not audio_file_path.exists():
                    raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

                file_size_mb = os.path.getsize(audio_file_path) / (1024 * 1024)
                logger.info(f"Starting transcription of {audio_file_path} ({file_size_mb:.1f} MB)")

                # Segments are produced lazily, so transcription only really runs
                # as the iterator is consumed - which must happen under the guard.
                segments, info = self.model.transcribe(
                    str(audio_file_path),
                    language=None,  # Auto-detect language
                    task="transcribe",  # Transcribe in original language
                )
                transcript = "".join(segment.text for segment in segments).strip()
                detected_language = getattr(info, "language", None)

            logger.info(
                f"Successfully transcribed audio. "
                f"Length: {len(transcript)} chars, Language: {detected_language}"
            )

            return transcript, detected_language

        except Exception as e:
            logger.error(f"Error transcribing audio with local Whisper: {e}")
            raise


# Global instance
_whisper_service: WhisperLocalTranscriptionService | None = None


def get_whisper_local_service() -> WhisperLocalTranscriptionService:
    """Get the global Whisper local transcription service instance."""
    global _whisper_service
    if _whisper_service is not None:
        return _whisper_service
    with _SERVICE_INIT_LOCK:
        if _whisper_service is None:
            _whisper_service = WhisperLocalTranscriptionService()
    return _whisper_service
