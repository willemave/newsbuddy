"""URL detection utilities for content type inference.

These utilities are used by both content_submission.py and
sequential_task_processor.py to determine content types from URLs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import ParseResult, parse_qs, urlparse

from app.services.twitter_share import is_tweet_url

if TYPE_CHECKING:
    from app.models.contracts import ContentType

PODCAST_HOST_PLATFORMS: dict[str, str] = {
    "open.spotify.com": "spotify",
    "spotify.link": "spotify",
    "spoti.fi": "spotify",
    "on.spotify.com": "spotify",
    "open.spotify.link": "spotify",
    "podcasters.spotify.com": "spotify",
    "podcasts.apple.com": "apple_podcasts",
    "music.apple.com": "apple_music",
    "overcast.fm": "overcast",
    "pca.st": "pocket_casts",
    "pocketcasts.com": "pocket_casts",
    "rss.com": "rss",
    "podcastaddict.com": "podcast_addict",
    "castbox.fm": "castbox",
}

PODCAST_PATH_KEYWORDS = ("podcast", "episode", "episodes", "show")

YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be"}
APPLE_PODCAST_HOSTS = {"podcasts.apple.com", "music.apple.com"}
PODCAST_SHARE_ARTICLE_HOSTS = set(PODCAST_HOST_PLATFORMS) - APPLE_PODCAST_HOSTS

# Platforms where we skip LLM analysis and use pattern-based detection
# These are well-known platforms with predictable URL structures
PLATFORMS_SKIP_LLM_ANALYSIS = set(PODCAST_HOST_PLATFORMS) | YOUTUBE_HOSTS

HandlerContentType = Literal["article", "podcast"]


@dataclass(frozen=True)
class UrlHandler:
    """Function-based URL handler definition."""

    name: str
    matcher: Callable[[ParseResult], bool]
    platform_resolver: Callable[[ParseResult], str | None]
    content_type: HandlerContentType
    skip_llm_analysis: bool


@dataclass(frozen=True)
class UrlHandlerMatch:
    """Resolved URL handler decision."""

    name: str
    content_type: HandlerContentType
    platform: str | None
    skip_llm_analysis: bool


def _normalize_platform(platform: str | None) -> str | None:
    """Lowercase and trim platform strings."""
    if not platform:
        return None
    return platform.strip().lower() or None


def _normalize_hostname(hostname: str | None) -> str:
    """Lowercase and normalize hostnames, removing www."""
    if not hostname:
        return ""
    hostname = hostname.strip().lower()
    if hostname.startswith("www."):
        return hostname[4:]
    return hostname


def _resolve_platform_from_host(parsed: ParseResult) -> str | None:
    hostname = _normalize_hostname(parsed.hostname)
    return PODCAST_HOST_PLATFORMS.get(hostname)


def _resolve_youtube_platform(_parsed: ParseResult) -> str:
    return "youtube"


def _hostname_in(hosts: set[str], parsed: ParseResult) -> bool:
    hostname = _normalize_hostname(parsed.hostname)
    return hostname in hosts


def _is_youtube_host(parsed: ParseResult) -> bool:
    return _hostname_in(YOUTUBE_HOSTS, parsed)


def _is_youtube_single_video(parsed: ParseResult) -> bool:
    """Return True for single YouTube video URLs, including shorts/live."""
    hostname = _normalize_hostname(parsed.hostname)
    stripped_path = parsed.path.strip("/")
    lowered_path = parsed.path.lower()

    if hostname == "youtu.be":
        return bool(stripped_path.split("/", 1)[0])

    if hostname not in {"youtube.com", "m.youtube.com"}:
        return False

    if lowered_path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
        return bool(video_id and str(video_id).strip())

    for prefix in ("/shorts/", "/live/", "/embed/", "/v/"):
        if lowered_path.startswith(prefix):
            suffix = lowered_path[len(prefix) :].strip("/")
            return bool(suffix)
    return False


URL_HANDLERS: tuple[UrlHandler, ...] = (
    UrlHandler(
        name="youtube_single_video",
        matcher=_is_youtube_single_video,
        platform_resolver=_resolve_youtube_platform,
        content_type="podcast",
        skip_llm_analysis=True,
    ),
    UrlHandler(
        name="youtube_share",
        matcher=_is_youtube_host,
        platform_resolver=_resolve_youtube_platform,
        content_type="article",
        skip_llm_analysis=True,
    ),
    UrlHandler(
        name="apple_podcast_share",
        matcher=lambda parsed: _hostname_in(APPLE_PODCAST_HOSTS, parsed),
        platform_resolver=_resolve_platform_from_host,
        content_type="podcast",
        skip_llm_analysis=True,
    ),
    UrlHandler(
        name="podcast_platform_share",
        matcher=lambda parsed: _hostname_in(PODCAST_SHARE_ARTICLE_HOSTS, parsed),
        platform_resolver=_resolve_platform_from_host,
        content_type="article",
        skip_llm_analysis=True,
    ),
)


def _match_url_handler(parsed: ParseResult) -> UrlHandlerMatch | None:
    """Resolve the first matching URL handler for a parsed URL."""
    for handler in URL_HANDLERS:
        if not handler.matcher(parsed):
            continue
        return UrlHandlerMatch(
            name=handler.name,
            content_type=handler.content_type,
            platform=handler.platform_resolver(parsed),
            skip_llm_analysis=handler.skip_llm_analysis,
        )
    return None


def get_url_handler_name(url: str) -> str | None:
    """Return the matching handler name for a URL, if any."""
    parsed = urlparse(url)
    match = _match_url_handler(parsed)
    return match.name if match else None


def infer_content_type_and_platform(
    url: str, provided_type: ContentType | None, platform_hint: str | None
) -> tuple[ContentType, str | None]:
    """Infer content type and platform based on host/path or provided hints.

    Args:
        url: Normalized URL to inspect.
        provided_type: Optional explicit content type from the client.
        platform_hint: Optional platform hint from the client.

    Returns:
        Tuple of inferred content type and normalized platform (if any).
    """
    # Import here to avoid circular imports
    from app.models.contracts import ContentType

    normalized_hint = _normalize_platform(platform_hint)

    if provided_type:
        return provided_type, normalized_hint

    if is_tweet_url(url):
        return ContentType.ARTICLE, "twitter"

    parsed = urlparse(url)
    match = _match_url_handler(parsed)
    if match:
        if match.content_type == "podcast":
            return ContentType.PODCAST, match.platform or normalized_hint
        return ContentType.ARTICLE, match.platform or normalized_hint

    if any(keyword in parsed.path.lower() for keyword in PODCAST_PATH_KEYWORDS):
        return ContentType.PODCAST, normalized_hint

    return ContentType.ARTICLE, normalized_hint


def should_use_llm_analysis(url: str) -> bool:
    """Determine if URL should use LLM analysis or pattern-based detection.

    For well-known platforms (Spotify, YouTube, Apple Podcasts, etc.),
    we skip the LLM call and use pattern-based detection for speed.

    Args:
        url: Normalized URL to check.

    Returns:
        True if LLM analysis should be used, False for pattern-based detection.
    """
    if is_tweet_url(url):
        return False

    parsed = urlparse(url)
    match = _match_url_handler(parsed)
    if match:
        return not match.skip_llm_analysis
    return True
