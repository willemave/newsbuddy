"""Helpers for resolving Apple Podcasts episode metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import parse_qs, urlencode, urlparse

import feedparser

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.http import HttpService

logger = get_logger(__name__)

APPLE_PODCAST_ID_REGEX = re.compile(r"/id(?P<podcast_id>\d+)")
APPLE_PODCAST_HOSTS = ("podcasts.apple.com", "itunes.apple.com")
DEFAULT_EPISODE_LIMIT = 200
STOPWORDS = {"the", "and", "of", "a", "an", "to", "in", "on", "for", "with"}

HTTP_SERVICE = HttpService()


@dataclass(frozen=True)
class ApplePodcastResolution:
    """Resolved Apple Podcasts metadata for a single episode."""

    feed_url: str | None
    episode_title: str | None
    audio_url: str | None


def resolve_apple_podcast_episode(url: str) -> ApplePodcastResolution:
    """Resolve Apple Podcasts episode metadata from a share URL.

    Args:
        url: Apple Podcasts share URL (show or episode).

    Returns:
        ApplePodcastResolution with feed_url, episode_title, and audio_url when available.
    """
    show_id = extract_apple_podcast_id(url)
    if not show_id:
        return ApplePodcastResolution(feed_url=None, episode_title=None, audio_url=None)

    episode_id = _extract_episode_id(url)
    feed_url, episode_title = _lookup_feed_and_episode(show_id, episode_id)
    if not feed_url or not episode_title:
        return ApplePodcastResolution(
            feed_url=feed_url,
            episode_title=episode_title,
            audio_url=None,
        )

    audio_url = _resolve_episode_audio_url(feed_url, episode_title)
    return ApplePodcastResolution(
        feed_url=feed_url,
        episode_title=episode_title,
        audio_url=audio_url,
    )


def resolve_apple_podcast_feed_url(url: str) -> str | None:
    """Resolve the publisher RSS feed URL for an Apple Podcasts show URL."""
    show_id = extract_apple_podcast_id(url)
    if not show_id:
        return None
    country = (get_settings().discovery_itunes_country or "").lower()
    try:
        return _lookup_podcast_feed_url(show_id, country)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Apple Podcasts show lookup failed: %s",
            exc,
            extra={
                "component": "apple_podcasts",
                "operation": "itunes_show_lookup",
                "context_data": {"show_id": show_id},
            },
        )
        return None


def extract_apple_podcast_id(url: str) -> str | None:
    """Extract the podcast show id from an Apple Podcasts URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not any(host.endswith(domain) for domain in APPLE_PODCAST_HOSTS):
        return None

    match = APPLE_PODCAST_ID_REGEX.search(parsed.path)
    if match:
        return match.group("podcast_id")

    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and query_id.isdigit():
        return query_id

    return None


def _extract_episode_id(url: str) -> str | None:
    parsed = urlparse(url)
    episode_id = parse_qs(parsed.query).get("i", [None])[0]
    if episode_id and str(episode_id).isdigit():
        return str(episode_id)
    return None


def _lookup_feed_and_episode(show_id: str, episode_id: str | None) -> tuple[str | None, str | None]:
    settings = get_settings()
    params = {
        "id": show_id,
        "entity": "podcastEpisode",
        "limit": DEFAULT_EPISODE_LIMIT,
    }
    if settings.discovery_itunes_country:
        params["country"] = settings.discovery_itunes_country.lower()
    lookup_url = f"https://itunes.apple.com/lookup?{urlencode(params)}"

    try:
        response = HTTP_SERVICE.fetch(lookup_url, headers={"Accept": "application/json"})
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Apple Podcasts lookup failed: %s",
            exc,
            extra={
                "component": "apple_podcasts",
                "operation": "itunes_lookup",
                "context_data": {"show_id": show_id, "episode_id": episode_id},
            },
        )
        return None, None

    feed_url = None
    episode_title = None
    for item in payload.get("results", []):
        if not feed_url and item.get("kind") == "podcast":
            feed_url = item.get("feedUrl")
        if episode_id and item.get("kind") == "podcast-episode":
            track_id = item.get("trackId")
            if track_id is not None and str(track_id) == str(episode_id):
                episode_title = item.get("trackName")

    return feed_url, episode_title


@lru_cache(maxsize=256)
def _lookup_podcast_feed_url(show_id: str, country: str) -> str | None:
    params = {
        "id": show_id,
        "entity": "podcast",
    }
    if country:
        params["country"] = country
    lookup_url = f"https://itunes.apple.com/lookup?{urlencode(params)}"

    response = HTTP_SERVICE.fetch(lookup_url, headers={"Accept": "application/json"})
    payload = response.json()
    for item in payload.get("results", []):
        feed_url = item.get("feedUrl")
        if isinstance(feed_url, str) and feed_url.strip():
            return feed_url
    return None


def _resolve_episode_audio_url(feed_url: str, episode_title: str) -> str | None:
    try:
        response = HTTP_SERVICE.fetch(feed_url, headers={"Accept": "application/rss+xml"})
        feed = feedparser.parse(response.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Apple Podcasts RSS fetch failed: %s",
            exc,
            extra={
                "component": "apple_podcasts",
                "operation": "rss_fetch",
                "context_data": {"feed_url": feed_url},
            },
        )
        return None

    target_tokens = _tokenize_title(episode_title)
    best_entry = None
    best_score = 0

    for entry in feed.entries:
        entry_title = entry.get("title") or ""
        if not entry_title:
            continue
        if _normalize_title(entry_title) == _normalize_title(episode_title):
            return _extract_audio_url(entry)

        entry_tokens = _tokenize_title(entry_title)
        score = len(set(entry_tokens) & set(target_tokens))
        if score > best_score:
            best_score = score
            best_entry = entry

    if not best_entry:
        return None

    min_score = max(3, len(target_tokens) // 2) if target_tokens else 0
    if best_score < min_score:
        return None

    return _extract_audio_url(best_entry)


def _normalize_title(title: str) -> str:
    return " ".join(_tokenize_title(title))


def _tokenize_title(title: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    return [token for token in tokens if token not in STOPWORDS]


def _extract_audio_url(entry) -> str | None:  # noqa: ANN001
    enclosures = entry.get("enclosures")
    if not enclosures:
        enclosures = dict(entry).get("enclosures")

    if enclosures:
        for enclosure in enclosures:
            enclosure_type = getattr(enclosure, "type", None) or enclosure.get("type", "")
            enclosure_href = getattr(enclosure, "href", None) or enclosure.get("href", "")
            if not enclosure_href:
                continue
            if enclosure_type and "audio" in enclosure_type:
                return enclosure_href
            if any(
                enclosure_href.lower().endswith(ext) for ext in (".mp3", ".m4a", ".wav", ".ogg")
            ):
                return enclosure_href

    for link_item in getattr(entry, "links", []):
        link_href = link_item.get("href", "")
        link_type = link_item.get("type", "")
        if link_type and "audio" in link_type:
            return link_href
        if link_href and any(ext in link_href.lower() for ext in [".mp3", ".m4a", ".wav", ".ogg"]):
            return link_href

    return None
