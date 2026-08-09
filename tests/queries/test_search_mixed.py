"""Tests for mixed search query orchestration."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from time import perf_counter, sleep
from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import Session

from app.models.api.content import ContentSummaryResponse
from app.models.contracts import ContentClassification, ContentStatus, ContentType
from app.queries import search_mixed


class _HoldingExecutor:
    def __init__(self) -> None:
        self.futures: list[Future[object]] = []

    def submit(self, *_args, **_kwargs) -> Future[object]:
        future: Future[object] = Future()
        self.futures.append(future)
        return future


def test_search_mixed_combines_local_feed_and_podcast_results(monkeypatch) -> None:
    calls: dict[str, object] = {}
    external_search_barrier = threading.Barrier(2)
    content_card = ContentSummaryResponse.model_construct(
        id=1,
        content_type=ContentType.ARTICLE,
        url="https://example.com/article",
        title="Local Article",
        status=ContentStatus.COMPLETED,
        short_summary="Local result",
        created_at="2026-04-27T12:00:00Z",
        classification=ContentClassification.TO_READ,
    )

    def fake_search_content_cards_execute(db, **kwargs):
        calls["content"] = {"db": db, **kwargs}
        return SimpleNamespace(contents=[content_card])

    def fake_find_feed_options(**kwargs):
        calls["feeds"] = kwargs
        external_search_barrier.wait(timeout=1)
        return SimpleNamespace(
            options=[
                SimpleNamespace(
                    id="feed-1",
                    title="Example Feed",
                    site_url="https://example.com",
                    feed_url="https://example.com/feed",
                    feed_type="substack",
                    feed_format="rss",
                    description="Feed description",
                    rationale="Good match",
                    evidence_url="https://example.com/about",
                )
            ]
        )

    def fake_search_podcast_episodes(**kwargs):
        calls["podcasts"] = kwargs
        external_search_barrier.wait(timeout=1)
        return [
            SimpleNamespace(
                title="Episode",
                episode_url="https://podcasts.example.com/episode",
                podcast_title="Podcast",
                source="listen_notes",
                snippet="Snippet",
                feed_url="https://podcasts.example.com/feed",
                published_at="2026-04-26T12:00:00Z",
                provider="listen_notes",
                score=0.9,
            )
        ]

    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        fake_search_content_cards_execute,
    )
    monkeypatch.setattr(search_mixed, "find_feed_options", fake_find_feed_options)
    monkeypatch.setattr(search_mixed, "search_podcast_episodes", fake_search_podcast_episodes)
    monkeypatch.setattr(
        search_mixed,
        "load_active_feed_urls",
        lambda _db, *, user_id: {"https://example.com/feed"} if user_id == 7 else set(),
    )

    db = cast(Session, object())
    response = search_mixed.execute(db, user_id=7, query="ai", limit=9)

    assert response.query == "ai"
    assert response.content == [content_card]
    assert response.feeds[0].feed_url == "https://example.com/feed"
    assert response.feeds[0].is_subscribed is True
    assert response.podcasts[0].episode_url == "https://podcasts.example.com/episode"
    assert calls["content"] == {
        "db": db,
        "user_id": 7,
        "q": "ai",
        "content_type": "all",
        "limit": 9,
        "cursor": None,
        "offset": 0,
    }
    assert calls["feeds"]["deadline"] > perf_counter()
    assert calls["feeds"] == {
        "query": "ai",
        "limit": 5,
        "user_id": 7,
        "deadline": calls["feeds"]["deadline"],
    }
    assert calls["podcasts"] == {
        "query": "ai",
        "limit": 9,
        "deadline": calls["feeds"]["deadline"],
    }


def test_search_mixed_degrades_one_failed_external_section(monkeypatch) -> None:
    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(contents=[]),
    )
    monkeypatch.setattr(
        search_mixed,
        "find_feed_options",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("E2B unavailable")),
    )
    monkeypatch.setattr(
        search_mixed,
        "search_podcast_episodes",
        lambda **_kwargs: [
            SimpleNamespace(
                title="Episode",
                episode_url="https://podcasts.example.com/episode",
                podcast_title="Podcast",
                source="provider",
                snippet=None,
                feed_url=None,
                published_at=None,
                provider="provider",
                score=0.8,
            )
        ],
    )
    monkeypatch.setattr(search_mixed, "load_active_feed_urls", lambda *_args, **_kwargs: set())

    response = search_mixed.execute(cast(Session, object()), user_id=7, query="ai", limit=5)

    assert response.feeds == []
    assert len(response.podcasts) == 1


def test_search_mixed_returns_after_external_section_timeout(monkeypatch) -> None:
    release_feed_search = threading.Event()
    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(contents=[]),
    )

    def blocked_feed_search(**_kwargs):
        release_feed_search.wait(timeout=2)
        return SimpleNamespace(options=[])

    monkeypatch.setattr(search_mixed, "find_feed_options", blocked_feed_search)
    monkeypatch.setattr(search_mixed, "search_podcast_episodes", lambda **_kwargs: [])
    monkeypatch.setattr(search_mixed, "load_active_feed_urls", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(search_mixed, "MIXED_SEARCH_EXTERNAL_TIMEOUT_SECONDS", 0.01)

    started_at = perf_counter()
    response = search_mixed.execute(cast(Session, object()), user_id=7, query="ai", limit=5)
    duration = perf_counter() - started_at
    release_feed_search.set()

    assert duration < 0.5
    assert response.feeds == []
    assert response.podcasts == []


def test_repeated_timeouts_keep_external_work_bounded(monkeypatch) -> None:
    release_searches = threading.Event()
    active_lock = threading.Lock()
    active_count = 0
    maximum_active = 0
    started_count = 0
    initial_workers_started = threading.Event()

    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(contents=[]),
    )

    def blocked_search(**_kwargs):
        nonlocal active_count, maximum_active, started_count
        with active_lock:
            active_count += 1
            started_count += 1
            maximum_active = max(maximum_active, active_count)
            if started_count == search_mixed.MIXED_SEARCH_EXTERNAL_MAX_WORKERS:
                initial_workers_started.set()
        try:
            release_searches.wait(timeout=2)
            return SimpleNamespace(options=[])
        finally:
            with active_lock:
                active_count -= 1

    monkeypatch.setattr(search_mixed, "find_feed_options", blocked_search)
    monkeypatch.setattr(search_mixed, "search_podcast_episodes", blocked_search)
    monkeypatch.setattr(search_mixed, "load_active_feed_urls", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(search_mixed, "MIXED_SEARCH_EXTERNAL_TIMEOUT_SECONDS", 0.01)

    try:
        for request_index in range(6):
            response = search_mixed.execute(
                cast(Session, object()),
                user_id=7,
                query="slow",
                limit=5,
            )
            assert response.feeds == []
            assert response.podcasts == []
            if request_index == 1:
                assert initial_workers_started.wait(timeout=1)
        assert maximum_active <= search_mixed.MIXED_SEARCH_EXTERNAL_MAX_WORKERS
    finally:
        release_searches.set()

    sleep(0.05)
    assert started_count == search_mixed.MIXED_SEARCH_EXTERNAL_MAX_WORKERS


def test_two_concurrent_requests_start_all_external_sections(monkeypatch) -> None:
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mixed-search-test")
    monkeypatch.setattr(search_mixed, "_EXTERNAL_SEARCH_EXECUTOR", executor)
    monkeypatch.setattr(search_mixed, "_EXTERNAL_SEARCH_SLOTS", threading.BoundedSemaphore(4))
    monkeypatch.setattr(
        search_mixed.search_content_cards,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(contents=[]),
    )
    monkeypatch.setattr(search_mixed, "load_active_feed_urls", lambda *_args, **_kwargs: set())

    started: list[tuple[str, str]] = []
    started_lock = threading.Lock()
    all_started = threading.Event()
    release = threading.Event()

    def record_start(section: str, query: str) -> None:
        with started_lock:
            started.append((section, query))
            if len(started) == 4:
                all_started.set()
        assert release.wait(timeout=2)

    def feed_search(**kwargs):
        query = str(kwargs["query"])
        record_start("feed", query)
        return SimpleNamespace(
            options=[
                SimpleNamespace(
                    id=f"feed-{query}",
                    title=f"Feed {query}",
                    site_url=f"https://{query}.example.com",
                    feed_url=f"https://{query}.example.com/feed.xml",
                    feed_type="atom",
                    feed_format="rss",
                    description=None,
                    rationale=None,
                    evidence_url=None,
                )
            ]
        )

    def podcast_search(**kwargs):
        query = str(kwargs["query"])
        record_start("podcast", query)
        return [
            SimpleNamespace(
                title=f"Episode {query}",
                episode_url=f"https://podcasts.example.com/{query}",
                podcast_title="Podcast",
                source="provider",
                snippet=None,
                feed_url=None,
                published_at=None,
                provider="provider",
                score=0.8,
            )
        ]

    monkeypatch.setattr(search_mixed, "find_feed_options", feed_search)
    monkeypatch.setattr(search_mixed, "search_podcast_episodes", podcast_search)
    responses: dict[str, object] = {}
    errors: list[BaseException] = []
    request_barrier = threading.Barrier(2)

    def run_request(query: str) -> None:
        try:
            request_barrier.wait(timeout=1)
            responses[query] = search_mixed.execute(
                cast(Session, object()),
                user_id=7,
                query=query,
                limit=5,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve thread assertion evidence
            errors.append(exc)

    threads = [threading.Thread(target=run_request, args=(query,)) for query in ("a", "b")]
    try:
        for thread in threads:
            thread.start()
        assert all_started.wait(timeout=1)
        release.set()
        for thread in threads:
            thread.join(timeout=2)
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)

    assert errors == []
    assert sorted(started) == [
        ("feed", "a"),
        ("feed", "b"),
        ("podcast", "a"),
        ("podcast", "b"),
    ]
    for query in ("a", "b"):
        response = responses[query]
        assert response.feeds[0].title == f"Feed {query}"
        assert response.podcasts[0].title == f"Episode {query}"


def test_external_admission_is_bounded_and_cancelled_jobs_release_their_slot(
    monkeypatch,
) -> None:
    executor = _HoldingExecutor()
    slots = threading.BoundedSemaphore(search_mixed.MIXED_SEARCH_EXTERNAL_ADMISSION_CAPACITY)
    monkeypatch.setattr(search_mixed, "_EXTERNAL_SEARCH_EXECUTOR", executor)
    monkeypatch.setattr(search_mixed, "_EXTERNAL_SEARCH_SLOTS", slots)
    deadline = search_mixed.monotonic() + 10

    admitted = [
        search_mixed._submit_external_search(lambda: None, deadline=deadline, kwargs={})
        for _ in range(search_mixed.MIXED_SEARCH_EXTERNAL_ADMISSION_CAPACITY)
    ]

    assert all(future is not None for future in admitted)
    assert search_mixed._submit_external_search(lambda: None, deadline=deadline, kwargs={}) is None
    first = admitted[0]
    assert first is not None
    assert first.cancel() is True
    replacement = search_mixed._submit_external_search(
        lambda: None,
        deadline=deadline,
        kwargs={},
    )
    assert replacement is not None

    for future in [*admitted[1:], replacement]:
        assert future is not None
        future.cancel()
