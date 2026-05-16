"""Reusable audio download and transcription primitives."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.scraping.youtube_config import load_youtube_client_config
from app.services.whisper_local import get_whisper_local_service

try:  # pragma: no cover - optional dependency in tests
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


class _YtDlpLogger:
    def __init__(self, base_logger):
        self._logger = base_logger

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        self._logger.warning(msg)


def build_youtube_extractor_args() -> dict[str, dict[str, list[str]]]:
    """Return configured yt-dlp extractor args for YouTube-compatible downloads."""
    client_config = load_youtube_client_config()
    extractor_args: dict[str, dict[str, list[str]]] = {
        "youtube": {
            "player_client": [client_config.player_client],
            "player_skip": ["configs"],
        }
    }

    provider = client_config.po_token_provider
    if provider:
        provider_key = f"youtubepot-{provider}"
        provider_args: dict[str, list[str]] = {}
        if client_config.po_token_base_url:
            provider_args["base_url"] = [str(client_config.po_token_base_url)]
        extractor_args[provider_key] = provider_args

    return extractor_args


def download_audio_via_ytdlp(
    url: str,
    out_dir: Path,
    *,
    output_stem: str = "audio",
    use_youtube_config: bool = False,
) -> Path:
    """Download best available audio for any yt-dlp-supported URL."""
    if yt_dlp is None:  # pragma: no cover - runtime safeguard when dependency missing
        raise RuntimeError("yt-dlp is required to download audio")

    started_at = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = next(out_dir.glob(f"{output_stem}.*"), None)
    if existing and existing.stat().st_size > 0:
        logger.info(
            "Audio download reused existing file",
            extra={
                "component": "audio_pipeline",
                "operation": "download_audio",
                "status": "cached",
                "duration_ms": _duration_ms(started_at),
                "context_data": {
                    "output_stem": output_stem,
                    "audio_size_bytes": existing.stat().st_size,
                    "use_youtube_config": use_youtube_config,
                },
            },
        )
        return existing

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "noplaylist": True,
        "no_check_certificate": True,
        "socket_timeout": 30,
        "logger": _YtDlpLogger(logger),
        "outtmpl": str(out_dir / f"{output_stem}.%(ext)s"),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.6613.120 Safari/537.36"
            )
        },
    }

    if use_youtube_config:
        client_config = load_youtube_client_config()
        cookies_path = client_config.resolved_cookies_path()
        if cookies_path and cookies_path.exists():
            ydl_opts["cookiefile"] = str(cookies_path)
        elif cookies_path:
            logger.warning("YouTube cookies not found at %s", cookies_path)

        extractor_args = build_youtube_extractor_args()
        if extractor_args:
            ydl_opts["extractor_args"] = extractor_args

    logger.info(
        "Audio download started",
        extra={
            "component": "audio_pipeline",
            "operation": "download_audio",
            "status": "started",
            "context_data": {
                "output_stem": output_stem,
                "use_youtube_config": use_youtube_config,
            },
        },
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise ValueError(f"Failed to download audio for {url}")
        file_path = Path(ydl.prepare_filename(info))

    if not file_path.exists():
        match = next(out_dir.glob(f"{output_stem}.*"), None)
        if match and match.exists():
            logger.info(
                "Audio download completed via matched output",
                extra={
                    "component": "audio_pipeline",
                    "operation": "download_audio",
                    "status": "completed",
                    "duration_ms": _duration_ms(started_at),
                    "context_data": {
                        "output_stem": output_stem,
                        "audio_size_bytes": match.stat().st_size,
                        "use_youtube_config": use_youtube_config,
                    },
                },
            )
            return match
        raise FileNotFoundError(f"Downloaded audio not found at {file_path}")

    logger.info(
        "Audio download completed",
        extra={
            "component": "audio_pipeline",
            "operation": "download_audio",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "context_data": {
                "output_stem": output_stem,
                "audio_size_bytes": file_path.stat().st_size,
                "use_youtube_config": use_youtube_config,
            },
        },
    )
    return file_path


def transcribe_audio_file_with_metadata(path: Path) -> tuple[str, str | None]:
    """Transcribe an audio file and return transcript text plus detected language."""
    started_at = time.perf_counter()
    logger.info(
        "Audio file transcription started",
        extra={
            "component": "audio_pipeline",
            "operation": "transcribe_audio_file",
            "status": "started",
            "context_data": {
                "file_name": path.name,
                "audio_size_bytes": path.stat().st_size if path.exists() else None,
            },
        },
    )
    service = get_whisper_local_service()
    transcript_text, detected_language = service.transcribe_audio(path)
    stripped = transcript_text.strip()
    logger.info(
        "Audio file transcription completed",
        extra={
            "component": "audio_pipeline",
            "operation": "transcribe_audio_file",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "context_data": {
                "file_name": path.name,
                "audio_size_bytes": path.stat().st_size if path.exists() else None,
                "language": detected_language,
                "transcript_chars": len(stripped),
            },
        },
    )
    return stripped, detected_language


def transcribe_audio_file(path: Path) -> str:
    """Transcribe an audio file and return transcript text."""
    transcript_text, _detected_language = transcribe_audio_file_with_metadata(path)
    return transcript_text
