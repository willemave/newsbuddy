import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.observability import build_log_extra, sanitize_url_for_logs
from app.core.settings import get_settings
from app.models.contracts import ContentStatus
from app.models.db import Content
from app.models.domain.content import ContentData
from app.models.domain.content_mapper import content_to_domain, domain_to_content
from app.services.agent_vm_runtime import resolve_sandbox_user_id
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.audio_pipeline import (
    download_audio_via_ytdlp,
    transcribe_audio_file_with_metadata,
)
from app.services.content_bodies import sync_content_body_storage
from app.services.http import get_http_service
from app.services.queue import TaskType, get_queue_service
from app.services.sandbox_media_download import download_remote_media_in_sandbox
from app.utils.url_utils import is_domain_or_subdomain

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


class PodcastMediaWorker:
    """Worker for the full podcast media hot path on local scratch storage."""

    def __init__(self) -> None:
        self.scratch_root = settings.podcast_scratch_root
        self.scratch_root.mkdir(parents=True, exist_ok=True)
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

    def _validate_url(self, url: str) -> bool:
        """Validate URL format and basic reachability."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                logger.error(
                    "Invalid podcast URL format",
                    extra=self._log_extra(
                        operation="validate_url",
                        status="failed",
                        context_data={"url": sanitize_url_for_logs(url)},
                    ),
                )
                return False

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
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Podcast URL validation raised exception",
                extra=self._log_extra(
                    operation="validate_url",
                    status="failed",
                    context_data={
                        "url": sanitize_url_for_logs(url),
                        "failure_class": type(exc).__name__,
                    },
                ),
            )
            return False

    @staticmethod
    def _is_apple_podcasts_url(url: str) -> bool:
        return is_domain_or_subdomain(urlparse(url).hostname, "podcasts.apple.com")

    def _extract_actual_audio_url(self, url: str) -> str:
        """
        Extract the actual audio URL from redirect URLs.

        Some podcast platforms, like Anchor.fm, include the real audio URL as an
        encoded path segment.
        """
        if is_domain_or_subdomain(urlparse(url).hostname, "anchor.fm") and "https%3A%2F%2F" in url:
            for part in url.split("/"):
                if "https%3A%2F%2F" in part:
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

    def _is_youtube_url(self, url: str) -> bool:
        """Check if URL is a YouTube URL."""
        parsed = urlparse(url)
        if is_domain_or_subdomain(parsed.hostname, "youtu.be"):
            return bool(parsed.path.strip("/"))
        if not is_domain_or_subdomain(parsed.hostname, "youtube.com"):
            return False
        if parsed.path.rstrip("/") == "/watch":
            return bool(parse_qs(parsed.query).get("v", [None])[0])
        return parsed.path.startswith(("/embed/", "/v/", "/shorts/"))

    def _extract_youtube_id(self, url: str) -> str | None:
        parsed = urlparse(url)
        if is_domain_or_subdomain(parsed.hostname, "youtu.be"):
            return parsed.path.lstrip("/") or None
        if not is_domain_or_subdomain(parsed.hostname, "youtube.com"):
            return None
        if "v=" in parsed.query:
            for part in parsed.query.split("&"):
                if part.startswith("v="):
                    return part.split("=", 1)[1]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
        return None

    def _download_youtube_audio(
        self,
        url: str,
        title: str | None,
        content_id: int,
        scratch_dir: Path,
    ) -> Path:
        youtube_dir = scratch_dir / "youtube"
        video_id = self._extract_youtube_id(url)
        sanitized_title = sanitize_filename(title or f"youtube_{content_id}")
        stem = f"{sanitized_title}-{video_id}" if video_id else sanitized_title

        return download_audio_via_ytdlp(
            url,
            youtube_dir,
            output_stem=stem,
            use_youtube_config=True,
        )

    def _resolve_audio_url(self, content, db_content: Content) -> str | None:  # noqa: ANN001
        audio_url = content.metadata.get("audio_url")
        if audio_url:
            return str(audio_url)

        platform = (content.metadata.get("platform") or db_content.platform or "").lower()
        is_apple_url = self._is_apple_podcasts_url(str(content.url))
        if platform == "apple_podcasts" or is_apple_url:
            resolution = resolve_apple_podcast_episode(
                str(content.url),
                feed_fetch=get_http_service().fetch_bounded_public,
            )
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

    @staticmethod
    def _sandbox_user_id(content: ContentData) -> int:
        return resolve_sandbox_user_id(content.metadata.get("submitted_by_user_id"))

    def _download_to_scratch(
        self,
        *,
        content_id: int,
        title: str | None,
        audio_url: str,
        scratch_dir: Path,
        sandbox_user_id: int,
    ) -> Path:
        scratch_dir.mkdir(parents=True, exist_ok=True)
        if self._is_youtube_url(audio_url):
            return self._download_youtube_audio(audio_url, title, content_id, scratch_dir)

        resolved_audio_url = self._extract_actual_audio_url(audio_url)
        if not self._validate_url(resolved_audio_url):
            raise ValueError("Invalid audio URL format")

        extension = get_file_extension_from_url(resolved_audio_url)
        filename = f"{sanitize_filename(title or f'podcast_{content_id}')}{extension}"
        audio_path = scratch_dir / filename
        logger.info(
            "Podcast sandbox download started",
            extra=self._log_extra(
                operation="download_audio",
                content_id=content_id,
                status="started",
                context_data={"audio_url": sanitize_url_for_logs(resolved_audio_url)},
            ),
        )
        result = download_remote_media_in_sandbox(
            resolved_audio_url,
            audio_path,
            user_id=sandbox_user_id,
            execution_id=content_id,
        )
        logger.info(
            "Podcast sandbox download completed",
            extra=self._log_extra(
                operation="download_audio",
                content_id=content_id,
                status="completed",
                context_data={"file_path": str(result), "file_size": result.stat().st_size},
            ),
        )
        return result

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
                        sandbox_user_id=self._sandbox_user_id(content),
                    )
                    normalized_audio_path = self._normalize_audio_file(audio_path)
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
