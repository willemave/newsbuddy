from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from app.models.db import (
    Content,
    ContentKnowledgeSave,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
    UserScraperConfig,
)
from app.models.llm.feed_discovery import (
    DiscoveryCandidate,
    DiscoveryCandidateBatch,
    DiscoveryDirection,
    DiscoveryDirectionPlan,
    DiscoveryLane,
    DiscoveryLanePlan,
    DiscoveryQuery,
)
from app.services import feed_discovery
from app.services.exa_client import ExaRequestError, ExaSearchResult
from app.services.feed_discovery import FeedDiscoveryDeps, run_feed_discovery
from app.services.vendor_usage import record_model_usage


class _FakeUsage:
    input_tokens = 12
    output_tokens = 8
    total_tokens = 20


class _FakeResult:
    @property
    def usage(self):
        return _FakeUsage()


def _stub_direction_selector(db_session, user_id: int) -> DiscoveryDirectionPlan:
    record_model_usage("direction_select", _FakeResult(), model_spec="test-model")
    rows = (
        db_session.query(ContentKnowledgeSave, Content)
        .join(Content, Content.id == ContentKnowledgeSave.content_id)
        .filter(ContentKnowledgeSave.user_id == user_id)
        .all()
    )
    ids = [content.id for _fav, content in rows]
    return DiscoveryDirectionPlan(
        summary="Stub directions",
        directions=[
            DiscoveryDirection(
                name="Primary",
                rationale="Top favorites",
                favorite_ids=ids[:2] or ids,
            ),
            DiscoveryDirection(
                name="Secondary",
                rationale="More favorites",
                favorite_ids=ids[2:] or ids,
            ),
        ],
    )


def _stub_lane_planner(
    _db_session,
    _user_id: int,
    plan: DiscoveryDirectionPlan,
) -> DiscoveryLanePlan:
    assert plan.directions
    return DiscoveryLanePlan(
        lanes=[
            DiscoveryLane(
                name="Feeds",
                goal="Find RSS feeds",
                target="feeds",
                queries=[
                    DiscoveryQuery(query="tech rss feed", rationale="Stub rationale"),
                    DiscoveryQuery(query="indie blog rss", rationale="Stub rationale"),
                ],
            ),
            DiscoveryLane(
                name="Podcasts",
                goal="Find podcasts",
                target="podcasts",
                queries=[
                    DiscoveryQuery(query="tech podcast rss", rationale="Stub rationale"),
                    DiscoveryQuery(query="product podcast", rationale="Stub rationale"),
                ],
            ),
            DiscoveryLane(
                name="YouTube",
                goal="Find YouTube channels",
                target="youtube",
                queries=[
                    DiscoveryQuery(query="ai youtube channel", rationale="Stub rationale"),
                    DiscoveryQuery(query="engineering youtube", rationale="Stub rationale"),
                ],
            ),
        ]
    )


def _stub_exa_search(query: str, num_results: int) -> list[ExaSearchResult]:
    return [
        ExaSearchResult(
            title=f"Stub result for {query}",
            url="https://www.youtube.com/channel/UC1234567890",
            snippet="Stub snippet",
        )
    ]


def _stub_candidate_extractor(
    _db_session,
    _user_id: int,
    lane: DiscoveryLane,
    results: list[ExaSearchResult],
) -> DiscoveryCandidateBatch:
    assert lane.name
    return DiscoveryCandidateBatch(
        candidates=[
            DiscoveryCandidate(
                title="Stub YouTube",
                site_url="https://www.youtube.com/channel/UC1234567890",
                feed_url="https://www.youtube.com/channel/UC1234567890",
                suggestion_type="youtube",
                rationale="Stub YouTube candidate",
                evidence_urls=[results[0].url],
                score=0.9,
            ),
            DiscoveryCandidate(
                title="Stub Feed",
                site_url="https://example.com",
                feed_url="https://example.com/feed.xml",
                suggestion_type="atom",
                rationale="Stub feed candidate",
                evidence_urls=["https://example.com"],
                score=0.7,
            ),
            DiscoveryCandidate(
                title="Stub Podcast",
                site_url="https://example.com",
                feed_url="https://example.com/podcast.xml",
                suggestion_type="podcast_rss",
                rationale="Stub podcast candidate",
                evidence_urls=["https://example.com"],
                score=0.8,
            ),
        ]
    )


def _stub_candidate_validator(_db, _user_id, candidates, _model_spec):
    return candidates


def test_run_feed_discovery_creates_run_and_suggestions(db_session, test_user, monkeypatch):
    contents = []
    for i in range(5):
        content = Content(
            content_type="article",
            url=f"https://example.com/{i}",
            title=f"Example {i}",
            source="example.com",
            status="completed",
        )
        db_session.add(content)
        contents.append(content)
    db_session.commit()

    for content in contents:
        db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr("app.services.feed_discovery.get_db", _override_get_db)

    deps = FeedDiscoveryDeps(
        direction_selector=lambda db, user_id: _stub_direction_selector(db, user_id),
        lane_planner=_stub_lane_planner,
        candidate_extractor=_stub_candidate_extractor,
        exa_search_fn=_stub_exa_search,
        candidate_validator=_stub_candidate_validator,
    )

    result = run_feed_discovery(user_id=test_user.id, deps=deps)
    assert result.status == "completed"

    runs = db_session.query(FeedDiscoveryRun).all()
    assert len(runs) == 1
    assert runs[0].token_total == 20
    assert runs[0].token_usage
    assert runs[0].duration_ms_total is not None
    assert runs[0].timing_json
    suggestions = db_session.query(FeedDiscoverySuggestion).all()
    assert len(suggestions) == 3


def test_run_feed_discovery_without_favorites_finishes_without_external_work(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr("app.services.feed_discovery.get_db", _override_get_db)

    result = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=lambda *_args: (_ for _ in ()).throw(
                AssertionError("direction selection should not run")
            ),
            lane_planner=lambda *_args: (_ for _ in ()).throw(
                AssertionError("lane planning should not run")
            ),
            candidate_extractor=lambda *_args: (_ for _ in ()).throw(
                AssertionError("candidate extraction should not run")
            ),
            exa_search_fn=lambda *_args: (_ for _ in ()).throw(
                AssertionError("Exa should not run")
            ),
            candidate_validator=lambda *_args: (_ for _ in ()).throw(
                AssertionError("sandbox validation should not run")
            ),
        ),
    )

    assert result.status == "completed"
    run = db_session.query(FeedDiscoveryRun).filter(FeedDiscoveryRun.id == result.run_id).one()
    assert run.error_message == "no_favorites"


def test_run_feed_discovery_with_insufficient_favorites_is_completed_fallback(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    """Too little signal is a valid no-results outcome, not a retryable outage."""
    content = Content(
        content_type="article",
        url="https://example.com/only-favorite",
        title="Only Favorite",
        source="example.com",
        status="completed",
    )
    db_session.add(content)
    db_session.flush()
    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    settings = feed_discovery.get_settings().model_copy(update={"discovery_min_favorites": 2})
    monkeypatch.setattr(feed_discovery, "get_db", _override_get_db)
    monkeypatch.setattr(feed_discovery, "get_settings", lambda: settings)
    unexpected = lambda *_args: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("insufficient signal must not invoke providers")
    )

    result = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=unexpected,
            lane_planner=unexpected,
            candidate_extractor=unexpected,
            exa_search_fn=unexpected,
            candidate_validator=unexpected,
        ),
    )

    assert result.status == "completed"
    run = db_session.get(FeedDiscoveryRun, result.run_id)
    assert run is not None
    assert run.error_message == "insufficient_favorites"


def test_run_feed_discovery_reuses_completed_run_in_active_week_without_paid_work(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    run = FeedDiscoveryRun(
        user_id=test_user.id,
        status="completed",
        seed_content_ids=[],
        created_at=datetime.now(UTC).replace(tzinfo=None),
        completed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            FeedDiscoverySuggestion(
                run_id=run.id,
                user_id=test_user.id,
                suggestion_type=suggestion_type,
                feed_url=f"https://example.com/{suggestion_type}",
                status="new",
                config={},
                metadata_json={},
            )
            for suggestion_type in ("atom", "podcast_rss", "youtube")
        ]
    )
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr(feed_discovery, "get_db", _override_get_db)
    unexpected = lambda *_args: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("completed discovery must not rebill providers")
    )

    result = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=unexpected,
            lane_planner=unexpected,
            candidate_extractor=unexpected,
            exa_search_fn=unexpected,
            candidate_validator=unexpected,
        ),
    )

    assert result.run_id == run.id
    assert (result.feeds, result.podcasts, result.youtube) == (1, 1, 1)
    assert db_session.query(FeedDiscoveryRun).count() == 1


def test_paid_weekly_exa_search_uses_strict_failure_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _search(query: str, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return []

    monkeypatch.setattr(feed_discovery, "exa_search", _search)

    assert feed_discovery._run_exa_search("independent rss", 7) == []
    assert captured == {
        "query": "independent rss",
        "num_results": 7,
        "raise_on_error": True,
    }


def test_exa_outage_marks_weekly_discovery_failed_and_does_not_checkpoint(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    for index in range(5):
        content = Content(
            content_type="article",
            url=f"https://outage.example/{index}",
            title=f"Outage seed {index}",
            source="outage.example",
            status="completed",
        )
        db_session.add(content)
        db_session.flush()
        db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr(feed_discovery, "get_db", _override_get_db)
    outage_calls = 0

    def _outage(*_args):
        nonlocal outage_calls
        outage_calls += 1
        raise ExaRequestError("Exa unavailable")

    unexpected = lambda *_args: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("provider outage must stop candidate work")
    )
    failed = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=_stub_direction_selector,
            lane_planner=_stub_lane_planner,
            candidate_extractor=unexpected,
            exa_search_fn=_outage,
            candidate_validator=unexpected,
        ),
    )

    assert failed.status == "failed"
    assert outage_calls == 1
    failed_run = db_session.get(FeedDiscoveryRun, failed.run_id)
    assert failed_run is not None
    assert failed_run.error_message == "Exa unavailable"

    recovered_calls = 0

    def _recovered_empty(*_args):
        nonlocal recovered_calls
        recovered_calls += 1
        return []

    recovered = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=_stub_direction_selector,
            lane_planner=_stub_lane_planner,
            candidate_extractor=unexpected,
            exa_search_fn=_recovered_empty,
            candidate_validator=lambda *_args: [],
        ),
    )

    assert recovered.status == "completed"
    assert recovered.run_id != failed.run_id
    assert recovered_calls > 0


def test_successful_empty_weekly_discovery_is_reused_without_paid_work(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    for index in range(5):
        content = Content(
            content_type="article",
            url=f"https://empty.example/{index}",
            title=f"Empty seed {index}",
            source="empty.example",
            status="completed",
        )
        db_session.add(content)
        db_session.flush()
        db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr(feed_discovery, "get_db", _override_get_db)
    first = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=_stub_direction_selector,
            lane_planner=_stub_lane_planner,
            candidate_extractor=lambda *_args: (_ for _ in ()).throw(
                AssertionError("empty Exa results must skip candidate extraction")
            ),
            exa_search_fn=lambda *_args: [],
            candidate_validator=lambda *_args: [],
        ),
    )

    assert first.status == "completed"
    assert (first.feeds, first.podcasts, first.youtube) == (0, 0, 0)
    unexpected = lambda *_args: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("completed empty discovery must not rebill providers")
    )
    reused = run_feed_discovery(
        user_id=test_user.id,
        deps=FeedDiscoveryDeps(
            direction_selector=unexpected,
            lane_planner=unexpected,
            candidate_extractor=unexpected,
            exa_search_fn=unexpected,
            candidate_validator=unexpected,
        ),
    )

    assert reused == first
    assert db_session.query(FeedDiscoveryRun).count() == 1


def test_persisted_discovery_marks_canonical_active_subscription(
    db_session,
    test_user,
) -> None:
    feed_url = "https://example.com/feed.xml"
    db_session.add(
        UserScraperConfig(
            user_id=test_user.id,
            scraper_type="atom",
            display_name="Subscribed Feed",
            config={"feed_url": feed_url},
            feed_url=feed_url,
            is_active=True,
        )
    )
    run = FeedDiscoveryRun(
        user_id=test_user.id,
        status="processing",
        seed_content_ids=[],
    )
    db_session.add(run)
    db_session.flush()

    records = feed_discovery._persist_suggestions(
        db_session,
        run.id,
        test_user.id,
        [
            DiscoveryCandidate(
                title="Subscribed Feed",
                site_url="https://example.com",
                feed_url=feed_url,
                suggestion_type="atom",
                rationale="Already part of the user's sources",
            )
        ],
    )

    assert len(records) == 1
    assert records[0].status == "subscribed"


def test_persisted_suggestions_roll_back_and_retry_belong_to_new_run(
    db_session,
    test_user,
) -> None:
    user_id = int(test_user.id)
    candidate = DiscoveryCandidate(
        title="Retry Feed",
        site_url="https://retry.example.com",
        feed_url="https://retry.example.com/feed.xml",
        suggestion_type="atom",
        rationale="Prove discovery persistence is atomic",
    )
    interrupted_run = FeedDiscoveryRun(
        user_id=user_id,
        status="processing",
        seed_content_ids=[],
    )
    db_session.add(interrupted_run)
    db_session.commit()
    interrupted_run_id = int(interrupted_run.id)

    pending = feed_discovery._persist_suggestions(
        db_session,
        interrupted_run_id,
        user_id,
        [candidate.model_copy(deep=True)],
    )
    assert len(pending) == 1

    # A worker exit closes and rolls back its open transaction.
    db_session.rollback()
    assert db_session.query(FeedDiscoverySuggestion).count() == 0

    retry_run = FeedDiscoveryRun(
        user_id=user_id,
        status="processing",
        seed_content_ids=[],
    )
    db_session.add(retry_run)
    db_session.commit()
    retry_run_id = int(retry_run.id)

    retried = feed_discovery._persist_suggestions(
        db_session,
        retry_run_id,
        user_id,
        [candidate.model_copy(deep=True)],
    )
    retry_run.status = "completed"
    db_session.commit()

    assert len(retried) == 1
    persisted = db_session.query(FeedDiscoverySuggestion).one()
    assert persisted.run_id == retry_run_id
    assert persisted.run_id != interrupted_run_id
    assert db_session.get(FeedDiscoveryRun, interrupted_run_id).status == "processing"
    assert db_session.get(FeedDiscoveryRun, retry_run_id).status == "completed"
