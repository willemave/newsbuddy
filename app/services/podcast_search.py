"""Provider-aggregated podcast episode search service."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.apple_podcasts import extract_apple_podcast_id
from app.services.exa_client import exa_search
from app.services.podcast_search_results import (
    PROVIDER_WEIGHTS,
    PodcastEpisodeSearchHit,
    _clean_text,
    _iso_from_epoch_seconds,
    _iso_from_millis,
    _looks_like_podcast_result,
    _nested_string,
    _normalize_published_date,
    _source_from_url,
    _spotify_release_to_iso,
    _string_or_none,
    normalize_podcast_http_url,
    rank_and_dedupe_hits,
)
from app.services.vendor_costs import record_vendor_usage_out_of_band
from app.utils.url_utils import is_domain_or_subdomain

logger = get_logger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_EXA_RESULTS = 40
PROVIDER_ORDER = (
    "listen_notes",
    "spotify",
    "apple_itunes",
    "podcast_index",
    "exa",
)
MAX_APPLE_FEED_LOOKUPS_PER_PROVIDER = 2
_PROVIDER_EXECUTOR_CAPACITY = len(PROVIDER_ORDER) * 2
_SEARCH_CACHE_MAX_ENTRIES = 256
RequestParamScalar = str | int | float | bool | None
RequestParams = Mapping[str, RequestParamScalar | Sequence[RequestParamScalar]]


@dataclass
class _ProviderState:
    failures: int = 0
    open_until: datetime | None = None


@dataclass
class _SpotifyToken:
    access_token: str
    expires_at_epoch: float


@dataclass(frozen=True)
class _PodcastSearchCacheEntry:
    expires_at: float
    hits: tuple[PodcastEpisodeSearchHit, ...]


class _PodcastSearchDeadlineExceeded(TimeoutError):
    """Raised when the shared search deadline, rather than a provider, expires."""


_SEARCH_CACHE: dict[str, _PodcastSearchCacheEntry] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_PROVIDER_STATES: dict[str, _ProviderState] = {}
_PROVIDER_STATE_LOCK = threading.Lock()
_SPOTIFY_TOKEN: _SpotifyToken | None = None
_SPOTIFY_TOKEN_LOCK = threading.Lock()
_PROVIDER_EXECUTOR = ThreadPoolExecutor(
    max_workers=_PROVIDER_EXECUTOR_CAPACITY,
    thread_name_prefix="podcast-search",
)
_PROVIDER_ADMISSION = threading.BoundedSemaphore(_PROVIDER_EXECUTOR_CAPACITY)


def search_podcast_episodes(
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
    """Search for podcast episodes by free-text query.

    Args:
        query: Search query entered by the user.
        limit: Maximum number of episode matches to return.

    Returns:
        Aggregated episode matches from configured providers.
    """
    cleaned_query = query.strip()
    if len(cleaned_query) < 2 or _deadline_expired(deadline):
        return []

    requested_limit = max(1, min(limit, MAX_LIMIT))
    cached = _read_cached_results(cleaned_query, requested_limit)
    if cached is not None:
        return cached

    effective_deadline = _effective_search_deadline(deadline)
    provider_limit = max(requested_limit * 2, requested_limit)
    provider_hits: list[PodcastEpisodeSearchHit] = []
    provider_futures: dict[str, Future[tuple[list[PodcastEpisodeSearchHit], bool]]] = {}
    for provider_name in PROVIDER_ORDER:
        future = _submit_provider(
            provider_name,
            cleaned_query,
            provider_limit,
            deadline=effective_deadline,
        )
        if future is not None:
            provider_futures[provider_name] = future

    completed_all_providers = len(provider_futures) == len(PROVIDER_ORDER)
    done, pending = wait(
        tuple(provider_futures.values()),
        timeout=max(0.0, effective_deadline - time.monotonic()),
    )
    for future in pending:
        future.cancel()

    for provider_name in PROVIDER_ORDER:
        future = provider_futures.get(provider_name)
        if future is None or future not in done:
            completed_all_providers = False
            continue
        try:
            hits, provider_completed = future.result()
        except Exception as exc:  # noqa: BLE001
            completed_all_providers = False
            logger.exception(
                "Podcast provider future failed unexpectedly: %s",
                exc,
                extra={
                    "component": "podcast_search",
                    "operation": "provider_future",
                    "context_data": {"provider": provider_name},
                },
            )
            continue
        provider_hits.extend(hits)
        completed_all_providers = completed_all_providers and provider_completed

    ranked_hits = rank_and_dedupe_hits(cleaned_query, provider_hits)[:requested_limit]
    if completed_all_providers or ranked_hits:
        _write_cached_results(
            cleaned_query,
            requested_limit,
            ranked_hits,
            degraded=not completed_all_providers,
        )
    return ranked_hits


def _read_cached_results(query: str, limit: int) -> list[PodcastEpisodeSearchHit] | None:
    settings = get_settings()
    ttl = settings.podcast_search_cache_ttl_seconds
    if ttl <= 0:
        return None

    cache_key = f"{query.lower()}::{limit}"
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if not cached:
            return None
        if cached.expires_at <= now:
            _SEARCH_CACHE.pop(cache_key, None)
            return None
        _SEARCH_CACHE.pop(cache_key)
        _SEARCH_CACHE[cache_key] = cached
        return list(cached.hits)


def _write_cached_results(
    query: str,
    limit: int,
    hits: list[PodcastEpisodeSearchHit],
    *,
    degraded: bool,
) -> None:
    settings = get_settings()
    full_ttl = float(settings.podcast_search_cache_ttl_seconds)
    ttl = (
        min(full_ttl, float(settings.podcast_search_provider_timeout_seconds))
        if degraded
        else full_ttl
    )
    if ttl <= 0 or (degraded and not hits):
        return

    cache_key = f"{query.lower()}::{limit}"
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        for expired_key, entry in tuple(_SEARCH_CACHE.items()):
            if entry.expires_at <= now:
                _SEARCH_CACHE.pop(expired_key, None)
        _SEARCH_CACHE.pop(cache_key, None)
        while len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX_ENTRIES:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
        _SEARCH_CACHE[cache_key] = _PodcastSearchCacheEntry(
            expires_at=now + ttl,
            hits=tuple(hits),
        )


def _run_provider(
    provider_name: str,
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> tuple[list[PodcastEpisodeSearchHit], bool]:
    """Return provider hits plus whether the provider completed successfully."""

    if _deadline_expired(deadline):
        return [], False
    if _is_provider_open(provider_name):
        logger.debug(
            "Skipping provider due to open circuit",
            extra={
                "component": "podcast_search",
                "operation": "provider_skip",
                "context_data": {"provider": provider_name},
            },
        )
        return [], False

    provider_map = {
        "listen_notes": _search_listen_notes,
        "spotify": _search_spotify,
        "apple_itunes": _search_apple_itunes,
        "podcast_index": _search_podcast_index,
        "exa": _search_exa,
    }
    provider_fn = provider_map.get(provider_name)
    if not provider_fn:
        return [], False

    try:
        hits = provider_fn(query, limit, deadline=deadline)
        if _deadline_expired(deadline):
            return [], False
        _record_provider_success(provider_name)
        return hits, True
    except _PodcastSearchDeadlineExceeded:
        return [], False
    except Exception as exc:  # noqa: BLE001
        if _deadline_expired(deadline):
            return [], False
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
        return [], False


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _effective_search_deadline(deadline: float | None) -> float:
    internal_deadline = time.monotonic() + max(
        0.001,
        float(get_settings().podcast_search_provider_timeout_seconds),
    )
    return internal_deadline if deadline is None else min(deadline, internal_deadline)


def _submit_provider(
    provider_name: str,
    query: str,
    limit: int,
    *,
    deadline: float,
) -> Future[tuple[list[PodcastEpisodeSearchHit], bool]] | None:
    if _deadline_expired(deadline):
        return None

    admission = _PROVIDER_ADMISSION
    if not admission.acquire(blocking=False):
        return None

    try:
        future = _PROVIDER_EXECUTOR.submit(
            _run_provider,
            provider_name,
            query,
            limit,
            deadline=deadline,
        )
    except RuntimeError:
        admission.release()
        raise

    def _release_admission_slot(
        _future: Future[tuple[list[PodcastEpisodeSearchHit], bool]],
    ) -> None:
        admission.release()

    future.add_done_callback(_release_admission_slot)
    return future


def _remaining_provider_timeout(deadline: float | None) -> float:
    configured = max(0.001, float(get_settings().podcast_search_provider_timeout_seconds))
    if deadline is None:
        return configured
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _PodcastSearchDeadlineExceeded("Podcast search deadline expired")
    return min(configured, remaining)


def _remaining_shared_deadline(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _PodcastSearchDeadlineExceeded("Podcast search deadline expired")
    return remaining


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


def _search_listen_notes(
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
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
        deadline=deadline,
    )

    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    hits: list[PodcastEpisodeSearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        episode_url = normalize_podcast_http_url(
            _string_or_none(item.get("link")) or _string_or_none(item.get("listennotes_url"))
        )
        if not episode_url:
            continue

        podcast_value = item.get("podcast")
        podcast = podcast_value if isinstance(podcast_value, dict) else {}
        podcast_title = _string_or_none(podcast.get("title_original"))
        source = _string_or_none(podcast.get("publisher")) or _source_from_url(episode_url)
        feed_url = normalize_podcast_http_url(
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


def _search_spotify(
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
    token = _get_spotify_token(deadline=deadline)
    if not token:
        return []

    payload = _spotify_search(
        token=token,
        query=query,
        limit=min(limit, 20),
        deadline=deadline,
    )
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
        episode_url = normalize_podcast_http_url(_nested_string(item, "external_urls", "spotify"))
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


def _spotify_search(
    token: str,
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> dict[str, object] | None:
    settings = get_settings()
    params: dict[str, RequestParamScalar] = {"q": query, "type": "episode", "limit": limit}
    if settings.spotify_market:
        params["market"] = settings.spotify_market

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    timeout = _remaining_provider_timeout(deadline)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get("https://api.spotify.com/v1/search", params=params, headers=headers)
        if response.status_code == 401:
            refreshed = _get_spotify_token(
                deadline=deadline,
                rejected_token=token,
            )
            if not refreshed:
                return None
            headers["Authorization"] = f"Bearer {refreshed}"
            response = client.get(
                "https://api.spotify.com/v1/search",
                params=params,
                headers=headers,
                timeout=_remaining_provider_timeout(deadline),
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


def _get_spotify_token(
    *,
    deadline: float | None = None,
    rejected_token: str | None = None,
) -> str | None:
    global _SPOTIFY_TOKEN  # noqa: PLW0603

    settings = get_settings()
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    if deadline is None:
        acquired = _SPOTIFY_TOKEN_LOCK.acquire()
    else:
        acquired = _SPOTIFY_TOKEN_LOCK.acquire(timeout=_remaining_shared_deadline(deadline))
    if not acquired:
        raise _PodcastSearchDeadlineExceeded("Spotify token lock exceeded podcast search deadline")
    try:
        if (
            _SPOTIFY_TOKEN
            and _SPOTIFY_TOKEN.access_token != rejected_token
            and (_SPOTIFY_TOKEN.expires_at_epoch - time.time()) > 30
        ):
            return _SPOTIFY_TOKEN.access_token

        timeout = _remaining_provider_timeout(deadline)
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
    finally:
        _SPOTIFY_TOKEN_LOCK.release()


def _search_apple_itunes(
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
    settings = get_settings()
    params: dict[str, RequestParamScalar] = {
        "term": query,
        "media": "podcast",
        "entity": "podcastEpisode",
        "limit": min(limit, 25),
    }
    if settings.discovery_itunes_country:
        params["country"] = settings.discovery_itunes_country

    payload = _http_get_json(
        "https://itunes.apple.com/search",
        params=params,
        deadline=deadline,
    )
    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    hits: list[PodcastEpisodeSearchHit] = []
    for index, item in enumerate(results):
        if _deadline_expired(deadline):
            break
        if not isinstance(item, dict):
            continue

        episode_url = normalize_podcast_http_url(_string_or_none(item.get("trackViewUrl")))
        if not episode_url:
            continue

        feed_url = normalize_podcast_http_url(_string_or_none(item.get("feedUrl")))
        if not feed_url and index < MAX_APPLE_FEED_LOOKUPS_PER_PROVIDER:
            feed_url = _resolve_feed_url(episode_url, deadline=deadline)

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


def _search_podcast_index(
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
    settings = get_settings()
    if not settings.podcast_index_api_key or not settings.podcast_index_api_secret:
        return []

    search_payload = _podcast_index_request(
        path="/search/byterm",
        params={"q": query, "max": min(10, max(4, limit // 2))},
        deadline=deadline,
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
        if _deadline_expired(deadline):
            break
        if not isinstance(feed, dict):
            continue
        feed_id = feed.get("id")
        if feed_id is None:
            continue
        feed_title = _string_or_none(feed.get("title"))
        feed_url = normalize_podcast_http_url(_string_or_none(feed.get("url")))

        episodes_payload = _podcast_index_request(
            path="/episodes/byfeedid",
            params={"id": str(feed_id), "max": min(3, limit), "fulltext": ""},
            deadline=deadline,
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
            episode_url = normalize_podcast_http_url(
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


def _podcast_index_request(
    path: str,
    params: RequestParams,
    *,
    deadline: float | None = None,
) -> dict[str, object]:
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
        deadline=deadline,
    )


def _search_exa(
    query: str,
    limit: int,
    *,
    deadline: float | None = None,
) -> list[PodcastEpisodeSearchHit]:
    if not get_settings().exa_api_key:
        return []
    raw_results = exa_search(
        query=f"{query} podcast episode",
        num_results=min(MAX_EXA_RESULTS, max(limit, limit * 2)),
        raise_on_error=True,
        request_timeout_seconds=_remaining_provider_timeout(deadline),
    )

    hits: list[PodcastEpisodeSearchHit] = []
    apple_feed_urls: dict[str, str | None] = {}
    for result in raw_results:
        if _deadline_expired(deadline):
            break
        episode_url = normalize_podcast_http_url(result.url)
        if not episode_url:
            continue
        if not _looks_like_podcast_result(result.title, result.snippet, episode_url):
            continue

        feed_url = None
        apple_show_id = extract_apple_podcast_id(episode_url)
        if apple_show_id in apple_feed_urls:
            feed_url = apple_feed_urls[apple_show_id]
        elif (
            apple_show_id is not None and len(apple_feed_urls) < MAX_APPLE_FEED_LOOKUPS_PER_PROVIDER
        ):
            feed_url = _resolve_feed_url(episode_url, deadline=deadline)
            apple_feed_urls[apple_show_id] = feed_url

        hits.append(
            PodcastEpisodeSearchHit(
                title=result.title or "Untitled Episode",
                episode_url=episode_url,
                podcast_title=None,
                source=_source_from_url(episode_url),
                snippet=_clean_text(result.snippet),
                feed_url=feed_url,
                published_at=_normalize_published_date(result.published_date),
                provider="exa",
                score=PROVIDER_WEIGHTS["exa"],
            )
        )

    return hits


def _http_get_json(
    url: str,
    params: RequestParams | None = None,
    headers: dict[str, str] | None = None,
    *,
    deadline: float | None = None,
) -> dict[str, object]:
    timeout = _remaining_provider_timeout(deadline)
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


def _resolve_feed_url(
    episode_url: str,
    *,
    deadline: float | None = None,
) -> str | None:
    parsed = urlparse(episode_url)
    host = parsed.hostname
    if not any(
        is_domain_or_subdomain(host, domain)
        for domain in ("podcasts.apple.com", "itunes.apple.com")
    ):
        return None

    try:
        show_id = extract_apple_podcast_id(episode_url)
        if not show_id:
            return None
        params: dict[str, RequestParamScalar] = {
            "id": show_id,
            "entity": "podcast",
        }
        country = get_settings().discovery_itunes_country
        if country:
            params["country"] = country
        payload = _http_get_json(
            "https://itunes.apple.com/lookup",
            params=params,
            deadline=deadline,
        )
        results = payload.get("results", [])
        if not isinstance(results, list):
            return None
        for item in results:
            if isinstance(item, dict):
                feed_url = normalize_podcast_http_url(_string_or_none(item.get("feedUrl")))
                if feed_url:
                    return feed_url
        return None
    except _PodcastSearchDeadlineExceeded:
        raise
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
