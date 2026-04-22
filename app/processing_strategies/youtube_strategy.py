import json
import re
from datetime import datetime
from typing import Any

import httpx
import yt_dlp

from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.scraping.youtube_unified import YouTubeClientConfig, load_youtube_client_config

logger = get_logger(__name__)


class _YtDlpLogger:
    def __init__(self, base_logger):
        self._logger = base_logger

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        self._logger.warning(msg)


class YouTubeProcessorStrategy(UrlProcessorStrategy):
    """Processing strategy for YouTube videos using yt-dlp."""

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        self.client_config = self._load_client_config()
        self.ydl_opts = self._build_ydl_opts(self.client_config)

    def _build_ydl_opts(self, client_config: YouTubeClientConfig) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "ignoreerrors": False,  # Changed: Let exceptions bubble up for better error info
            "no_check_certificate": True,
            "logger": _YtDlpLogger(logger),
            "skip_download": True,  # Don't download video
            # Add user agent to avoid bot detection
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        cookies_path = client_config.resolved_cookies_path()
        if cookies_path and cookies_path.exists():
            opts["cookiefile"] = str(cookies_path)
        elif cookies_path:
            logger.warning("YouTube cookies not found at %s", cookies_path)

        extractor_args = self._build_extractor_args(client_config)
        if extractor_args:
            opts["extractor_args"] = extractor_args

        return opts

    def _load_client_config(self) -> YouTubeClientConfig:
        try:
            return load_youtube_client_config()
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Failed to load YouTube client config: %s", exc)
            return YouTubeClientConfig()

    @staticmethod
    def _build_extractor_args(
        client_config: YouTubeClientConfig,
    ) -> dict[str, dict[str, list[str]]]:
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

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """Check if this strategy can handle the given URL."""
        patterns = [
            r"youtube\.com/watch\?v=",
            r"youtu\.be/",
            r"youtube\.com/embed/",
            r"m\.youtube\.com/watch\?v=",
            r"youtube\.com/v/",
            r"youtube\.com/shorts/",
        ]
        return any(re.search(pattern, url) for pattern in patterns)

    async def download_content(self, url: str) -> bytes:
        """Download content from YouTube (returns empty bytes as we only need metadata)."""
        # We don't actually download the video, just return empty bytes
        # The actual content comes from the transcript
        return b""

    async def extract_data(
        self,
        content: bytes,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract metadata and transcript from YouTube video."""
        del content, context
        logger.info(f"Extracting YouTube data from: {url}")

        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            try:
                # Extract video info
                info = ydl.extract_info(url, download=False)

                # Defensive check: yt-dlp returns None for unavailable/private/restricted videos
                if info is None:
                    error_msg = (
                        f"Failed to extract video information from {url}. "
                        "Video may be unavailable, private, region-restricted, or age-restricted. "
                        f"yt-dlp version: {yt_dlp.version.__version__}"
                    )
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                # Extract basic metadata
                video_id = info.get("id")
                title = info.get("title", "Untitled")
                uploader = info.get("uploader", "Unknown")
                description = info.get("description", "")
                duration = info.get("duration", 0)
                upload_date = info.get("upload_date")
                view_count = info.get("view_count", 0)
                like_count = info.get("like_count", 0)
                thumbnail = info.get("thumbnail")

                transcript = await self._extract_transcript(info)

                # Parse upload date
                if upload_date:
                    publication_date = datetime.strptime(upload_date, "%Y%m%d")
                else:
                    publication_date = datetime.now()

                text_content = transcript or description
                if not text_content:
                    text_content = f"YouTube Video: {title}"

                return {
                    "title": title,
                    "author": uploader,
                    "publication_date": publication_date.isoformat(),
                    "text_content": text_content,
                    "content_type": "text",
                    "final_url_after_redirects": url,
                    "video_id": video_id,
                    "thumbnail_url": thumbnail,
                    "view_count": view_count,
                    "like_count": like_count,
                    # Use transcript if available, else description
                    "text": transcript or description,
                    "metadata": {
                        "platform": "youtube",  # Platform identifier
                        "source": f"youtube:{uploader}",  # Standardized format: platform:channel
                        "video_id": video_id,
                        "channel": uploader,
                        "duration": duration,
                        "description": description[:1000] if description else None,
                        "thumbnail_url": thumbnail,
                        "view_count": view_count,
                        "like_count": like_count,
                        "publication_date": publication_date.isoformat(),
                        "has_transcript": bool(transcript),
                        "transcript": transcript,
                        "audio_url": url,  # Store YouTube URL as audio_url for consistency
                        "video_url": url,  # Also store as video_url
                    },
                }

            except yt_dlp.utils.DownloadError as e:
                error_str = str(e)
                if self._should_skip_download_error(error_str):
                    logger.warning("Skipping YouTube video %s: %s", url, error_str)
                    return {
                        "skip_processing": True,
                        "skip_reason": "YouTube requires authentication or is a premiere",
                        "title": f"YouTube Video: {url}",
                        "content_type": "youtube",
                    }

                # For other download errors (geo-blocking, age-restriction, etc.), raise
                error_msg = f"YouTube download error for {url}: {error_str}"
                logger.error(error_msg)
                logger.error(
                    f"This may indicate: geo-blocking, age-restriction, "
                    f"rate-limiting, or outdated yt-dlp (current: {yt_dlp.version.__version__})"
                )
                raise ValueError(
                    f"Failed to extract YouTube video: {error_str}. "
                    "The video may be geo-blocked, age-restricted, or require authentication."
                ) from e
            except Exception as e:
                logger.error(f"Unexpected error extracting YouTube data from {url}: {e}")
                logger.error(f"yt-dlp version: {yt_dlp.version.__version__}")
                raise

    async def _extract_transcript(self, video_info: dict[str, Any]) -> str | None:
        """Extract transcript from video info."""
        try:
            # Check for subtitles
            subtitles = video_info.get("subtitles", {})
            automatic_captions = video_info.get("automatic_captions", {})

            # Prefer manual subtitles over automatic
            subtitle_tracks = subtitles.get("en", []) or automatic_captions.get("en", [])

            if not subtitle_tracks:
                logger.warning(f"No English subtitles found for video {video_info.get('id')}")
                return None

            # Get the first available format (usually vtt or srv3)
            for track in subtitle_tracks:
                if track.get("ext") in ["vtt", "srv3", "json3"]:
                    # yt-dlp can fetch the subtitle content
                    subtitle_url = track.get("url")
                    if subtitle_url:
                        transcript = await self._download_subtitle(subtitle_url, track.get("ext"))
                        if transcript:
                            return transcript

            # If we have subtitle data directly in the info
            requested_subtitles = video_info.get("requested_subtitles", {})
            if "en" in requested_subtitles and requested_subtitles["en"].get("data"):
                return self._parse_subtitle_data(requested_subtitles["en"]["data"])

            return None

        except Exception as e:
            logger.error(f"Error extracting transcript: {e}")
            return None

    async def _download_subtitle(self, url: str, ext: str) -> str | None:
        """Download and parse subtitle file."""
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()

                content = response.text

                # Parse based on format
                if ext == "vtt":
                    return self._parse_vtt(content)
                elif ext in ["srv3", "json3"]:
                    return self._parse_json_subtitle(content)
                else:
                    return content

        except Exception as e:
            logger.error(f"Error downloading subtitle: {e}")
            return None

    @staticmethod
    def _should_skip_download_error(error_message: str) -> bool:
        lowered = error_message.lower()
        if "sign in to confirm" in lowered:
            return True
        if "requires authentication" in lowered:
            return True
        return "premieres in" in lowered

    def _parse_vtt(self, vtt_content: str) -> str:
        """Parse VTT subtitle format to plain text."""
        lines = vtt_content.split("\n")
        transcript_lines = []

        # Skip header
        i = 0
        while i < len(lines) and not lines[i].strip().startswith("00:"):
            i += 1

        # Extract text
        while i < len(lines):
            line = lines[i].strip()
            # Skip timecodes and empty lines
            if "-->" in line or not line or line.startswith("00:"):
                i += 1
                continue
            # Skip tags
            line = re.sub(r"<[^>]+>", "", line)
            if line:
                transcript_lines.append(line)
            i += 1

        return " ".join(transcript_lines)

    def _parse_json_subtitle(self, json_content: str) -> str:
        """Parse JSON subtitle format to plain text."""
        try:
            data = json.loads(json_content)

            # Handle different JSON subtitle formats
            if isinstance(data, dict) and "events" in data:
                # srv3 format
                events = data.get("events", [])
                transcript_parts = []

                for event in events:
                    if "segs" in event:
                        for seg in event["segs"]:
                            text = seg.get("utf8", "")
                            if text and text.strip():
                                transcript_parts.append(text.strip())

                return " ".join(transcript_parts)

            elif isinstance(data, list):
                # Simple JSON array format
                return " ".join(item.get("text", "") for item in data if "text" in item)

            return json_content

        except json.JSONDecodeError:
            logger.error("Failed to parse JSON subtitle")
            return json_content

    def _parse_subtitle_data(self, data: str) -> str:
        """Parse subtitle data that's already been fetched."""
        # Try to detect format
        if data.startswith("WEBVTT"):
            return self._parse_vtt(data)
        elif data.startswith("{") or data.startswith("["):
            return self._parse_json_subtitle(data)
        else:
            # Return as-is if format unknown
            return data

    async def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """Prepare the extracted data for LLM processing."""
        metadata = extracted_data.get("metadata", {})
        title = extracted_data.get("title", "Untitled")
        channel = metadata.get("channel", "Unknown")
        description = metadata.get("description", "")
        transcript = metadata.get("transcript", "")

        # Format duration
        duration = metadata.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        # Build content for LLM
        parts = [
            f"YouTube Video: {title}",
            f"Channel: {channel}",
            f"Duration: {duration_str}",
            f"Views: {metadata.get('view_count', 0):,}",
            "",
        ]

        if description:
            parts.extend(["Description:", description, ""])

        if transcript:
            parts.extend(["Transcript:", transcript])
        else:
            parts.append(
                "Note: No transcript available. Summary based on title and description only."
            )

        content_text = "\n".join(parts)

        return {
            "content_to_filter": content_text,
            "content_to_summarize": content_text,
            "is_pdf": False,
        }
