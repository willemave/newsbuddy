"""Resolve non-YouTube equivalents for YouTube submissions.

This service is intentionally narrow: it starts with lightweight metadata from
YouTube, searches for equivalent non-YouTube pages, and only returns a result
when the current analyzer can validate the candidate into a non-YouTube item
with a usable media URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.logging import get_logger
from app.services.content_analyzer import AnalysisError
from app.services.exa_client import exa_search
from app.services.gateways.llm_gateway import get_llm_gateway
from app.services.podcast_search import search_podcast_episodes

logger = get_logger(__name__)

YOUTUBE_EXCLUDE_DOMAINS = ["youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"]
KNOWN_AUDIO_HOST_HINTS = (
    "podcasts.apple.com",
    "spotify.com",
    "simplecast.com",
    "simplecastaudio.com",
    "megaphone.fm",
    "anchor.fm",
    "buzzsprout.com",
    "captivate.fm",
    "libsyn.com",
    "transistor.fm",
    "substack.com",
    "podscan.fm",
)
MIN_ACCEPTABLE_SIMILARITY = 0.30
MAX_CANDIDATES_TO_VALIDATE = 5


@dataclass(frozen=True)
class YouTubeOEmbedMetadata:
    title: str | None = None
    author_name: str | None = None
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class YouTubeEquivalentResolution:
    metadata: YouTubeOEmbedMetadata | None
    search_query: str | None
    resolved_url: str | None = None
    resolved_title: str | None = None
    content_type: str | None = None
    platform: str | None = None
    media_url: str | None = None
    media_format: str | None = None
    source: str | None = None
    similarity: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Candidate:
    url: str
    title: str | None
    snippet: str | None
    source: str
    podcast_title: str | None = None
    score: float = 0.0


def resolve_youtube_equivalent(
    youtube_url: str,
    *,
    fallback_title: str | None = None,
) -> YouTubeEquivalentResolution:
    """Resolve a non-YouTube equivalent URL for a YouTube submission."""
    metadata = _fetch_oembed_metadata(youtube_url)
    search_query = _build_search_query(metadata, fallback_title)
    if not search_query:
        return YouTubeEquivalentResolution(
            metadata=metadata,
            search_query=None,
            reason="missing_metadata",
        )

    candidates = _rank_candidates(
        search_query=search_query,
        metadata=metadata,
    )
    if not candidates:
        return YouTubeEquivalentResolution(
            metadata=metadata,
            search_query=search_query,
            reason="no_candidates",
        )

    gateway = get_llm_gateway()
    for candidate in candidates[:MAX_CANDIDATES_TO_VALIDATE]:
        result = gateway.analyze_url(candidate.url)
        if isinstance(result, AnalysisError):
            continue

        analysis = result.analysis
        platform = (analysis.platform or "").strip().lower() or None
        if platform == "youtube":
            continue
        if not analysis.media_url:
            continue

        similarity = max(
            candidate.score,
            _title_similarity(metadata.title or fallback_title, analysis.title),
        )
        if similarity < MIN_ACCEPTABLE_SIMILARITY:
            continue

        resolved_title = analysis.title or candidate.title or metadata.title or fallback_title
        return YouTubeEquivalentResolution(
            metadata=metadata,
            search_query=search_query,
            resolved_url=candidate.url,
            resolved_title=resolved_title,
            content_type=analysis.content_type,
            platform=platform,
            media_url=analysis.media_url,
            media_format=analysis.media_format,
            source=candidate.source,
            similarity=similarity,
            reason="resolved",
        )

    return YouTubeEquivalentResolution(
        metadata=metadata,
        search_query=search_query,
        reason="no_validated_match",
    )


def _fetch_oembed_metadata(youtube_url: str) -> YouTubeOEmbedMetadata:
    canonical_url = _normalize_youtube_watch_url(youtube_url)
    try:
        response = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_url, "format": "json"},
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return YouTubeOEmbedMetadata()
        return YouTubeOEmbedMetadata(
            title=_strip_or_none(payload.get("title")),
            author_name=_strip_or_none(payload.get("author_name")),
            thumbnail_url=_strip_or_none(payload.get("thumbnail_url")),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("YouTube oEmbed lookup failed for %s: %s", youtube_url, exc)
        return YouTubeOEmbedMetadata()


def _normalize_youtube_watch_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else url
    if "youtube.com" not in hostname:
        return url
    if parsed.path == "/watch":
        raw_video_id = parse_qs(parsed.query).get("v", [None])[0]
        watch_video_id = raw_video_id if isinstance(raw_video_id, str) else None
        return f"https://www.youtube.com/watch?v={watch_video_id}" if watch_video_id else url
    return url


def _build_search_query(
    metadata: YouTubeOEmbedMetadata,
    fallback_title: str | None,
) -> str | None:
    title = _strip_or_none(metadata.title)
    author_name = _strip_or_none(metadata.author_name)
    fallback = _strip_or_none(fallback_title)
    base_title = title or fallback
    if not base_title:
        return None
    parts = [base_title]
    if author_name:
        parts.append(author_name)
    return " ".join(parts)


def _rank_candidates(
    *,
    search_query: str,
    metadata: YouTubeOEmbedMetadata,
) -> list[_Candidate]:
    raw_candidates = _gather_candidates(search_query, metadata)
    youtube_title = metadata.title
    author_name = metadata.author_name
    deduped: dict[str, _Candidate] = {}

    for candidate in raw_candidates:
        similarity = _title_similarity(youtube_title, candidate.title)
        author_bonus = _author_bonus(author_name, candidate)
        host_bonus = _host_bonus(candidate.url)
        scored = _Candidate(
            url=candidate.url,
            title=candidate.title,
            snippet=candidate.snippet,
            source=candidate.source,
            podcast_title=candidate.podcast_title,
            score=similarity + author_bonus + host_bonus,
        )
        existing = deduped.get(candidate.url)
        if existing is None or scored.score > existing.score:
            deduped[candidate.url] = scored

    return sorted(deduped.values(), key=lambda item: item.score, reverse=True)


def _gather_candidates(
    search_query: str,
    metadata: YouTubeOEmbedMetadata,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []

    for podcast_hit in search_podcast_episodes(search_query, limit=5):
        candidates.append(
            _Candidate(
                url=podcast_hit.episode_url,
                title=podcast_hit.title,
                snippet=podcast_hit.snippet,
                source="podcast_search",
                podcast_title=podcast_hit.podcast_title,
            )
        )

    title = metadata.title or search_query
    author_name = metadata.author_name
    natural_query = (
        "Find the same podcast episode, interview, or canonical published version outside "
        f'YouTube for "{title}".'
    )
    if author_name:
        natural_query += f' Prioritize results related to "{author_name}".'
    natural_query += (
        " Prefer direct episode pages, Apple Podcasts, Spotify, Substack, or publisher pages."
    )

    for exa_hit in exa_search(
        natural_query,
        num_results=5,
        exclude_domains=YOUTUBE_EXCLUDE_DOMAINS,
    ):
        candidates.append(
            _Candidate(
                url=exa_hit.url,
                title=exa_hit.title,
                snippet=exa_hit.snippet,
                source="exa_search",
            )
        )

    return candidates


def _title_similarity(left: str | None, right: str | None) -> float:
    left_text = _normalize_text(left)
    right_text = _normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _author_bonus(author_name: str | None, candidate: _Candidate) -> float:
    if not author_name:
        return 0.0
    author_tokens = _tokenize(author_name)
    if not author_tokens:
        return 0.0
    haystack = " ".join(
        filter(
            None,
            [
                candidate.title,
                candidate.podcast_title,
                candidate.snippet,
            ],
        )
    )
    normalized_haystack = _normalize_text(haystack)
    if not normalized_haystack:
        return 0.0
    matched = sum(1 for token in author_tokens if token in normalized_haystack)
    return 0.15 * (matched / len(author_tokens))


def _host_bonus(url: str) -> float:
    hostname = (urlparse(url).hostname or "").lower()
    return 0.08 if any(hint in hostname for hint in KNOWN_AUDIO_HOST_HINTS) else 0.0


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.lower().split())


def _tokenize(value: str) -> list[str]:
    return [token for token in _normalize_text(value).split() if len(token) > 2]


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
