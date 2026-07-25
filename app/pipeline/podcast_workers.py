import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra, sanitize_url_for_logs
from app.core.settings import get_settings
from app.models.contracts import ContentStatus
from app.models.db import Content
from app.models.domain.content import ContentData
from app.models.domain.content_mapper import content_to_domain, domain_to_content
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.audio_pipeline import (
    download_audio_via_ytdlp,
    transcribe_audio_file_with_metadata,
)
from app.services.content_bodies import sync_content_body_storage
from app.services.queue import TaskType, get_queue_service

# Resolve project root (two levels up from this file: app/ → project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

logger = get_logger(__name__)
settings = get_settings()

DIRECT_AUDIO_FILE_EXTENSIONS = (
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mpga",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
)


def sanitize_filename(title: str) -> str:
    """Sanitizes a title to be a valid filename."""
    # Remove invalid characters
    sanitized = re.sub(r"[^\w\s-]", "", title).strip()
    # Replace spaces with hyphens
    sanitized = re.sub(r"[-\s]+", "-", sanitized)
    # Truncate to a reasonable length
    return sanitized[:100]


def get_file_extension_from_url(url: str) -> str:
    """Extract file extension from URL."""
    parsed = urlparse(url)
    path = parsed.path
    if "." in path:
        return os.path.splitext(path)[1]
    return ".mp3"  # Default to mp3


def _is_direct_audio_file_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    path = unquote(parsed.path).lower()
    return path.endswith(DIRECT_AUDIO_FILE_EXTENSIONS)


def _direct_audio_url_from_content(content: ContentData) -> str | None:
    candidates = [str(content.url)]
    source_url = str(content.source_url) if content.source_url else None
    if source_url and source_url not in candidates:
        candidates.append(source_url)

    for candidate in candidates:
        if _is_direct_audio_file_url(candidate):
            return candidate
    return None


class PodcastDownloadWorker:
    """Worker for downloading podcast audio files."""

    def __init__(self):
        # Resolve to an absolute path so downstream workers can rely on it even if
        # the process is started from a different working directory.
        self.base_dir = settings.podcast_media_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.queue_service = get_queue_service()

    @staticmethod
    def _log_extra(
        *,
        operation: str,
        content_id: int | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        context_data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_log_extra(
            component="podcast_download_worker",
            operation=operation,
            event_name="content.download_audio",
            status=status,
            duration_ms=duration_ms,
            content_id=content_id,
            context_data=context_data,
        )

    def _validate_url(self, url: str) -> bool:
        """Validate URL format and basic reachability."""
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                logger.error(
                    "Invalid podcast URL format",
                    extra=self._log_extra(
                        operation="validate_url",
                        status="failed",
                        context_data={"url": sanitize_url_for_logs(url)},
                    ),
                )
                return False

            # Check for obvious invalid characters
            if any(char in url for char in [" ", "\n", "\r", "\t"]):
                logger.error(
                    "Podcast URL contains invalid characters",
                    extra=self._log_extra(
                        operation="validate_url",
                        status="failed",
                        context_data={"url": sanitize_url_for_logs(url)},
                    ),
                )
                return False

            return True
        except Exception as e:
            logger.error(
                "Podcast URL validation raised exception",
                extra=self._log_extra(
                    operation="validate_url",
                    status="failed",
                    context_data={
                        "url": sanitize_url_for_logs(url),
                        "failure_class": type(e).__name__,
                    },
                ),
            )
            return False

    @staticmethod
    def _is_apple_podcasts_url(url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host.endswith("podcasts.apple.com")

    def _extract_actual_audio_url(self, url: str) -> str:
        """
        Extract the actual audio URL from redirect URLs.

        Some podcast platforms (like Anchor.fm) use redirect URLs that contain
        the actual audio URL as an encoded parameter.
        """
        # Check if this is an Anchor.fm redirect URL
        if "anchor.fm" in url and "https%3A%2F%2F" in url:
            # Find the encoded URL in the path
            parts = url.split("/")
            for part in parts:
                if "https%3A%2F%2F" in part:
                    # Decode the URL
                    decoded_url = unquote(part)
                    logger.info(
                        "Resolved anchor redirect URL",
                        extra=self._log_extra(
                            operation="resolve_audio_url",
                            status="completed",
                            context_data={"resolved_url": sanitize_url_for_logs(decoded_url)},
                        ),
                    )
                    return decoded_url

        return url

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(
            (
                httpx.ConnectError,
                httpx.TimeoutException,
                OSError,  # This includes DNS resolution errors
            )
        ),
    )
    def _download_with_retry(self, audio_url: str, file_path: Path) -> None:
        """Download file with retry logic for network issues."""
        logger.info(
            "Podcast download attempt started",
            extra=self._log_extra(
                operation="download_audio",
                status="started",
                context_data={
                    "audio_url": sanitize_url_for_logs(audio_url),
                    "file_path": str(file_path),
                },
            ),
        )

        # Use sync httpx client with proper timeout configuration
        timeout = httpx.Timeout(
            timeout=300.0,  # Default timeout
            connect=30.0,  # DNS resolution timeout
            read=300.0,  # Large file download timeout
            write=30.0,
            pool=10.0,
        )

        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0; Podcast Downloader)"}

        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            # First, try a HEAD request to validate the URL
            try:
                head_response = client.head(audio_url)
                head_response.raise_for_status()
                content_length = head_response.headers.get("content-length", "unknown")
                logger.info(
                    "Podcast URL validated",
                    extra=self._log_extra(
                        operation="validate_audio_url",
                        status="completed",
                        context_data={
                            "audio_url": sanitize_url_for_logs(audio_url),
                            "content_length": content_length,
                        },
                    ),
                )
            except Exception as e:
                logger.warning(
                    "Podcast HEAD request failed; proceeding with GET",
                    extra=self._log_extra(
                        operation="validate_audio_url",
                        status="failed",
                        context_data={
                            "audio_url": sanitize_url_for_logs(audio_url),
                            "failure_class": type(e).__name__,
                        },
                    ),
                )

            # Download the file
            with client.stream("GET", audio_url) as response:
                response.raise_for_status()

                # Create directory if it doesn't exist
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # Write file in chunks
                with open(file_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

                logger.info(
                    "Podcast download attempt completed",
                    extra=self._log_extra(
                        operation="download_audio",
                        status="completed",
                        context_data={
                            "audio_url": sanitize_url_for_logs(audio_url),
                            "file_path": str(file_path),
                            "file_size": file_path.stat().st_size,
                        },
                    ),
                )

    def _is_youtube_url(self, url: str) -> bool:
        """Check if URL is a YouTube URL."""
        youtube_patterns = [
            r"youtube\.com/watch\?v=",
            r"youtu\.be/",
            r"youtube\.com/embed/",
            r"m\.youtube\.com/watch\?v=",
            r"youtube\.com/v/",
            r"youtube\.com/shorts/",
        ]
        return any(re.search(pattern, url) for pattern in youtube_patterns)

    def _extract_youtube_id(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.netloc.endswith("youtu.be"):
            return parsed.path.lstrip("/") or None
        if "v=" in parsed.query:
            for part in parsed.query.split("&"):
                if part.startswith("v="):
                    return part.split("=", 1)[1]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
        return None

    def _download_youtube_audio(self, url: str, title: str | None, content_id: int) -> Path:
        youtube_dir = self.base_dir / "youtube"

        video_id = self._extract_youtube_id(url)
        sanitized_title = sanitize_filename(title or f"youtube_{content_id}")
        stem = f"{sanitized_title}-{video_id}" if video_id else sanitized_title

        return download_audio_via_ytdlp(
            url,
            youtube_dir,
            output_stem=stem,
            use_youtube_config=True,
        )

    def process_download_task(self, content_id: int) -> bool:
        """
        Download a podcast audio file.

        Args:
            content_id: ID of the content to download

        Returns:
            True if successful, False otherwise
        """
        logger.info(
            "Podcast download task started",
            extra=self._log_extra(
                operation="download_audio",
                content_id=content_id,
                status="started",
            ),
        )
        file_path = None
        download_started_at = datetime.now(UTC)

        try:
            with get_db() as db:
                # Get content
                db_content = db.query(Content).filter(Content.id == content_id).first()

                if not db_content:
                    logger.error(f"Content {content_id} not found")
                    return False

                content = content_to_domain(db_content)

                # Get audio URL from metadata
                audio_url = content.metadata.get("audio_url")
                if not audio_url:
                    platform = (
                        content.metadata.get("platform") or db_content.platform or ""
                    ).lower()
                    if platform == "apple_podcasts" or self._is_apple_podcasts_url(
                        str(content.url)
                    ):
                        resolution = resolve_apple_podcast_episode(str(content.url))
                        if resolution.feed_url:
                            content.metadata.setdefault("feed_url", resolution.feed_url)
                        if resolution.episode_title:
                            content.metadata.setdefault("episode_title", resolution.episode_title)
                            if not content.title:
                                content.title = resolution.episode_title
                        if resolution.audio_url:
                            content.metadata["audio_url"] = resolution.audio_url
                            domain_to_content(content, db_content)
                            db.commit()
                            audio_url = resolution.audio_url

                    if not audio_url:
                        audio_url = _direct_audio_url_from_content(content)
                        if audio_url:
                            content.metadata["audio_url"] = audio_url
                            logger.info(
                                "Resolved podcast audio URL from content URL",
                                extra=self._log_extra(
                                    operation="resolve_audio_url",
                                    content_id=content_id,
                                    status="completed",
                                    context_data={"audio_url": sanitize_url_for_logs(audio_url)},
                                ),
                            )

                    if not audio_url:
                        platform = db_content.platform or content.metadata.get("platform")
                        logger.error(
                            "No podcast audio URL found",
                            extra=self._log_extra(
                                operation="resolve_audio_url",
                                content_id=content_id,
                                status="failed",
                                context_data={"platform": platform},
                            ),
                        )
                        db_content.status = ContentStatus.FAILED.value
                        db_content.error_message = "No audio URL found"
                        content.status = ContentStatus.FAILED
                        content.error_message = "No audio URL found"
                        domain_to_content(content, db_content)
                        db.commit()
                        return False

                # Check if this is a YouTube URL
                if self._is_youtube_url(audio_url):
                    logger.info(
                        "Podcast source resolved to YouTube",
                        extra=self._log_extra(
                            operation="resolve_audio_url",
                            content_id=content_id,
                            status="completed",
                            context_data={
                                "platform": "youtube",
                                "audio_url": sanitize_url_for_logs(audio_url),
                            },
                        ),
                    )
                    file_path = self._download_youtube_audio(audio_url, content.title, content_id)

                    content.metadata["youtube_video"] = True
                    content.metadata["file_path"] = str(file_path)
                    content.metadata["download_date"] = datetime.now(UTC).isoformat()
                    content.metadata["file_size"] = file_path.stat().st_size

                    # Update database
                    domain_to_content(content, db_content)
                    db.commit()

                    # Queue transcription task directly (transcriber will handle YouTube)
                    self.queue_service.enqueue(TaskType.TRANSCRIBE, content_id=content_id)

                    youtube_duration_ms = (
                        datetime.now(UTC) - download_started_at
                    ).total_seconds() * 1000
                    logger.info(
                        "YouTube audio prepared for transcription",
                        extra=self._log_extra(
                            operation="download_audio",
                            content_id=content_id,
                            status="completed",
                            duration_ms=youtube_duration_ms,
                            context_data={
                                "platform": "youtube",
                                "file_path": str(file_path),
                                "file_size": file_path.stat().st_size,
                                "file_extension": file_path.suffix,
                                "next_task_types": [TaskType.TRANSCRIBE.value],
                            },
                        ),
                    )

                    return True

                # Extract actual audio URL if it's a redirect
                audio_url = self._extract_actual_audio_url(audio_url)

                # Validate URL format
                if not self._validate_url(audio_url):
                    logger.error(
                        "Invalid podcast audio URL",
                        extra=self._log_extra(
                            operation="validate_url",
                            content_id=content_id,
                            status="failed",
                            context_data={"audio_url": sanitize_url_for_logs(audio_url)},
                        ),
                    )
                    db_content.status = ContentStatus.FAILED.value
                    db_content.error_message = "Invalid audio URL format"
                    db.commit()
                    return False

                # Get podcast feed name from metadata (try multiple keys)
                feed_name = (
                    content.metadata.get("feed_name")
                    or content.metadata.get("podcast_feed_name")
                    or "unknown_feed"
                )

                # Create directory structure
                feed_dir = self.base_dir / sanitize_filename(feed_name)
                feed_dir.mkdir(parents=True, exist_ok=True)

                # Determine file extension and create file path
                file_extension = get_file_extension_from_url(audio_url)
                sanitized_title = sanitize_filename(content.title or f"podcast_{content_id}")
                filename = f"{sanitized_title}{file_extension}"
                file_path = feed_dir / filename

                # Check if file already exists
                if file_path.exists() and file_path.stat().st_size > 0:
                    logger.info(
                        "Reusing existing podcast download",
                        extra=self._log_extra(
                            operation="download_audio",
                            content_id=content_id,
                            status="completed",
                            context_data={
                                "reuse_existing_file": True,
                                "audio_url": sanitize_url_for_logs(audio_url),
                                "file_path": str(file_path),
                                "file_size": file_path.stat().st_size,
                                "file_extension": file_path.suffix,
                                "next_task_types": [TaskType.TRANSCRIBE.value],
                            },
                        ),
                    )
                    content.metadata["file_path"] = str(file_path)
                    content.metadata["download_date"] = datetime.now(UTC).isoformat()
                    content.metadata["file_size"] = file_path.stat().st_size

                    # Update database
                    domain_to_content(content, db_content)
                    db.commit()

                    # Queue transcription task
                    self.queue_service.enqueue(TaskType.TRANSCRIBE, content_id=content_id)

                    return True

                # Download the audio file with retry logic
                self._download_with_retry(audio_url, file_path)

                # Verify file was written correctly
                if not file_path.exists() or file_path.stat().st_size == 0:
                    raise Exception("Downloaded file is empty or doesn't exist")

                # Update content metadata
                content.metadata["file_path"] = str(file_path)
                content.metadata["download_date"] = datetime.now(UTC).isoformat()
                content.metadata["file_size"] = file_path.stat().st_size

                # Update database
                domain_to_content(content, db_content)
                db.commit()

                file_size = file_path.stat().st_size
                download_duration_ms = (
                    datetime.now(UTC) - download_started_at
                ).total_seconds() * 1000
                logger.info(
                    "Podcast download completed",
                    extra=self._log_extra(
                        operation="download_audio",
                        content_id=content_id,
                        status="completed",
                        duration_ms=download_duration_ms,
                        context_data={
                            "platform": content.metadata.get("platform") or db_content.platform,
                            "audio_url": sanitize_url_for_logs(audio_url),
                            "file_path": str(file_path),
                            "file_size": file_size,
                            "file_extension": file_path.suffix,
                            "reuse_existing_file": False,
                            "next_task_types": [TaskType.TRANSCRIBE.value],
                        },
                    ),
                )

                # Queue transcription task
                self.queue_service.enqueue(TaskType.TRANSCRIBE, content_id=content_id)

                return True

        except Exception as e:
            error_msg = str(e)
            failed_audio_url = sanitize_url_for_logs(audio_url) if "audio_url" in locals() else None
            failed_duration_ms = (datetime.now(UTC) - download_started_at).total_seconds() * 1000
            logger.exception(
                "Podcast download failed",
                extra=self._log_extra(
                    operation="download_audio",
                    content_id=content_id,
                    status="failed",
                    duration_ms=failed_duration_ms,
                    context_data={
                        "audio_url": failed_audio_url,
                        "file_path": str(file_path) if file_path else None,
                        "failure_class": type(e).__name__,
                    },
                ),
            )

            # Clean up partial download if exists
            if file_path and Path(file_path).exists():
                try:
                    Path(file_path).unlink()
                    logger.info(
                        "Cleaned up partial podcast download",
                        extra=self._log_extra(
                            operation="cleanup_partial_download",
                            content_id=content_id,
                            status="completed",
                            context_data={"file_path": str(file_path)},
                        ),
                    )
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to clean up partial podcast download",
                        extra=self._log_extra(
                            operation="cleanup_partial_download",
                            content_id=content_id,
                            status="failed",
                            context_data={"failure_class": type(cleanup_error).__name__},
                        ),
                    )

            # Update content with error
            try:
                with get_db() as db:
                    db_content = db.query(Content).filter(Content.id == content_id).first()
                    if db_content:
                        db_content.status = ContentStatus.FAILED.value
                        db_content.error_message = error_msg[:500]  # Limit error message length
                        db_content.retry_count = (db_content.retry_count or 0) + 1
                        db.commit()
            except Exception as db_error:
                logger.error(
                    "Failed to persist podcast download error",
                    extra=self._log_extra(
                        operation="persist_error",
                        content_id=content_id,
                        status="failed",
                        context_data={"failure_class": type(db_error).__name__},
                    ),
                )

            return False


class PodcastMediaWorker:
    """Worker for the full podcast media hot path on local scratch storage."""

    def __init__(self) -> None:
        self.scratch_root = settings.podcast_scratch_root
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.queue_service = get_queue_service()
        self.download_worker = PodcastDownloadWorker()
        self.transcribe_worker = PodcastTranscribeWorker()

    @staticmethod
    def _log_extra(
        *,
        operation: str,
        content_id: int | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        context_data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_log_extra(
            component="podcast_media_worker",
            operation=operation,
            event_name="content.process_podcast_media",
            status=status,
            duration_ms=duration_ms,
            content_id=content_id,
            context_data=context_data,
        )

    def _scratch_dir(self, content_id: int) -> Path:
        return self.scratch_root / f"content-{content_id}"

    def _resolve_audio_url(self, content, db_content: Content) -> str | None:  # noqa: ANN001
        audio_url = content.metadata.get("audio_url")
        if audio_url:
            return str(audio_url)

        platform = (content.metadata.get("platform") or db_content.platform or "").lower()
        is_apple_url = self.download_worker._is_apple_podcasts_url(str(content.url))
        if platform == "apple_podcasts" or is_apple_url:
            resolution = resolve_apple_podcast_episode(str(content.url))
            if resolution.feed_url:
                content.metadata.setdefault("feed_url", resolution.feed_url)
            if resolution.episode_title:
                content.metadata.setdefault("episode_title", resolution.episode_title)
                if not content.title:
                    content.title = resolution.episode_title
            if resolution.audio_url:
                content.metadata["audio_url"] = resolution.audio_url
                return resolution.audio_url

        audio_url = _direct_audio_url_from_content(content)
        if audio_url:
            content.metadata["audio_url"] = audio_url
            logger.info(
                "Resolved podcast audio URL from content URL",
                extra=self._log_extra(
                    operation="resolve_audio_url",
                    content_id=content.id,
                    status="completed",
                    context_data={"audio_url": sanitize_url_for_logs(audio_url)},
                ),
            )
            return audio_url

        return None

    def _download_to_scratch(
        self,
        *,
        content_id: int,
        title: str | None,
        audio_url: str,
        scratch_dir: Path,
    ) -> Path:
        scratch_dir.mkdir(parents=True, exist_ok=True)
        helper = self.download_worker
        original_base_dir = helper.base_dir
        helper.base_dir = scratch_dir
        try:
            if helper._is_youtube_url(audio_url):
                return helper._download_youtube_audio(audio_url, title, content_id)

            resolved_audio_url = helper._extract_actual_audio_url(audio_url)
            if not helper._validate_url(resolved_audio_url):
                raise ValueError("Invalid audio URL format")

            extension = get_file_extension_from_url(resolved_audio_url)
            filename = f"{sanitize_filename(title or f'podcast_{content_id}')}{extension}"
            audio_path = scratch_dir / filename
            helper._download_with_retry(resolved_audio_url, audio_path)
            return audio_path
        finally:
            helper.base_dir = original_base_dir

    def _normalize_audio_file(self, audio_path: Path) -> Path:
        ffmpeg_binary = shutil.which("ffmpeg")
        if ffmpeg_binary is None:
            return audio_path

        normalized_path = audio_path.with_suffix(".normalized.wav")
        result = subprocess.run(
            [
                ffmpeg_binary,
                "-y",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(normalized_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not normalized_path.exists():
            return audio_path
        return normalized_path

    def process_media_task(self, content_id: int) -> bool:
        """Run podcast download + local normalization + transcription in one task."""
        started_at = datetime.now(UTC)
        logger.info(
            "Podcast media task started",
            extra=self._log_extra(
                operation="process_podcast_media",
                content_id=content_id,
                status="started",
            ),
        )
        scratch_dir = self._scratch_dir(content_id)

        try:
            with get_db() as db:
                db_content = db.query(Content).filter(Content.id == content_id).first()
                if not db_content:
                    logger.error("Content %s not found", content_id)
                    return False

                content = content_to_domain(db_content)
                transcript_text = None
                reused_embedded_transcript = False
                if content.metadata.get("youtube_video"):
                    existing_transcript = content.metadata.get("transcript")
                    transcript_candidate = existing_transcript or content.metadata.get(
                        "content_to_summarize"
                    )
                    if isinstance(transcript_candidate, str) and transcript_candidate.strip():
                        transcript_text = transcript_candidate.strip()
                        reused_embedded_transcript = True

                detected_language = None
                if transcript_text is None:
                    audio_url = self._resolve_audio_url(content, db_content)
                    if not audio_url:
                        db_content.status = ContentStatus.FAILED.value
                        db_content.error_message = "No audio URL found"
                        db.commit()
                        return False

                    audio_path = self._download_to_scratch(
                        content_id=content_id,
                        title=content.title,
                        audio_url=audio_url,
                        scratch_dir=scratch_dir,
                    )
                    normalized_audio_path = self._normalize_audio_file(audio_path)
                    self.transcribe_worker._get_transcription_service()
                    transcript_text, detected_language = transcribe_audio_file_with_metadata(
                        normalized_audio_path
                    )

                content.metadata["transcription_date"] = datetime.now(UTC).isoformat()
                content.metadata["transcription_service"] = (
                    "youtube" if content.metadata.get("youtube_video") else "whisper_local"
                )
                content.metadata["transcript"] = transcript_text
                if detected_language:
                    content.metadata["detected_language"] = detected_language
                content.status = ContentStatus.PROCESSING
                content.processed_at = datetime.now(UTC)

                domain_to_content(content, db_content)
                sync_content_body_storage(db, content=db_content)
                db.commit()

                self.queue_service.enqueue(TaskType.SUMMARIZE, content_id=content_id)
                duration_ms = (datetime.now(UTC) - started_at).total_seconds() * 1000
                logger.info(
                    "Podcast media task completed",
                    extra=self._log_extra(
                        operation="process_podcast_media",
                        content_id=content_id,
                        status="completed",
                        duration_ms=duration_ms,
                        context_data={
                            "scratch_dir": str(scratch_dir),
                            "reused_embedded_transcript": reused_embedded_transcript,
                            "detected_language": detected_language,
                            "transcript_chars": len(transcript_text),
                            "next_task_types": [TaskType.SUMMARIZE.value],
                        },
                    ),
                )
                return True
        except Exception as exc:  # noqa: BLE001
            duration_ms = (datetime.now(UTC) - started_at).total_seconds() * 1000
            logger.exception(
                "Podcast media task failed",
                extra=self._log_extra(
                    operation="process_podcast_media",
                    content_id=content_id,
                    status="failed",
                    duration_ms=duration_ms,
                    context_data={"failure_class": type(exc).__name__},
                ),
            )
            try:
                with get_db() as db:
                    db_content = db.query(Content).filter(Content.id == content_id).first()
                    if db_content:
                        db_content.status = ContentStatus.FAILED.value
                        db_content.error_message = str(exc)[:500]
                        db_content.retry_count = (db_content.retry_count or 0) + 1
                        db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to persist podcast media failure for content %s",
                    content_id,
                )
            return False
        finally:
            if scratch_dir.exists():
                shutil.rmtree(scratch_dir, ignore_errors=True)


class PodcastTranscribeWorker:
    """Worker for transcribing podcast audio files using OpenAI."""

    def __init__(self):
        self.base_dir = settings.podcast_media_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.queue_service = get_queue_service()
        self.transcription_service = None

    @staticmethod
    def _log_extra(
        *,
        operation: str,
        content_id: int | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        context_data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_log_extra(
            component="podcast_transcribe_worker",
            operation=operation,
            event_name="content.transcribe",
            status=status,
            duration_ms=duration_ms,
            content_id=content_id,
            context_data=context_data,
        )

    def _get_transcription_service(self):
        """Lazy load the transcription service."""
        if self.transcription_service is None:
            # Imported lazily: whisper pulls in torch, which must stay out of the
            # import graph of every non-media worker process.
            from app.services.whisper_local import get_whisper_local_service

            try:
                self.transcription_service = get_whisper_local_service()
                logger.info(
                    "Whisper transcription service initialized",
                    extra=self._log_extra(operation="init_transcriber", status="completed"),
                )
            except Exception as e:
                logger.error(
                    "Failed to initialize transcription service",
                    extra=self._log_extra(
                        operation="init_transcriber",
                        status="failed",
                        context_data={"failure_class": type(e).__name__},
                    ),
                )
                raise

    def process_transcribe_task(self, content_id: int) -> bool:
        """
        Transcribe a podcast audio file.

        Args:
            content_id: ID of the content to transcribe

        Returns:
            True if successful, False otherwise
        """
        logger.info(
            "Podcast transcription task started",
            extra=self._log_extra(
                operation="transcribe",
                content_id=content_id,
                status="started",
            ),
        )
        transcribe_started_at = datetime.now(UTC)

        try:
            with get_db() as db:
                # Get content
                db_content = db.query(Content).filter(Content.id == content_id).first()

                if not db_content:
                    logger.error(f"Content {content_id} not found")
                    return False

                content = content_to_domain(db_content)

                # Check if this is a YouTube video with existing transcript
                if content.metadata.get("youtube_video") and content.metadata.get("transcript"):
                    transcript_chars = len(str(content.metadata.get("transcript") or ""))
                    logger.info(
                        "Reusing existing YouTube transcript",
                        extra=self._log_extra(
                            operation="transcribe",
                            content_id=content_id,
                            status="completed",
                            context_data={
                                "transcription_service": "youtube",
                                "transcript_chars": transcript_chars,
                                "next_task_types": [TaskType.SUMMARIZE.value],
                            },
                        ),
                    )

                    # YouTube transcript already available from strategy processing
                    transcript_text = content.metadata.get("transcript")
                    if not isinstance(transcript_text, str) or not transcript_text.strip():
                        raise ValueError("Missing YouTube transcript text")

                    # Create directory for YouTube transcripts
                    youtube_dir = self.base_dir / "youtube"
                    youtube_dir.mkdir(parents=True, exist_ok=True)

                    # Create text file path
                    sanitized_title = sanitize_filename(content.title or f"youtube_{content_id}")
                    text_path = youtube_dir / f"{sanitized_title}.txt"

                    # Write transcript to file
                    with open(text_path, "w", encoding="utf-8") as f:
                        f.write(transcript_text.strip())

                    logger.info(
                        "YouTube transcript persisted",
                        extra=self._log_extra(
                            operation="persist_transcript",
                            content_id=content_id,
                            status="completed",
                            context_data={"transcript_path": str(text_path)},
                        ),
                    )

                    # Update content metadata
                    content.metadata["transcript_path"] = str(text_path)
                    content.metadata["transcription_date"] = datetime.now(UTC).isoformat()
                    content.metadata["transcription_service"] = "youtube"

                    # Update database
                    domain_to_content(content, db_content)
                    db.commit()

                    # Queue summarization task
                    self.queue_service.enqueue(TaskType.SUMMARIZE, content_id=content_id)

                    return True

                # Get file path from metadata for regular podcasts
                file_path_value = content.metadata.get("file_path")
                if not file_path_value:
                    error_msg = "Audio file path missing in metadata"
                    logger.error(
                        error_msg,
                        extra=self._log_extra(
                            operation="load_audio",
                            content_id=content_id,
                            status="failed",
                        ),
                    )
                    db_content.status = ContentStatus.FAILED.value
                    db_content.error_message = error_msg
                    db.commit()
                    return False

                original_path = Path(file_path_value)
                if original_path.is_absolute():
                    audio_path = original_path
                else:
                    # Treat stored path as relative to the project root first
                    audio_path = (PROJECT_ROOT / original_path).resolve()

                    if not audio_path.exists():
                        parts = original_path.parts
                        if len(parts) >= 2 and parts[0:2] == ("data", "podcasts"):
                            tail = Path(*parts[2:])
                            audio_path = (self.base_dir / tail).resolve()
                        else:
                            audio_path = (self.base_dir / original_path).resolve()

                if not audio_path.exists():
                    error_msg = f"Audio file not found: {audio_path}"
                    logger.error(
                        "Podcast audio file not found",
                        extra=self._log_extra(
                            operation="load_audio",
                            content_id=content_id,
                            status="failed",
                            context_data={"audio_path": str(audio_path)},
                        ),
                    )
                    db_content.status = ContentStatus.FAILED.value
                    db_content.error_message = error_msg
                    db.commit()
                    return False

                # Normalise metadata so future retries use the absolute path
                if str(audio_path) != file_path_value:
                    content.metadata["file_path"] = str(audio_path)
                    domain_to_content(content, db_content)
                    db.commit()

                # Get transcription service and transcribe
                self._get_transcription_service()

                logger.info(
                    "Podcast transcription started",
                    extra=self._log_extra(
                        operation="transcribe",
                        content_id=content_id,
                        status="started",
                        context_data={"audio_path": str(audio_path)},
                    ),
                )

                # Transcribe the audio
                transcript_text, detected_language = self.transcription_service.transcribe_audio(
                    audio_path
                )

                # Create text file path (same directory as audio, but with .txt extension)
                base_path = audio_path.with_suffix("")
                text_path = base_path.with_suffix(".txt")

                # Write transcript to file
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(transcript_text.strip())

                transcribe_duration_ms = (
                    datetime.now(UTC) - transcribe_started_at
                ).total_seconds() * 1000
                logger.info(
                    "Podcast transcription completed",
                    extra=self._log_extra(
                        operation="transcribe",
                        content_id=content_id,
                        status="completed",
                        duration_ms=transcribe_duration_ms,
                        context_data={
                            "audio_path": str(audio_path),
                            "transcript_path": str(text_path),
                            "transcript_chars": len(transcript_text.strip()),
                            "detected_language": detected_language,
                            "transcription_service": "whisper_local",
                            "next_task_types": [TaskType.SUMMARIZE.value],
                        },
                    ),
                )

                # Update content metadata
                content.metadata["transcript_path"] = str(text_path)
                content.metadata["transcript"] = transcript_text.strip()
                content.metadata["transcription_date"] = datetime.now(UTC).isoformat()
                if detected_language:
                    content.metadata["detected_language"] = detected_language
                content.metadata["transcription_service"] = "whisper_local"

                # Update database
                domain_to_content(content, db_content)
                db.commit()

                # Queue summarization task
                self.queue_service.enqueue(TaskType.SUMMARIZE, content_id=content_id)

                return True

        except Exception as e:
            transcribe_failed_ms = (
                datetime.now(UTC) - transcribe_started_at
            ).total_seconds() * 1000
            logger.exception(
                "Podcast transcription failed",
                extra=self._log_extra(
                    operation="transcribe",
                    content_id=content_id,
                    status="failed",
                    duration_ms=transcribe_failed_ms,
                    context_data={"failure_class": type(e).__name__},
                ),
            )

            # Update content with error
            try:
                with get_db() as db:
                    db_content = db.query(Content).filter(Content.id == content_id).first()
                    if db_content:
                        db_content.status = ContentStatus.FAILED.value
                        db_content.error_message = str(e)
                        db_content.retry_count = (db_content.retry_count or 0) + 1
                        db.commit()
            except Exception:
                pass

            return False

    def cleanup_service(self):
        """Clean up the transcription service."""
        if self.transcription_service is not None:
            # Clean up local whisper model
            if hasattr(self.transcription_service, "cleanup_service"):
                self.transcription_service.cleanup_service()
            self.transcription_service = None
        logger.info(
            "Transcription service cleaned up",
            extra=self._log_extra(operation="cleanup_service", status="completed"),
        )
