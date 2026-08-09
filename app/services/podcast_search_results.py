"""Podcast search result model, normalization, and ranking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.services.content_submission import normalize_url
from app.utils.url_utils import is_domain_or_subdomain

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "si",
    "fbclid",
    "gclid",
}
TOKEN_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "to",
    "of",
    "in",
    "on",
    "at",
    "with",
    "podcast",
    "episode",
}
PROVIDER_WEIGHTS = {
    "listen_notes": 0.95,
    "spotify": 0.9,
    "apple_itunes": 0.82,
    "podcast_index": 0.78,
    "exa": 0.6,
}
PODCAST_KEYWORDS = ("podcast", "episode", "listen", "audio", "interview")
PODCAST_HOST_HINTS = (
    "podcasts.apple.com",
    "spotify.com",
    "overcast.fm",
    "pca.st",
    "podbean.com",
    "buzzsprout.com",
    "captivate.fm",
    "transistor.fm",
    "simplecast.com",
    "megaphone.fm",
    "listennotes.com",
)


@dataclass(frozen=True)
class PodcastEpisodeSearchHit:
    """A podcast episode match from external search."""

    title: str
    episode_url: str
    podcast_title: str | None
    source: str | None
    snippet: str | None
    feed_url: str | None
    published_at: str | None
    provider: str
    score: float | None = None


def rank_and_dedupe_hits(
    query: str,
    hits: list[PodcastEpisodeSearchHit],
) -> list[PodcastEpisodeSearchHit]:
    """Normalize, deduplicate, and rank aggregated provider results."""
    query_tokens = _tokenize(query)
    deduped: dict[str, PodcastEpisodeSearchHit] = {}

    for hit in hits:
        canonical_url = _canonicalize_episode_url(hit.episode_url)
        if not canonical_url:
            continue
        computed_score = _compute_hit_score(hit, query_tokens)
        scored_hit = PodcastEpisodeSearchHit(
            title=hit.title,
            episode_url=hit.episode_url,
            podcast_title=hit.podcast_title,
            source=hit.source,
            snippet=hit.snippet,
            feed_url=hit.feed_url,
            published_at=hit.published_at,
            provider=hit.provider,
            score=computed_score,
        )

        existing = deduped.get(canonical_url)
        if not existing or (existing.score or 0.0) < (scored_hit.score or 0.0):
            deduped[canonical_url] = scored_hit

    return sorted(
        deduped.values(),
        key=lambda item: ((item.score or 0.0), _sort_epoch(item.published_at)),
        reverse=True,
    )


def _compute_hit_score(hit: PodcastEpisodeSearchHit, query_tokens: list[str]) -> float:
    base = hit.score or PROVIDER_WEIGHTS.get(hit.provider, 0.5)
    text = " ".join(
        [
            hit.title,
            hit.podcast_title or "",
            hit.snippet or "",
        ]
    ).lower()

    if query_tokens:
        matched = sum(1 for token in query_tokens if token in text)
        base += 0.25 * (matched / len(query_tokens))

    if hit.feed_url:
        base += 0.05

    if hit.published_at:
        published = _parse_iso_dt(hit.published_at)
        if published:
            age_days = max(0.0, (datetime.now(UTC) - published).total_seconds() / 86_400)
            if age_days <= 14:
                base += 0.07
            elif age_days <= 60:
                base += 0.04
            elif age_days <= 365:
                base += 0.02

    return min(base, 2.0)


def _sort_epoch(value: str | None) -> float:
    parsed = _parse_iso_dt(value) if value else None
    if not parsed:
        return 0.0
    return parsed.timestamp()


def _canonicalize_episode_url(raw_url: str) -> str | None:
    normalized = normalize_podcast_http_url(raw_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    canonical = parsed._replace(
        query=urlencode(filtered_query, doseq=True),
        fragment="",
    )
    return normalize_podcast_http_url(urlunparse(canonical))


def normalize_podcast_http_url(raw_url: str | None) -> str | None:
    """Return a validated podcast-provider HTTP URL or ``None``."""
    if not raw_url:
        return None
    try:
        return normalize_url(raw_url)
    except Exception:  # noqa: BLE001
        return None


def _source_from_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        return host[4:]
    return host


def _looks_like_podcast_result(title: str | None, snippet: str | None, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(is_domain_or_subdomain(host, hint) for hint in PODCAST_HOST_HINTS):
        return True

    combined = " ".join([title or "", snippet or "", url]).lower()
    return any(keyword in combined for keyword in PODCAST_KEYWORDS)


def _clean_text(text: str | None) -> str | None:
    if not text:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", text)
    compact = re.sub(r"\s+", " ", without_tags).strip()
    return compact or None


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _nested_string(payload: dict[str, object], *keys: str) -> str | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _string_or_none(current)


def _iso_from_millis(value: object) -> str | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _iso_from_epoch_seconds(value: object) -> str | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def _spotify_release_to_iso(date_str: str | None, precision: str | None) -> str | None:
    if not date_str:
        return None
    try:
        if precision == "year":
            dt = datetime.strptime(date_str, "%Y").replace(tzinfo=UTC)
        elif precision == "month":
            dt = datetime.strptime(date_str, "%Y-%m").replace(tzinfo=UTC)
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        return _normalize_published_date(date_str)


def _normalize_published_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    value = date_str.strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(value)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if token not in TOKEN_STOPWORDS and len(token) > 1]


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
