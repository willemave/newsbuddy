"""Normalize feed-discovery candidates before live feed validation."""

from __future__ import annotations

import html
import re
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from app.core.logging import get_logger
from app.models.internal.scraper_configs import canonicalize_feed_url
from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services.apple_podcasts import extract_apple_podcast_id, resolve_apple_podcast_feed_url
from app.services.content_submission import normalize_url
from app.utils.url_utils import is_domain_or_subdomain

logger = get_logger(__name__)

FEED_TYPES = {"atom", "substack"}
PODCAST_TYPES = {"podcast_rss"}
YOUTUBE_TYPE = "youtube"
DISCOVERY_SKIP_HOSTS = {
    "link.chtbl.com",
    "podcasts.apple.com",
    "itunes.apple.com",
    "overcast.fm",
    "pca.st",
    "open.spotify.com",
    "creators.spotify.com",
    "podcasts.google.com",
    "music.youtube.com",
}
MARKDOWN_URL_REGEX = re.compile(r"\((https?://[^)]+)\)")


def _normalize_candidate(candidate: DiscoveryCandidate) -> DiscoveryCandidate | None:
    site_url = _normalize_candidate_url(candidate.site_url)
    feed_url = _normalize_candidate_url(candidate.feed_url)
    item_url = _normalize_candidate_url(candidate.item_url)

    if not site_url and feed_url:
        site_url = feed_url
    if not site_url:
        return None

    candidate.site_url = site_url
    candidate.feed_url = feed_url
    candidate.item_url = item_url

    candidate = _normalize_apple_podcast_candidate(candidate)
    if _should_skip_candidate(candidate):
        logger.debug(
            "Skipping candidate due to skipped host",
            extra={
                "component": "feed_discovery",
                "operation": "candidate_skip",
                "context_data": {
                    "site_url": candidate.site_url,
                    "feed_url": candidate.feed_url,
                },
            },
        )
        return None

    if _is_youtube_candidate(candidate):
        return _normalize_youtube_candidate(candidate)
    return candidate


def _sanitize_candidate_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None

    cleaned = html.unescape(raw_url.strip())
    match = MARKDOWN_URL_REGEX.search(cleaned)
    if match:
        cleaned = match.group(1)

    cleaned = cleaned.strip("<> \t\r\n")
    cleaned = cleaned.rstrip(").,]>\"'\\")
    if not cleaned or not cleaned.startswith(("http://", "https://")):
        return None
    return cleaned


def _normalize_candidate_url(raw_url: str | None) -> str | None:
    cleaned = _sanitize_candidate_url(raw_url)
    if not cleaned:
        return None
    try:
        return canonicalize_feed_url(normalize_url(cleaned))
    except Exception:  # noqa: BLE001
        return None


def _candidate_domain(candidate: DiscoveryCandidate) -> str | None:
    url = candidate.feed_url or candidate.site_url
    if not url:
        return None
    return (urlparse(url).hostname or "").lower() or None


def _should_skip_candidate(candidate: DiscoveryCandidate) -> bool:
    feed_host = (urlparse(candidate.feed_url).hostname or "").lower() if candidate.feed_url else ""
    if feed_host:
        return feed_host in DISCOVERY_SKIP_HOSTS
    return (urlparse(candidate.site_url).hostname or "").lower() in DISCOVERY_SKIP_HOSTS


def _is_youtube_host(hostname: str | None) -> bool:
    return is_domain_or_subdomain(hostname, "youtube.com") or is_domain_or_subdomain(
        hostname, "youtu.be"
    )


def _is_youtube_url(url: str | None) -> bool:
    return bool(url and _is_youtube_host(urlparse(url).hostname))


def _is_youtube_candidate(candidate: DiscoveryCandidate) -> bool:
    return _is_youtube_url(candidate.site_url) or _is_youtube_url(candidate.feed_url)


def _normalize_youtube_candidate(candidate: DiscoveryCandidate) -> DiscoveryCandidate | None:
    url = candidate.feed_url or candidate.site_url
    if not url:
        return None

    channel_id, playlist_id, canonical = _parse_youtube_identifiers(url)
    candidate.suggestion_type = cast(Any, YOUTUBE_TYPE)
    if _looks_like_watch_url(url) and not candidate.item_url:
        candidate.item_url = canonical
    if candidate.site_url and _looks_like_watch_url(candidate.site_url) and not candidate.item_url:
        candidate.item_url = normalize_url(candidate.site_url)
    candidate.channel_id = channel_id
    candidate.playlist_id = playlist_id
    candidate.feed_url = canonical
    if channel_id or playlist_id:
        candidate.config = {
            "feed_url": canonical,
            **({"channel_id": channel_id} if channel_id else {}),
            **({"playlist_id": playlist_id} if playlist_id else {}),
        }
    else:
        candidate.config = None
    return candidate


def _normalize_apple_podcast_candidate(candidate: DiscoveryCandidate) -> DiscoveryCandidate:
    url = candidate.feed_url or candidate.site_url
    if not url:
        return candidate

    podcast_id = extract_apple_podcast_id(url)
    if not podcast_id:
        return candidate

    feed_url = resolve_apple_podcast_feed_url(url)
    if not feed_url:
        return candidate

    try:
        candidate.feed_url = canonicalize_feed_url(normalize_url(feed_url))
    except Exception:  # noqa: BLE001
        return candidate

    try:
        candidate.site_url = candidate.site_url or normalize_url(url)
    except Exception:  # noqa: BLE001
        candidate.site_url = candidate.site_url or url

    candidate.suggestion_type = "podcast_rss"
    candidate.config = {
        **(candidate.config or {}),
        "source": "apple_podcasts",
        "podcast_id": podcast_id,
    }
    logger.debug(
        "Resolved Apple Podcasts feed URL",
        extra={
            "component": "feed_discovery",
            "operation": "apple_podcast_lookup",
            "context_data": {"podcast_id": podcast_id, "feed_url": candidate.feed_url},
        },
    )
    return candidate


def _parse_youtube_identifiers(url: str) -> tuple[str | None, str | None, str]:
    parsed = urlparse(url)
    host = parsed.hostname
    if is_domain_or_subdomain(host, "youtu.be"):
        video_id = parsed.path.strip("/")
        canonical = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
        return None, None, canonical

    if not is_domain_or_subdomain(host, "youtube.com"):
        return None, None, url

    path = parsed.path.strip("/")
    if path.startswith("playlist"):
        playlist_id = parse_qs(parsed.query).get("list", [None])[0]
        canonical = f"https://www.youtube.com/playlist?list={playlist_id}" if playlist_id else url
        return None, playlist_id, canonical

    if path.startswith("channel/"):
        channel_id = path.split("/", 1)[1]
        return channel_id, None, f"https://www.youtube.com/channel/{channel_id}"

    if path.startswith("@") or path.startswith("c/") or path.startswith("user/"):
        return None, None, f"https://www.youtube.com/{path}"

    return None, None, url


def _looks_like_watch_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        is_domain_or_subdomain(parsed.hostname, "youtube.com") and parsed.path.startswith("/watch")
    ) or is_domain_or_subdomain(parsed.hostname, "youtu.be")
