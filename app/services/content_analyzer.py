"""Content analysis service using page fetching and LLM analysis.

Fetches actual page content using trafilatura, then uses an LLM to analyze
the HTML for embedded podcast/video links and determine content type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypedDict

import feedparser
import httpx
import trafilatura
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.model_defaults import SMART_MODEL_SPEC, SMART_OPENAI_MODEL_NAME
from app.core.settings import get_settings
from app.models.llm.content_analysis import (
    ContentAnalysisOutput,
    ContentAnalysisResult,
    InstructionLink,
    InstructionResult,
)
from app.services.feed_detection import extract_feed_links
from app.services.langfuse_tracing import langfuse_trace_context
from app.services.llm_models import build_pydantic_model
from app.services.prompt_library import load_prompt, render_prompt
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

__all__ = [
    "AnalysisError",
    "CONTENT_ANALYSIS_MODEL",
    "CONTENT_ANALYZER_SYSTEM_PROMPT",
    "ContentAnalysisOutput",
    "ContentAnalysisResult",
    "InstructionLink",
    "InstructionResult",
    "get_content_analyzer",
]

# Configuration - use Responses API with web search
CONTENT_ANALYSIS_MODEL = SMART_OPENAI_MODEL_NAME
CONTENT_ANALYSIS_MODEL_SPEC = SMART_MODEL_SPEC
MAX_ANALYSIS_TEXT_CHARS = 8000

# Patterns to detect podcast/video platform links in HTML
PODCAST_VIDEO_PATTERNS = [
    (r"open\.spotify\.com/episode/([a-zA-Z0-9]+)", "spotify"),
    (r"podcasts\.apple\.com/.+/podcast/.+/id(\d+)", "apple_podcasts"),
    (r"music\.apple\.com/.+/album/.+/(\d+)", "apple_music"),
    (r"youtube\.com/watch\?v=([a-zA-Z0-9_-]+)", "youtube"),
    (r"youtu\.be/([a-zA-Z0-9_-]+)", "youtube"),
    (r"overcast\.fm/\+([a-zA-Z0-9]+)", "overcast"),
    (r"player\.vimeo\.com/video/(\d+)", "vimeo"),
]

# Audio file patterns
AUDIO_FILE_PATTERNS = [
    r'(https?://[^\s"\'<>]+\.mp3(?:\?[^\s"\'<>]*)?)',
    r'(https?://[^\s"\'<>]+\.m4a(?:\?[^\s"\'<>]*)?)',
    r'(https?://[^\s"\'<>]+\.wav(?:\?[^\s"\'<>]*)?)',
    r'(https?://[^\s"\'<>]+\.ogg(?:\?[^\s"\'<>]*)?)',
]


@dataclass
class AnalysisError:
    """Error from content analysis."""

    message: str
    recoverable: bool = True


class DetectedMedia(TypedDict):
    """Structured result of HTML media scanning."""

    platforms: list[str]
    platform_urls: list[str]
    audio_urls: list[str]
    rss_feeds: list[str]
    rss_audio_url: str | None


CONTENT_ANALYZER_SYSTEM_PROMPT = load_prompt("content/analyzer#system")


def _fetch_page_content(url: str) -> tuple[str | None, str | None]:
    """Fetch page HTML and extract text content.

    Returns:
        Tuple of (raw_html, extracted_text). Either may be None on failure.
    """
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            response.raise_for_status()
            html = response.text
            # Extract readable text using trafilatura
            text = trafilatura.extract(html, include_links=True) or ""
            return html, text
    except Exception as e:
        logger.warning(f"Failed to fetch page content: {e}")
        return None, None


def _empty_detected_media() -> DetectedMedia:
    """Return an empty media-detection payload."""
    return {
        "platforms": [],
        "platform_urls": [],
        "audio_urls": [],
        "rss_feeds": [],
        "rss_audio_url": None,
    }


def _detect_media_in_html(html: str, page_url: str) -> DetectedMedia:
    """Scan HTML for podcast/video platform links, audio files, and RSS feeds.

    Args:
        html: Raw HTML content of the page.
        page_url: URL of the page (needed to resolve relative feed URLs).

    Returns:
        Dict with detected platforms, media URLs, and RSS feeds.
    """
    detected: DetectedMedia = _empty_detected_media()

    # Check for podcast/video platform links
    for pattern, platform in PODCAST_VIDEO_PATTERNS:
        # Extract full URLs containing the pattern
        url_pattern = rf'(https?://[^\s"\'<>]*?{pattern.split("(")[0]}[^\s"\'<>]*)'
        urls = re.findall(url_pattern, html, re.IGNORECASE)
        if urls:
            detected["platforms"].append(platform)
            detected["platform_urls"].extend(urls[:3])  # Limit to 3

    # Check for direct audio files
    for pattern in AUDIO_FILE_PATTERNS:
        matches = re.findall(pattern, html, re.IGNORECASE)
        detected["audio_urls"].extend(matches[:3])

    # Deduplicate
    detected["platforms"] = list(set(detected["platforms"]))
    detected["platform_urls"] = list(set(detected["platform_urls"]))
    detected["audio_urls"] = list(set(detected["audio_urls"]))

    # Detect RSS feeds and try to extract audio URL
    try:
        feeds = extract_feed_links(html, page_url)
        detected["rss_feeds"] = [f["feed_url"] for f in feeds]

        # If Spotify/Apple detected but no direct audio, try RSS for audio URL
        has_platform_embed = any(
            p in detected["platforms"] for p in ("spotify", "apple_podcasts", "overcast")
        )
        if has_platform_embed and not detected["audio_urls"]:
            for feed_url in detected["rss_feeds"][:2]:  # Try first 2 feeds
                audio_url = _extract_audio_from_rss(feed_url)
                if audio_url:
                    detected["rss_audio_url"] = audio_url
                    logger.info(f"Extracted audio URL from RSS feed: {audio_url[:80]}...")
                    break
    except Exception as e:
        logger.debug(f"RSS feed detection failed: {e}")

    return detected


def _extract_audio_from_rss(feed_url: str) -> str | None:
    """Parse RSS feed and extract first audio enclosure URL.

    Args:
        feed_url: URL of the RSS feed to parse.

    Returns:
        First audio URL found, or None if no audio enclosures.
    """
    try:
        feed = feedparser.parse(feed_url)

        # Skip if parsing failed completely
        if feed.bozo and not feed.entries:
            logger.debug(f"Failed to parse RSS feed: {feed_url}")
            return None

        # Check first 5 entries for audio enclosures
        for entry in feed.entries[:5]:
            # Check enclosures for audio
            for enc in getattr(entry, "enclosures", []):
                enc_type = enc.get("type", "")
                if "audio" in enc_type:
                    return enc.get("href")

            # Check links for audio
            for link in getattr(entry, "links", []):
                link_type = link.get("type", "")
                if "audio" in link_type:
                    return link.get("href")

        return None

    except Exception as e:
        logger.debug(f"Error parsing RSS feed {feed_url}: {e}")
        return None


class ContentAnalyzer:
    """Analyzes URLs to determine content type and extract media URLs.

    Fetches actual page content, scans for podcast/video links, then uses
    an LLM to analyze and classify the content.
    """

    def __init__(self) -> None:
        """Initialize the content analyzer."""
        self._agent: Agent[None, ContentAnalysisOutput] | None = None

    def _get_agent(self) -> Agent[None, ContentAnalysisOutput]:
        """Get or create the content-analysis agent."""
        if self._agent is None:
            settings = get_settings()
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY not configured in settings")
            model, model_settings = build_pydantic_model(CONTENT_ANALYSIS_MODEL_SPEC)
            self._agent = Agent(
                model,
                output_type=ContentAnalysisOutput,
                system_prompt=CONTENT_ANALYZER_SYSTEM_PROMPT,
                model_settings=model_settings,
                capabilities=[WebSearch()],
            )
        return self._agent

    def analyze_url(
        self,
        url: str,
        instruction: str | None = None,
        *,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> ContentAnalysisOutput | AnalysisError:
        """Analyze a URL to determine content type and extract media URL.

        Fetches the page, scans for media links, then uses LLM to analyze.

        Args:
            url: The URL to analyze.

        Returns:
            ContentAnalysisOutput on success, AnalysisError on failure.
        """
        try:
            logger.info(
                "Starting content analysis for URL",
                extra={
                    "component": "content_analyzer",
                    "operation": "analyze_url",
                    "context_data": {"url": url, "model": CONTENT_ANALYSIS_MODEL_SPEC},
                },
            )

            # Step 1: Fetch the actual page content (best-effort)
            html, text = _fetch_page_content(url)
            if not html:
                logger.warning(
                    "Content analyzer fetch failed; continuing with web search only",
                    extra={
                        "component": "content_analyzer",
                        "operation": "fetch_page_content",
                        "context_data": {"url": url},
                    },
                )
                html = ""
                text = ""

            # Step 2: Scan HTML for podcast/video links and RSS feeds
            detected: DetectedMedia = (
                _detect_media_in_html(html or "", url) if html else _empty_detected_media()
            )

            # Step 3: Use LLM to analyze content with detected media info
            agent = self._get_agent()

            # Truncate text for LLM context and calculate word count
            text_payload = text or ""
            text_snippet = text_payload[:MAX_ANALYSIS_TEXT_CHARS]
            word_count = len(text_payload.split()) if text_payload else 0

            # Build RSS audio info for prompt
            rss_audio_info = detected.get("rss_audio_url")
            rss_audio_line = (
                f"- RSS audio URL (direct mp3): {rss_audio_info}"
                if rss_audio_info
                else "- RSS audio URL: None"
            )

            instruction_text = instruction.strip() if instruction else "None"

            prompt = render_prompt(
                "content/analyzer#user",
                system_prompt=CONTENT_ANALYZER_SYSTEM_PROMPT,
                url=url,
                word_count=word_count,
                instruction_text=instruction_text,
                platforms=detected["platforms"] or "None",
                platform_urls=detected["platform_urls"][:3] or "None",
                audio_urls=detected["audio_urls"][:2] or "None",
                rss_audio_line=rss_audio_line,
                text_snippet=text_snippet,
            )

            try:
                with langfuse_trace_context(
                    trace_name="queue.content_analyzer.analyze_url",
                    metadata={
                        "source": "queue",
                        "url": url,
                        "model_spec": CONTENT_ANALYSIS_MODEL_SPEC,
                    },
                    tags=["queue", "content_analyzer"],
                ):
                    result = agent.run_sync(prompt)
                record_model_usage(
                    "analyze_url",
                    result,
                    model_spec=CONTENT_ANALYSIS_MODEL_SPEC,
                    persist=usage_persist,
                )
            except ModelHTTPError as exc:
                logger.error(
                    "Content analysis request failed: %s",
                    exc,
                    extra={
                        "component": "content_analyzer",
                        "operation": "analyze_url",
                        "context_data": {"url": url},
                    },
                )
                return AnalysisError(str(exc), recoverable=True)
            except UnexpectedModelBehavior as exc:
                logger.error(
                    "Content analysis output parse failed: %s",
                    exc,
                    extra={
                        "component": "content_analyzer",
                        "operation": "parse_output",
                        "context_data": {"url": url},
                    },
                )
                return AnalysisError("Invalid LLM output format", recoverable=True)
            parsed = result.output

            logger.info(
                "LLM analysis complete: type=%s, platform=%s",
                parsed.analysis.content_type,
                parsed.analysis.platform,
                extra={
                    "component": "content_analyzer",
                    "operation": "analyze_url",
                    "context_data": {
                        "url": url,
                        "content_type": parsed.analysis.content_type,
                        "platform": parsed.analysis.platform,
                    },
                },
            )
            return parsed

        except Exception as e:
            logger.exception(
                "Unexpected error during content analysis: %s",
                e,
                extra={
                    "component": "content_analyzer",
                    "operation": "analyze_url",
                    "context_data": {"url": url, "error": str(e)},
                },
            )
            return AnalysisError(str(e), recoverable=True)


# Global instance for singleton pattern
_content_analyzer: ContentAnalyzer | None = None


def get_content_analyzer() -> ContentAnalyzer:
    """Get the global content analyzer instance."""
    global _content_analyzer
    if _content_analyzer is None:
        _content_analyzer = ContentAnalyzer()
    return _content_analyzer
