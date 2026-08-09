"""Reusable audio download and transcription primitives."""

from __future__ import annotations

import multiprocessing
import os
import signal
import time
from contextlib import suppress
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.scraping.youtube_config import load_youtube_client_config

try:  # pragma: no cover - optional dependency in tests
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

logger = get_logger(__name__)

MAX_YTDLP_AUDIO_BYTES = 500_000_000
YTDLP_DOWNLOAD_TIMEOUT_SECONDS = 600.0


class YtDlpDownloadDeadlineExceeded(RuntimeError):
    """Raised when a yt-dlp download exceeds its total wall-clock budget."""


class YtDlpDownloadSizeExceeded(RuntimeError):
    """Raised when a yt-dlp download exceeds its maximum output size."""


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


def _enforce_ytdlp_progress_limits(
    status: dict[str, Any],
    *,
    deadline: float,
) -> None:
    if time.monotonic() >= deadline:
        raise YtDlpDownloadDeadlineExceeded("yt-dlp audio download exceeded its deadline")

    for field in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
        value = status.get(field)
        if isinstance(value, int) and value > MAX_YTDLP_AUDIO_BYTES:
            raise YtDlpDownloadSizeExceeded("yt-dlp audio download exceeded its byte limit")

    for field in ("tmpfilename", "filename"):
        value = status.get(field)
        if not isinstance(value, str):
            continue
        try:
            size = Path(value).stat().st_size
        except OSError:
            continue
        if size > MAX_YTDLP_AUDIO_BYTES:
            raise YtDlpDownloadSizeExceeded("yt-dlp audio download exceeded its byte limit")


def _download_audio_in_child(
    url: str,
    ydl_opts: dict[str, Any],
    deadline: float,
    result_connection: Connection,
) -> None:
    try:
        with suppress(OSError):
            os.setsid()
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is required to download audio")
        child_opts = dict(ydl_opts)
        child_opts["logger"] = _YtDlpLogger(logger)
        child_opts["progress_hooks"] = [
            lambda status: _enforce_ytdlp_progress_limits(status, deadline=deadline)
        ]
        with yt_dlp.YoutubeDL(child_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise ValueError(f"Failed to download audio for {url}")
            file_path = Path(ydl.prepare_filename(info))
        result_connection.send(("ok", str(file_path), ""))
    except Exception as exc:  # noqa: BLE001 - preserve provider failures across process boundary
        result_connection.send(("error", type(exc).__name__, str(exc)))
    finally:
        result_connection.close()


def _stop_ytdlp_process(process: BaseProcess) -> None:
    process_group_id: int | None = None
    if process.pid is not None:
        with suppress(OSError):
            candidate_group_id = os.getpgid(process.pid)
            if candidate_group_id == process.pid:
                process_group_id = candidate_group_id
    if process.is_alive() and process_group_id is not None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except OSError:
            process.terminate()
    elif process.is_alive():
        process.terminate()
    process.join(timeout=1)
    if process.is_alive():
        if process_group_id is not None:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except OSError:
                process.kill()
        else:
            process.kill()
        process.join()


def _run_ytdlp_with_deadline(
    url: str,
    ydl_opts: dict[str, Any],
    *,
    deadline: float,
) -> Path:
    process_context = multiprocessing.get_context("spawn")
    result_connection, child_connection = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_download_audio_in_child,
        args=(url, ydl_opts, deadline, child_connection),
        daemon=True,
    )
    try:
        process.start()
    except BaseException:
        result_connection.close()
        child_connection.close()
        if process.pid is not None:
            _stop_ytdlp_process(process)
        raise
    child_connection.close()
    try:
        remaining_seconds = max(0.0, deadline - time.monotonic())
        if not result_connection.poll(remaining_seconds):
            _stop_ytdlp_process(process)
            raise YtDlpDownloadDeadlineExceeded("yt-dlp audio download exceeded its deadline")
        try:
            status, value, message = result_connection.recv()
        except EOFError as exc:
            raise RuntimeError(
                f"yt-dlp audio download process exited with code {process.exitcode}"
            ) from exc
    finally:
        result_connection.close()
        process.join(timeout=1)
        if process.is_alive():
            _stop_ytdlp_process(process)

    if status == "ok":
        return Path(value)
    if value == YtDlpDownloadDeadlineExceeded.__name__:
        raise YtDlpDownloadDeadlineExceeded(message)
    if value == YtDlpDownloadSizeExceeded.__name__:
        raise YtDlpDownloadSizeExceeded(message)
    if status == "error":
        raise RuntimeError(f"yt-dlp failed with {value}: {message}")
    raise RuntimeError("yt-dlp audio download returned an invalid result")


def _remove_download_outputs(out_dir: Path, output_stem: str) -> None:
    for candidate in out_dir.glob(f"{output_stem}.*"):
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            logger.warning("Unable to remove incomplete yt-dlp output %s", candidate)


def _find_download_output(out_dir: Path, output_stem: str) -> Path | None:
    completed: Path | None = None
    for candidate in out_dir.glob(f"{output_stem}.*"):
        if candidate.name.endswith((".part", ".ytdl")) or ".part-" in candidate.name:
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink(missing_ok=True)
        elif completed is None and candidate.is_file():
            completed = candidate
    return completed


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
    deadline = time.monotonic() + YTDLP_DOWNLOAD_TIMEOUT_SECONDS
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = _find_download_output(out_dir, output_stem)
    if existing and existing.stat().st_size > MAX_YTDLP_AUDIO_BYTES:
        _remove_download_outputs(out_dir, output_stem)
        raise YtDlpDownloadSizeExceeded("Cached yt-dlp audio exceeded its byte limit")
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
        "max_filesize": MAX_YTDLP_AUDIO_BYTES,
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
    try:
        file_path = _run_ytdlp_with_deadline(url, ydl_opts, deadline=deadline)
        if not file_path.exists():
            match = _find_download_output(out_dir, output_stem)
            if match is None:
                raise FileNotFoundError(f"Downloaded audio not found at {file_path}")
            file_path = match
        audio_size_bytes = file_path.stat().st_size
        if audio_size_bytes <= 0:
            raise ValueError(f"Downloaded audio is empty at {file_path}")
        if audio_size_bytes > MAX_YTDLP_AUDIO_BYTES:
            raise YtDlpDownloadSizeExceeded("yt-dlp audio download exceeded its byte limit")
    except BaseException:
        _remove_download_outputs(out_dir, output_stem)
        raise

    logger.info(
        "Audio download completed",
        extra={
            "component": "audio_pipeline",
            "operation": "download_audio",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "context_data": {
                "output_stem": output_stem,
                "audio_size_bytes": audio_size_bytes,
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
    # Imported lazily: whisper pulls in torch, which must stay out of the import
    # graph of every non-media worker process.
    from app.services.whisper_local import get_whisper_local_service

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
