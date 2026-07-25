import os
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()


class WhisperLocalTranscriptionService:
    """Local Whisper service for audio transcription using OpenAI's Whisper model."""

    def __init__(self):
        self.model_name = getattr(settings, "whisper_model_size", "base")
        self.device = self._get_device()
        self.model = None
        logger.info(f"Whisper service: model={self.model_name}, device={self.device}")

    def _get_device(self) -> str:
        """Determine the best device to use for inference."""
        device_setting = getattr(settings, "whisper_device", "auto")

        if device_setting == "auto":
            import torch

            if torch.cuda.is_available():
                device = "cuda"
                logger.info(f"CUDA available, using GPU: {torch.cuda.get_device_name(0)}")
            elif torch.backends.mps.is_available():
                # MPS has issues with sparse tensors in Whisper, use CPU instead
                device = "cpu"
                logger.info(
                    "MPS (Apple Silicon) detected, but using CPU due to sparse tensor compatibility"
                )
            else:
                device = "cpu"
                logger.info("Using CPU for inference")
            return device

        return device_setting

    def _load_model(self):
        """Lazy load the Whisper model."""
        if self.model is None:
            import whisper

            logger.info(f"Loading Whisper model: {self.model_name}")
            try:
                self.model = whisper.load_model(self.model_name, device=self.device)
                logger.info(f"Model loaded successfully on {self.device}")
            except (RuntimeError, NotImplementedError) as e:
                error_msg = str(e).lower()
                if "mps" in error_msg or "sparse" in error_msg or "_sparse_coo_tensor" in error_msg:
                    logger.warning(
                        f"Failed to load model on {self.device}, falling back to CPU: {e}"
                    )
                    self.device = "cpu"
                    self.model = whisper.load_model(self.model_name, device=self.device)
                    logger.info("Model loaded successfully on CPU after MPS failure")
                else:
                    raise

    def transcribe_audio(self, audio_file_path: Path) -> tuple[str, str | None]:
        """Transcribe audio file using local Whisper model.

        Args:
            audio_file_path: Path to the audio file to transcribe

        Returns:
            Tuple of (transcript, language_code)
        """
        try:
            # Ensure model is loaded
            self._load_model()

            # Verify file exists
            if not audio_file_path.exists():
                raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

            file_size_mb = os.path.getsize(audio_file_path) / (1024 * 1024)
            logger.info(f"Starting transcription of {audio_file_path} ({file_size_mb:.1f} MB)")

            # Transcribe the audio
            try:
                result = self.model.transcribe(
                    str(audio_file_path),
                    fp16=self.device != "cpu",  # Use FP16 on GPU for faster inference
                    language=None,  # Auto-detect language
                    task="transcribe",  # Transcribe in original language
                    verbose=False,
                )
            except (RuntimeError, NotImplementedError) as e:
                error_msg = str(e).lower()
                if "mps" in error_msg or "sparse" in error_msg or "_sparse_coo_tensor" in error_msg:
                    logger.warning(f"MPS transcription failed, retrying with CPU: {e}")
                    # Reload model on CPU
                    self.cleanup_service()
                    self.device = "cpu"
                    self._load_model()
                    # Retry transcription
                    result = self.model.transcribe(
                        str(audio_file_path),
                        fp16=False,  # Disable FP16 on CPU
                        language=None,
                        task="transcribe",
                        verbose=False,
                    )
                else:
                    raise

            transcript = result["text"].strip()
            detected_language = result.get("language", None)

            logger.info(
                f"Successfully transcribed audio. "
                f"Length: {len(transcript)} chars, Language: {detected_language}"
            )

            return transcript, detected_language

        except Exception as e:
            logger.error(f"Error transcribing audio with local Whisper: {e}")
            raise

    def cleanup_service(self):
        """Clean up the model from memory."""
        if self.model is not None:
            del self.model
            self.model = None

            # Clear GPU cache if using CUDA
            if self.device == "cuda":
                import torch

                torch.cuda.empty_cache()

            logger.info("Whisper model cleaned up")


# Global instance
_whisper_service = None


def get_whisper_local_service() -> WhisperLocalTranscriptionService:
    """Get the global Whisper local transcription service instance."""
    global _whisper_service
    if _whisper_service is None:
        _whisper_service = WhisperLocalTranscriptionService()
    return _whisper_service
