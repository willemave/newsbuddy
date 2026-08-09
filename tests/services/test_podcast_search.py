from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.models.db import VendorUsageRecord
from app.services.podcast_search import PodcastEpisodeSearchHit, search_podcast_episodes


def _build_hit(
    *,
    title: str,
    url: str,
    provider: str,
    score: float,
    podcast_title: str | None = None,
) -> PodcastEpisodeSearchHit:
    return PodcastEpisodeSearchHit(
        title=title,
        episode_url=url,
        podcast_title=podcast_title,
        source=None,
        snippet=None,
        feed_url=None,
        published_at=None,
        provider=provider,
        score=score,
    )


def test_search_podcast_episodes_merges_and_dedupes(monkeypatch):
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    podcast_search._PROVIDER_STATES.clear()

    monkeypatch.setattr(
        podcast_search,
        "_search_listen_notes",
        lambda _query, _limit, **_kwargs: [
            _build_hit(
                title="OpenAI Dev Day Podcast Episode",
                url="https://example.fm/episodes/dev-day?utm_source=test",
                provider="listen_notes",
                score=0.95,
                podcast_title="AI Weekly",
            )
        ],
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_spotify",
        lambda _query, _limit, **_kwargs: [
            _build_hit(
                title="OpenAI release recap",
                url="https://open.spotify.com/episode/abc123?si=tracking",
                provider="spotify",
                score=0.9,
                podcast_title="AI Daily",
            )
        ],
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_apple_itunes",
        lambda _query, _limit, **_kwargs: [],
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_podcast_index",
        lambda _query, _limit, **_kwargs: [],
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_exa",
        lambda _query, _limit, **_kwargs: [
            _build_hit(
                title="Duplicate episode result from Exa",
                url="https://example.fm/episodes/dev-day",
                provider="exa",
                score=0.6,
                podcast_title="AI Weekly",
            )
        ],
    )

    hits = search_podcast_episodes("openai dev day", limit=10)

    assert len(hits) == 2
    assert hits[0].provider == "listen_notes"
    assert hits[0].episode_url.startswith("https://example.fm/episodes/dev-day")
    assert hits[1].provider == "spotify"


def test_search_podcast_episodes_short_query_returns_empty(monkeypatch):
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    podcast_search._PROVIDER_STATES.clear()

    called = False

    def _stub_provider(_query: str, _limit: int, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(podcast_search, "_search_listen_notes", _stub_provider)
    monkeypatch.setattr(podcast_search, "_search_spotify", _stub_provider)
    monkeypatch.setattr(podcast_search, "_search_apple_itunes", _stub_provider)
    monkeypatch.setattr(podcast_search, "_search_podcast_index", _stub_provider)
    monkeypatch.setattr(podcast_search, "_search_exa", _stub_provider)

    hits = search_podcast_episodes("x", limit=5)

    assert hits == []
    assert called is False


def test_search_podcast_episodes_runs_providers_concurrently_and_collects_in_order(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    started = {name: threading.Event() for name in podcast_search.PROVIDER_ORDER}
    release = {name: threading.Event() for name in podcast_search.PROVIDER_ORDER}
    outcome: dict[str, list[PodcastEpisodeSearchHit]] = {}

    def _provider(
        provider_name: str,
        _query: str,
        _limit: int,
        **_kwargs,
    ) -> tuple[list[PodcastEpisodeSearchHit], bool]:
        started[provider_name].set()
        release[provider_name].wait(timeout=2)
        return (
            [
                _build_hit(
                    title="Concurrent result",
                    url=f"https://example.fm/{provider_name}",
                    provider=provider_name,
                    score=1.0,
                )
            ],
            True,
        )

    monkeypatch.setattr(podcast_search, "_run_provider", _provider)
    monkeypatch.setattr(
        podcast_search,
        "rank_and_dedupe_hits",
        lambda _query, hits: hits,
    )

    search_thread = threading.Thread(
        target=lambda: outcome.update(hits=search_podcast_episodes("concurrent providers"))
    )
    search_thread.start()
    try:
        assert all(event.wait(timeout=1) for event in started.values())
        for provider_name in reversed(podcast_search.PROVIDER_ORDER):
            release[provider_name].set()
    finally:
        for event in release.values():
            event.set()
        search_thread.join(timeout=2)

    assert not search_thread.is_alive()
    assert [hit.provider for hit in outcome["hits"]] == list(podcast_search.PROVIDER_ORDER)


def test_search_podcast_episodes_uses_internal_deadline_and_returns_fast_partial(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    release = threading.Event()
    finished = {
        name: threading.Event() for name in podcast_search.PROVIDER_ORDER if name != "listen_notes"
    }
    deadlines: list[float] = []
    deadline_lock = threading.Lock()

    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(
            podcast_search_cache_ttl_seconds=300,
            podcast_search_provider_timeout_seconds=0.05,
        ),
    )

    def _provider(
        provider_name: str,
        _query: str,
        _limit: int,
        *,
        deadline: float,
    ) -> tuple[list[PodcastEpisodeSearchHit], bool]:
        with deadline_lock:
            deadlines.append(deadline)
        if provider_name == "listen_notes":
            return (
                [
                    _build_hit(
                        title="Fast partial",
                        url="https://example.fm/fast-partial",
                        provider=provider_name,
                        score=0.9,
                    )
                ],
                True,
            )
        release.wait(timeout=1)
        finished[provider_name].set()
        return [], True

    monkeypatch.setattr(podcast_search, "_run_provider", _provider)
    started_at = time.monotonic()
    try:
        hits = search_podcast_episodes("internal deadline")
        elapsed = time.monotonic() - started_at
    finally:
        release.set()
        assert all(event.wait(timeout=1) for event in finished.values())

    assert len(hits) == 1
    assert hits[0].title == "Fast partial"
    assert elapsed < 0.5
    assert len(deadlines) == len(podcast_search.PROVIDER_ORDER)
    assert max(deadlines) - min(deadlines) < 0.01


def test_deadline_expiration_does_not_trip_provider_circuit(monkeypatch):
    from app.services import podcast_search

    podcast_search._PROVIDER_STATES.clear()
    deadline_checks = iter([False, True])
    monkeypatch.setattr(
        podcast_search,
        "_deadline_expired",
        lambda _deadline: next(deadline_checks),
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_listen_notes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("deadline")),
    )

    assert podcast_search._run_provider(
        "listen_notes",
        "deadline",
        5,
        deadline=1.0,
    ) == ([], False)
    assert podcast_search._PROVIDER_STATES == {}


def test_shared_deadline_exception_never_trips_provider_circuit(monkeypatch) -> None:
    from app.services import podcast_search

    podcast_search._PROVIDER_STATES.clear()
    monkeypatch.setattr(
        podcast_search,
        "_search_listen_notes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            podcast_search._PodcastSearchDeadlineExceeded("shared deadline")
        ),
    )

    assert podcast_search._run_provider(
        "listen_notes",
        "deadline",
        5,
        deadline=podcast_search.time.monotonic() + 10,
    ) == ([], False)
    assert podcast_search._PROVIDER_STATES == {}


def test_spotify_token_lock_uses_shared_not_provider_timeout(monkeypatch) -> None:
    from app.services import podcast_search

    captured: dict[str, float] = {}

    class FakeLock:
        def acquire(self, *, timeout: float) -> bool:
            captured["timeout"] = timeout
            return False

        def release(self) -> None:
            raise AssertionError("an unacquired lock must not be released")

    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "spotify_client_id": "client",
                "spotify_client_secret": "secret",
                "podcast_search_provider_timeout_seconds": 0.01,
            },
        )(),
    )
    monkeypatch.setattr(podcast_search, "_SPOTIFY_TOKEN_LOCK", FakeLock())

    with pytest.raises(podcast_search._PodcastSearchDeadlineExceeded):
        podcast_search._get_spotify_token(
            deadline=podcast_search.time.monotonic() + 1,
        )

    assert captured["timeout"] > 0.5


def test_search_podcast_episodes_caches_provider_failure_partials_as_degraded(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    podcast_search._PROVIDER_STATES.clear()
    cache_writes: list[tuple[list[PodcastEpisodeSearchHit], bool]] = []

    monkeypatch.setattr(
        podcast_search,
        "_search_listen_notes",
        lambda *_args, **_kwargs: [
            _build_hit(
                title="Available result",
                url="https://example.fm/available",
                provider="listen_notes",
                score=0.9,
            )
        ],
    )
    monkeypatch.setattr(
        podcast_search,
        "_search_spotify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    monkeypatch.setattr(podcast_search, "_search_apple_itunes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(podcast_search, "_search_podcast_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(podcast_search, "_search_exa", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        podcast_search,
        "_write_cached_results",
        lambda _query, _limit, hits, *, degraded: cache_writes.append((hits, degraded)),
    )

    hits = search_podcast_episodes("provider partial")

    assert [hit.title for hit in hits] == ["Available result"]
    assert len(cache_writes) == 1
    assert [hit.title for hit in cache_writes[0][0]] == ["Available result"]
    assert cache_writes[0][1] is True


def test_search_cache_uses_short_degraded_ttl_and_full_ttl(monkeypatch) -> None:
    from app.services import podcast_search

    clock = {"now": 100.0}
    hit = _build_hit(
        title="Cached",
        url="https://example.fm/cached",
        provider="listen_notes",
        score=0.9,
    )
    podcast_search._SEARCH_CACHE.clear()
    monkeypatch.setattr(podcast_search.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(
            podcast_search_cache_ttl_seconds=300,
            podcast_search_provider_timeout_seconds=6,
        ),
    )

    podcast_search._write_cached_results(
        "degraded",
        10,
        [hit],
        degraded=True,
    )
    podcast_search._write_cached_results(
        "complete",
        10,
        [hit],
        degraded=False,
    )
    clock["now"] = 107.0

    assert podcast_search._read_cached_results("degraded", 10) is None
    assert podcast_search._read_cached_results("complete", 10) == [hit]
    podcast_search._SEARCH_CACHE.clear()


def test_search_cache_is_bounded_and_refreshes_lru_order(monkeypatch) -> None:
    from app.services import podcast_search

    podcast_search._SEARCH_CACHE.clear()
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(
            podcast_search_cache_ttl_seconds=300,
            podcast_search_provider_timeout_seconds=6,
        ),
    )
    hit = _build_hit(
        title="Cached",
        url="https://example.fm/cached",
        provider="listen_notes",
        score=0.9,
    )
    for index in range(podcast_search._SEARCH_CACHE_MAX_ENTRIES):
        podcast_search._write_cached_results(
            f"query {index}",
            10,
            [hit],
            degraded=False,
        )

    assert podcast_search._read_cached_results("query 0", 10) == [hit]
    podcast_search._write_cached_results(
        "query overflow",
        10,
        [hit],
        degraded=False,
    )

    assert len(podcast_search._SEARCH_CACHE) == podcast_search._SEARCH_CACHE_MAX_ENTRIES
    assert podcast_search._read_cached_results("query 0", 10) == [hit]
    assert podcast_search._read_cached_results("query 1", 10) is None
    podcast_search._SEARCH_CACHE.clear()


def test_provider_submission_rejects_saturation_without_queueing(monkeypatch) -> None:
    from app.services import podcast_search

    class FullAdmission:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

    class UnexpectedExecutor:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("a saturated search must not queue provider work")

    monkeypatch.setattr(podcast_search, "_PROVIDER_ADMISSION", FullAdmission())
    monkeypatch.setattr(podcast_search, "_PROVIDER_EXECUTOR", UnexpectedExecutor())

    assert (
        podcast_search._submit_provider(
            "listen_notes",
            "query",
            5,
            deadline=time.monotonic() + 1,
        )
        is None
    )


def test_provider_submission_releases_admission_when_future_finishes(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    class Admission:
        releases = 0

        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return True

        def release(self) -> None:
            self.releases += 1

    class ImmediateExecutor:
        def submit(self, *_args, **_kwargs):
            future: Future[tuple[list[PodcastEpisodeSearchHit], bool]] = Future()
            future.set_result(([], True))
            return future

    admission = Admission()
    monkeypatch.setattr(podcast_search, "_PROVIDER_ADMISSION", admission)
    monkeypatch.setattr(podcast_search, "_PROVIDER_EXECUTOR", ImmediateExecutor())

    future = podcast_search._submit_provider(
        "listen_notes",
        "query",
        5,
        deadline=time.monotonic() + 1,
    )

    assert future is not None
    assert future.result() == ([], True)
    assert admission.releases == 1


def test_http_provider_timeout_is_clamped_to_remaining_deadline(monkeypatch):
    from app.services import podcast_search

    captured: dict[str, float] = {}
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {"podcast_search_provider_timeout_seconds": 15.0},
        )(),
    )
    monkeypatch.setattr(podcast_search.time, "monotonic", lambda: 100.0)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        def __init__(self, *, timeout: float, **_kwargs) -> None:
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(podcast_search.httpx, "Client", FakeClient)

    assert (
        podcast_search._http_get_json(
            "https://example.com/search",
            deadline=102.0,
        )
        == {}
    )
    assert captured["timeout"] == 2.0


def test_exa_provider_uses_configured_timeout_without_shared_deadline(monkeypatch) -> None:
    from app.services import podcast_search

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "exa_api_key": "key",
                "podcast_search_provider_timeout_seconds": 6.0,
            },
        )(),
    )

    def _exa_search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(podcast_search, "exa_search", _exa_search)

    assert podcast_search._search_exa("openai", 5) == []
    assert captured["raise_on_error"] is True
    assert captured["request_timeout_seconds"] == 6.0


def test_apple_feed_resolution_uses_one_bounded_country_aware_path(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(discovery_itunes_country="US"),
    )

    def _http_get_json(_url: str, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {"results": [{"feedUrl": "https://example.fm/feed.xml"}]}

    monkeypatch.setattr(podcast_search, "_http_get_json", _http_get_json)
    episode_url = "https://podcasts.apple.com/us/podcast/show/id123?i=456"
    explicit_deadline = time.monotonic() + 1

    assert podcast_search._resolve_feed_url(episode_url) == "https://example.fm/feed.xml"
    assert (
        podcast_search._resolve_feed_url(
            episode_url,
            deadline=explicit_deadline,
        )
        == "https://example.fm/feed.xml"
    )
    assert calls == [
        {
            "params": {"id": "123", "entity": "podcast", "country": "US"},
            "deadline": None,
        },
        {
            "params": {"id": "123", "entity": "podcast", "country": "US"},
            "deadline": explicit_deadline,
        },
    ]


def test_apple_feed_resolution_propagates_shared_deadline(monkeypatch) -> None:
    from app.services import podcast_search

    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(discovery_itunes_country=None),
    )
    monkeypatch.setattr(
        podcast_search,
        "_http_get_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            podcast_search._PodcastSearchDeadlineExceeded("expired")
        ),
    )

    with pytest.raises(podcast_search._PodcastSearchDeadlineExceeded):
        podcast_search._resolve_feed_url(
            "https://podcasts.apple.com/us/podcast/show/id123?i=456",
            deadline=time.monotonic() + 1,
        )


def test_exa_caps_apple_feed_resolution_fanout(monkeypatch) -> None:
    from app.services import podcast_search

    results = [
        SimpleNamespace(
            title=f"Podcast episode {index}",
            url=(
                "https://podcasts.apple.com/us/podcast/"
                f"show-{index}/id{1000 + index}?i={2000 + index}"
            ),
            snippet="Podcast episode",
            published_date=None,
        )
        for index in range(40)
    ]
    resolution_calls: list[str] = []
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(
            exa_api_key="key",
            podcast_search_provider_timeout_seconds=6,
        ),
    )
    monkeypatch.setattr(podcast_search, "exa_search", lambda **_kwargs: results)
    monkeypatch.setattr(
        podcast_search,
        "_resolve_feed_url",
        lambda episode_url, **_kwargs: (
            resolution_calls.append(episode_url) or "https://example.fm/feed.xml"
        ),
    )

    hits = podcast_search._search_exa("openai", 25)

    assert len(hits) == 40
    assert len(resolution_calls) == podcast_search.MAX_APPLE_FEED_LOOKUPS_PER_PROVIDER


def test_search_listen_notes_records_vendor_usage(
    db_session,
    vendor_usage_db,
    monkeypatch,
):
    from app.services import podcast_search

    del vendor_usage_db
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type("Settings", (), {"listen_notes_api_key": "key"})(),
    )
    monkeypatch.setattr(
        podcast_search,
        "_http_get_json",
        lambda *_args, **_kwargs: {
            "results": [
                {
                    "title_original": "Episode A",
                    "link": "https://example.fm/episode-a",
                    "podcast": {"title_original": "Show", "publisher": "Publisher"},
                }
            ]
        },
    )

    hits = podcast_search._search_listen_notes("openai", 5)

    assert len(hits) == 1
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "listen_notes"
    assert row.feature == "podcast_search"
    assert row.request_count == 1
    assert row.resource_count == 1


def test_spotify_search_records_vendor_usage(
    db_session,
    vendor_usage_db,
    monkeypatch,
):
    from app.services import podcast_search

    del vendor_usage_db
    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "spotify_market": "US",
                "podcast_search_provider_timeout_seconds": 15.0,
            },
        )(),
    )

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "episodes": {
                    "items": [
                        {
                            "name": "Episode A",
                            "external_urls": {"spotify": "https://open.spotify.com/episode/abc"},
                            "show": {"name": "Show", "publisher": "Publisher"},
                        }
                    ]
                }
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(podcast_search.httpx, "Client", lambda **_kwargs: FakeClient())

    payload = podcast_search._spotify_search("token", "openai", 5)

    assert payload is not None
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "spotify"
    assert row.feature == "podcast_search"
    assert row.request_count == 1
    assert row.resource_count == 1


def test_spotify_retry_reclamps_timeout_after_token_refresh(monkeypatch) -> None:
    from app.services import podcast_search

    timeouts = iter([5.0, 0.25])
    get_calls: list[dict[str, object]] = []
    refresh_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: type("Settings", (), {"spotify_market": "US"})(),
    )
    monkeypatch.setattr(
        podcast_search,
        "_remaining_provider_timeout",
        lambda _deadline: next(timeouts),
    )
    monkeypatch.setattr(
        podcast_search,
        "_get_spotify_token",
        lambda **kwargs: refresh_calls.append(kwargs) or "fresh-token",
    )
    monkeypatch.setattr(podcast_search, "_record_podcast_usage", lambda **_kwargs: None)

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"episodes": {"items": []}}

    class FakeClient:
        def __init__(self, *, timeout: float, **_kwargs) -> None:
            assert timeout == 5.0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **kwargs):
            get_calls.append(kwargs)
            return FakeResponse(401 if len(get_calls) == 1 else 200)

    monkeypatch.setattr(podcast_search.httpx, "Client", FakeClient)

    assert podcast_search._spotify_search(
        "stale-token",
        "openai",
        5,
        deadline=123.0,
    ) == {"episodes": {"items": []}}
    assert "timeout" not in get_calls[0]
    assert get_calls[1]["timeout"] == 0.25
    assert refresh_calls == [{"deadline": 123.0, "rejected_token": "stale-token"}]


def test_spotify_rejection_reuses_token_refreshed_by_another_caller(
    monkeypatch,
) -> None:
    from app.services import podcast_search

    monkeypatch.setattr(
        podcast_search,
        "get_settings",
        lambda: SimpleNamespace(
            spotify_client_id="client",
            spotify_client_secret="secret",
            podcast_search_provider_timeout_seconds=6,
        ),
    )
    monkeypatch.setattr(
        podcast_search,
        "_SPOTIFY_TOKEN",
        podcast_search._SpotifyToken(
            access_token="fresh-token",
            expires_at_epoch=time.time() + 3600,
        ),
    )
    monkeypatch.setattr(
        podcast_search.httpx,
        "Client",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("a concurrently refreshed token must be reused")
        ),
    )

    assert podcast_search._get_spotify_token(rejected_token="stale-token") == "fresh-token"
