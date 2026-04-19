"""Provider-aggregated podcast episode search service."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.content_submission import normalize_url
from app.services.exa_client import exa_search
from app.services.vendor_costs import record_vendor_usage_out_of_band

logger = get_logger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_EXA_RESULTS = 40
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
PROVIDER_ORDER = (
    "listen_notes",
    "spotify",
    "apple_itunes",
    "podcast_index",
    "exa",
)
RequestParamScalar = str | int | float | bool | None
RequestParams = Mapping[str, RequestParamScalar | Sequence[RequestParamScalar]]


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


@dataclass
class _ProviderState:
    failures: int = 0
    open_until: datetime | None = None


@dataclass
class _SpotifyToken:
    access_token: str
    expires_at_epoch: float


_SEARCH_CACHE: dict[str, tuple[float, list[PodcastEpisodeSearchHit]]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_PROVIDER_STATES: dict[str, _ProviderState] = {}
_PROVIDER_STATE_LOCK = threading.Lock()
_SPOTIFY_TOKEN: _SpotifyToken | None = None
_SPOTIFY_TOKEN_LOCK = threading.Lock()


def search_podcast_episodes(
    query: str, limit: int = DEFAULT_LIMIT
) -> list[PodcastEpisodeSearchHit]:
    """Search for podcast episodes by free-text query.

    Args:
        query: Search query entered by the user.
        limit: Maximum number of episode matches to return.

    Returns:
        Aggregated episode matches from configured providers.
    """
    cleaned_query = query.strip()
    if len(cleaned_query) < 2:
        return []

    requested_limit = max(1, min(limit, MAX_LIMIT))
    cached = _read_cached_results(cleaned_query, requested_limit)
    if cached is not None:
        return cached

    provider_limit = max(requested_limit * 2, requested_limit)
    provider_hits: list[PodcastEpisodeSearchHit] = []
    for provider_name in PROVIDER_ORDER:
        provider_hits.extend(_run_provider(provider_name, cleaned_query, provider_limit))

    ranked_hits = _rank_and_dedupe_hits(cleaned_query, provider_hits)[:requested_limit]
    _write_cached_results(cleaned_query, requested_limit, ranked_hits)
    return ranked_hits


def _read_cached_results(query: str, limit: int) -> list[PodcastEpisodeSearchHit] | None:
    settings = get_settings()
    ttl = settings.podcast_search_cache_ttl_seconds
    if ttl <= 0:
        return None

    cache_key = f"{query.lower()}::{limit}"
    now_epoch = time.time()
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if not cached:
            return None
        cached_at, cached_hits = cached
        if (now_epoch - cached_at) > ttl:
            _SEARCH_CACHE.pop(cache_key, None)
            return None
        return list(cached_hits)


def _write_cached_results(query: str, limit: int, hits: list[PodcastEpisodeSearchHit]) -> None:
    settings = get_settings()
    if settings.podcast_search_cache_ttl_seconds <= 0:
        return

    cache_key = f"{query.lower()}::{limit}"
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (time.time(), list(hits))


def _run_provider(provider_name: str, query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    if _is_provider_open(provider_name):
        logger.debug(
            "Skipping provider due to open circuit",
            extra={
                "component": "podcast_search",
                "operation": "provider_skip",
                "context_data": {"provider": provider_name},
            },
        )
        return []

    provider_map = {
        "listen_notes": _search_listen_notes,
        "spotify": _search_spotify,
        "apple_itunes": _search_apple_itunes,
        "podcast_index": _search_podcast_index,
        "exa": _search_exa,
    }
    provider_fn = provider_map.get(provider_name)
    if not provider_fn:
        return []

    try:
        hits = provider_fn(query, limit)
        _record_provider_success(provider_name)
        return hits
    except Exception as exc:  # noqa: BLE001
        _record_provider_failure(provider_name, exc)
        logger.warning(
            "Podcast provider failed: %s",
            exc,
            extra={
                "component": "podcast_search",
                "operation": "provider_search",
                "context_data": {"provider": provider_name, "query": query},
            },
        )
        return []


def _is_provider_open(provider_name: str) -> bool:
    with _PROVIDER_STATE_LOCK:
        state = _PROVIDER_STATES.get(provider_name)
        if not state or state.open_until is None:
            return False
        return state.open_until > datetime.now(UTC)


def _record_provider_success(provider_name: str) -> None:
    with _PROVIDER_STATE_LOCK:
        state = _PROVIDER_STATES.setdefault(provider_name, _ProviderState())
        state.failures = 0
        state.open_until = None


def _record_provider_failure(provider_name: str, error: Exception) -> None:
    settings = get_settings()
    threshold = settings.podcast_search_circuit_breaker_failures
    cooldown_seconds = settings.podcast_search_circuit_breaker_cooldown_seconds

    with _PROVIDER_STATE_LOCK:
        state = _PROVIDER_STATES.setdefault(provider_name, _ProviderState())
        state.failures += 1
        if state.failures >= threshold:
            state.open_until = datetime.now(UTC) + timedelta(seconds=cooldown_seconds)
            logger.warning(
                "Opening podcast provider circuit",
                extra={
                    "component": "podcast_search",
                    "operation": "provider_circuit_open",
                    "context_data": {
                        "provider": provider_name,
                        "failures": state.failures,
                        "cooldown_seconds": cooldown_seconds,
                        "error": str(error),
                    },
                },
            )


def _rank_and_dedupe_hits(
    query: str, hits: list[PodcastEpisodeSearchHit]
) -> list[PodcastEpisodeSearchHit]:
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


def _search_listen_notes(query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    settings = get_settings()
    if not settings.listen_notes_api_key:
        return []

    payload = _http_get_json(
        "https://listen-api.listennotes.com/api/v2/search",
        params={
            "q": query,
            "type": "episode",
            "sort_by_date": 1,
            "offset": 0,
            "page_size": min(limit, 10),
        },
        headers={"X-ListenAPI-Key": settings.listen_notes_api_key},
    )

    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    hits: list[PodcastEpisodeSearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        episode_url = _normalize_http_url(
            _string_or_none(item.get("link")) or _string_or_none(item.get("listennotes_url"))
        )
        if not episode_url:
            continue

        podcast_value = item.get("podcast")
        podcast = podcast_value if isinstance(podcast_value, dict) else {}
        podcast_title = _string_or_none(podcast.get("title_original"))
        source = _string_or_none(podcast.get("publisher")) or _source_from_url(episode_url)
        feed_url = _normalize_http_url(
            _string_or_none(item.get("rss")) or _string_or_none(podcast.get("rss"))
        )
        snippet = _clean_text(
            _string_or_none(item.get("description_original"))
            or _string_or_none(item.get("description_highlighted"))
        )
        hits.append(
            PodcastEpisodeSearchHit(
                title=_string_or_none(item.get("title_original")) or "Untitled Episode",
                episode_url=episode_url,
                podcast_title=podcast_title,
                source=source,
                snippet=snippet,
                feed_url=feed_url,
                published_at=_iso_from_millis(item.get("pub_date_ms")),
                provider="listen_notes",
                score=PROVIDER_WEIGHTS["listen_notes"],
            )
        )

    _record_podcast_usage(
        provider="listen_notes",
        model="episode_search",
        operation="podcast_search.listen_notes_search",
        request_count=1,
        resource_count=len(hits),
    )
    return hits


def _search_spotify(query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    token = _get_spotify_token()
    if not token:
        return []

    payload = _spotify_search(token=token, query=query, limit=min(limit, 20))
    if payload is None:
        return []
    episodes = payload.get("episodes")
    if not isinstance(episodes, dict):
        return []
    items = episodes.get("items", [])
    if not isinstance(items, list):
        return []

    hits: list[PodcastEpisodeSearchHit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        episode_url = _normalize_http_url(_nested_string(item, "external_urls", "spotify"))
        if not episode_url:
            continue
        show_value = item.get("show")
        show = show_value if isinstance(show_value, dict) else {}
        release_date = _string_or_none(item.get("release_date"))
        release_precision = _string_or_none(item.get("release_date_precision"))
        published_at = _spotify_release_to_iso(release_date, release_precision)

        hits.append(
            PodcastEpisodeSearchHit(
                title=_string_or_none(item.get("name")) or "Untitled Episode",
                episode_url=episode_url,
                podcast_title=_string_or_none(show.get("name")),
                source=_string_or_none(show.get("publisher")) or _source_from_url(episode_url),
                snippet=_clean_text(_string_or_none(item.get("description"))),
                feed_url=None,
                published_at=published_at,
                provider="spotify",
                score=PROVIDER_WEIGHTS["spotify"],
            )
        )

    return hits


def _spotify_search(token: str, query: str, limit: int) -> dict[str, object] | None:
    settings = get_settings()
    params: dict[str, RequestParamScalar] = {"q": query, "type": "episode", "limit": limit}
    if settings.spotify_market:
        params["market"] = settings.spotify_market

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    timeout = settings.podcast_search_provider_timeout_seconds
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get("https://api.spotify.com/v1/search", params=params, headers=headers)
        if response.status_code == 401:
            _clear_spotify_token()
            refreshed = _get_spotify_token()
            if not refreshed:
                return None
            headers["Authorization"] = f"Bearer {refreshed}"
            response = client.get(
                "https://api.spotify.com/v1/search",
                params=params,
                headers=headers,
            )

        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            episodes = payload.get("episodes")
            items = episodes.get("items", []) if isinstance(episodes, dict) else []
            resource_count = len(items) if isinstance(items, list) else 0
            _record_podcast_usage(
                provider="spotify",
                model="episode_search",
                operation="podcast_search.spotify_search",
                request_count=1,
                resource_count=resource_count,
            )
            return payload
        return None


def _get_spotify_token() -> str | None:
    global _SPOTIFY_TOKEN  # noqa: PLW0603

    settings = get_settings()
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    with _SPOTIFY_TOKEN_LOCK:
        if _SPOTIFY_TOKEN and (_SPOTIFY_TOKEN.expires_at_epoch - time.time()) > 30:
            return _SPOTIFY_TOKEN.access_token

        timeout = settings.podcast_search_provider_timeout_seconds
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return None

            access_token = _string_or_none(payload.get("access_token"))
            expires_in = int(payload.get("expires_in") or 3600)
            if not access_token:
                return None

            _SPOTIFY_TOKEN = _SpotifyToken(
                access_token=access_token,
                expires_at_epoch=time.time() + max(60, expires_in),
            )
            _record_podcast_usage(
                provider="spotify",
                model="oauth_token",
                operation="podcast_search.spotify_token",
                request_count=1,
                resource_count=1,
            )
            return access_token


def _clear_spotify_token() -> None:
    global _SPOTIFY_TOKEN  # noqa: PLW0603
    with _SPOTIFY_TOKEN_LOCK:
        _SPOTIFY_TOKEN = None


def _search_apple_itunes(query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    settings = get_settings()
    params: dict[str, RequestParamScalar] = {
        "term": query,
        "media": "podcast",
        "entity": "podcastEpisode",
        "limit": min(limit, 25),
    }
    if settings.discovery_itunes_country:
        params["country"] = settings.discovery_itunes_country

    payload = _http_get_json("https://itunes.apple.com/search", params=params)
    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    hits: list[PodcastEpisodeSearchHit] = []
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue

        episode_url = _normalize_http_url(_string_or_none(item.get("trackViewUrl")))
        if not episode_url:
            continue

        feed_url = _normalize_http_url(_string_or_none(item.get("feedUrl")))
        if not feed_url and index < 2:
            feed_url = _resolve_feed_url(episode_url)

        hits.append(
            PodcastEpisodeSearchHit(
                title=_string_or_none(item.get("trackName")) or "Untitled Episode",
                episode_url=episode_url,
                podcast_title=_string_or_none(item.get("collectionName")),
                source="apple_podcasts",
                snippet=_clean_text(_string_or_none(item.get("description"))),
                feed_url=feed_url,
                published_at=_normalize_published_date(_string_or_none(item.get("releaseDate"))),
                provider="apple_itunes",
                score=PROVIDER_WEIGHTS["apple_itunes"],
            )
        )

    return hits


def _search_podcast_index(query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    settings = get_settings()
    if not settings.podcast_index_api_key or not settings.podcast_index_api_secret:
        return []

    search_payload = _podcast_index_request(
        path="/search/byterm",
        params={"q": query, "max": min(10, max(4, limit // 2))},
    )
    feeds = search_payload.get("feeds", [])
    if not isinstance(feeds, list):
        return []
    _record_podcast_usage(
        provider="podcast_index",
        model="search_byterm",
        operation="podcast_search.podcast_index_search",
        request_count=1,
        resource_count=len(feeds),
    )

    hits: list[PodcastEpisodeSearchHit] = []
    for feed in feeds[:3]:
        if not isinstance(feed, dict):
            continue
        feed_id = feed.get("id")
        if feed_id is None:
            continue
        feed_title = _string_or_none(feed.get("title"))
        feed_url = _normalize_http_url(_string_or_none(feed.get("url")))

        episodes_payload = _podcast_index_request(
            path="/episodes/byfeedid",
            params={"id": str(feed_id), "max": min(3, limit), "fulltext": ""},
        )
        items = episodes_payload.get("items", [])
        if not isinstance(items, list):
            continue
        _record_podcast_usage(
            provider="podcast_index",
            model="episodes_byfeedid",
            operation="podcast_search.podcast_index_episodes",
            request_count=1,
            resource_count=len(items),
        )

        for item in items:
            if not isinstance(item, dict):
                continue
            episode_url = _normalize_http_url(
                _string_or_none(item.get("link")) or _string_or_none(item.get("enclosureUrl"))
            )
            if not episode_url:
                continue

            hits.append(
                PodcastEpisodeSearchHit(
                    title=_string_or_none(item.get("title")) or "Untitled Episode",
                    episode_url=episode_url,
                    podcast_title=feed_title,
                    source=_source_from_url(episode_url),
                    snippet=_clean_text(_string_or_none(item.get("description"))),
                    feed_url=feed_url,
                    published_at=_iso_from_epoch_seconds(item.get("datePublished")),
                    provider="podcast_index",
                    score=PROVIDER_WEIGHTS["podcast_index"],
                )
            )

    return hits


def _podcast_index_request(path: str, params: RequestParams) -> dict[str, object]:
    settings = get_settings()
    timestamp = str(int(time.time()))
    auth = hashlib.sha1(
        f"{settings.podcast_index_api_key}{settings.podcast_index_api_secret}{timestamp}".encode()
    ).hexdigest()
    headers = {
        "X-Auth-Key": settings.podcast_index_api_key or "",
        "X-Auth-Date": timestamp,
        "Authorization": auth,
        "User-Agent": settings.podcast_index_user_agent,
    }
    return _http_get_json(
        f"https://api.podcastindex.org/api/1.0{path}",
        params=params,
        headers=headers,
    )


def _search_exa(query: str, limit: int) -> list[PodcastEpisodeSearchHit]:
    raw_results = exa_search(
        query=f"{query} podcast episode",
        num_results=min(MAX_EXA_RESULTS, max(limit, limit * 2)),
    )

    hits: list[PodcastEpisodeSearchHit] = []
    for result in raw_results:
        episode_url = _normalize_http_url(result.url)
        if not episode_url:
            continue
        if not _looks_like_podcast_result(result.title, result.snippet, episode_url):
            continue

        hits.append(
            PodcastEpisodeSearchHit(
                title=result.title or "Untitled Episode",
                episode_url=episode_url,
                podcast_title=None,
                source=_source_from_url(episode_url),
                snippet=_clean_text(result.snippet),
                feed_url=_resolve_feed_url(episode_url),
                published_at=_normalize_published_date(result.published_date),
                provider="exa",
                score=PROVIDER_WEIGHTS["exa"],
            )
        )

    return hits


def _http_get_json(
    url: str, params: RequestParams | None = None, headers: dict[str, str] | None = None
) -> dict[str, object]:
    settings = get_settings()
    timeout = settings.podcast_search_provider_timeout_seconds
    request_headers = {"Accept": "application/json", "User-Agent": "newsly/1.0"}
    if headers:
        request_headers.update(headers)

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, params=params, headers=request_headers)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    return {}


def _record_podcast_usage(
    *,
    provider: str,
    model: str,
    operation: str,
    request_count: int,
    resource_count: int = 0,
) -> None:
    """Persist external podcast search provider usage when keyed APIs are called."""
    record_vendor_usage_out_of_band(
        provider=provider,
        model=model,
        feature="podcast_search",
        operation=operation,
        source="api",
        usage={
            "request_count": request_count,
            "resource_count": resource_count,
        },
    )


def _normalize_http_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    try:
        return normalize_url(raw_url)
    except Exception:  # noqa: BLE001
        return None


def _canonicalize_episode_url(raw_url: str) -> str | None:
    normalized = _normalize_http_url(raw_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_QUERY_KEYS
    ]
    canonical = parsed._replace(
        query=urlencode(filtered_query, doseq=True),
        fragment="",
    )
    return _normalize_http_url(urlunparse(canonical))


def _source_from_url(url: str) -> str | None:
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        return host[4:]
    return host


def _looks_like_podcast_result(title: str | None, snippet: str | None, url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if any(host.endswith(hint) or hint in host for hint in PODCAST_HOST_HINTS):
        return True

    combined = " ".join([title or "", snippet or "", url]).lower()
    return any(keyword in combined for keyword in PODCAST_KEYWORDS)


def _resolve_feed_url(episode_url: str) -> str | None:
    parsed = urlparse(episode_url)
    host = (parsed.netloc or "").lower()
    if "podcasts.apple.com" not in host and "itunes.apple.com" not in host:
        return None

    try:
        resolution = resolve_apple_podcast_episode(episode_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to resolve Apple podcast feed for search hit: %s",
            exc,
            extra={
                "component": "podcast_search",
                "operation": "resolve_feed_url",
                "context_data": {"episode_url": episode_url},
            },
        )
        return None
    return _normalize_http_url(resolution.feed_url)


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if token not in TOKEN_STOPWORDS and len(token) > 1]


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
