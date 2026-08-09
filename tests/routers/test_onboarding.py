from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingCompleteRequest,
)
from app.models.contracts import ContentStatus, ContentType
from app.models.db import (
    Content,
    ContentStatusEntry,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    OnboardingDiscoverySuggestion,
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    ProcessingTask,
    UserScraperConfig,
)
from app.services.feed_research_runtime import (
    FeedResearchDeadlineExceeded,
    FeedResearchRuntimeError,
)
from app.services.onboarding.entrypoints import complete_onboarding, start_audio_discovery
from app.services.queue import TaskType


@pytest.mark.usefixtures("stub_valid_feed_url")
def test_onboarding_complete_creates_configs(client, db_session, monkeypatch, test_user) -> None:
    calls: list[tuple[str, dict]] = []
    commit_count = 0
    queue_call_commit_counts: list[int] = []
    original_commit = db_session.commit

    def tracking_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit()

    monkeypatch.setattr(db_session, "commit", tracking_commit)

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            queue_call_commit_counts.append(commit_count)
            calls.extend((request.task_type.value, request.payload or {}) for request in requests)
            return [42] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "substack",
                    "title": "Example Substack",
                    "feed_url": "https://example.substack.com/feed",
                },
                {
                    "suggestion_type": "podcast_rss",
                    "title": "Example Podcast",
                    "feed_url": "https://feed.podbean.com/arthistoryhour/feed.xml",
                },
            ],
            "selected_subreddits": ["MachineLearning"],
            "profile_summary": "AI researcher and writer",
            "inferred_topics": ["AI", "ML"],
            "twitter_username": "@willem_aw",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["task_id"] == 42
    assert data["configured_source_count"] == 3
    assert data["has_completed_onboarding"] is True

    configs = (
        db_session.query(UserScraperConfig).filter(UserScraperConfig.user_id == test_user.id).all()
    )
    assert len(configs) == 3
    assert any(config.scraper_type == "substack" for config in configs)
    assert any(config.scraper_type == "podcast_rss" for config in configs)
    assert any(config.scraper_type == "reddit" for config in configs)

    backfill_calls = [call for call in calls if call[0] == TaskType.BACKFILL_FEEDS.value]
    assert len(backfill_calls) == 1
    assert backfill_calls[0][1]["user_id"] == test_user.id
    assert len(backfill_calls[0][1]["config_ids"]) == 2
    assert backfill_calls[0][1]["count"] == 2
    assert any(call[0] == TaskType.SCRAPE.value for call in calls)
    assert any(call[0] == TaskType.ONBOARDING_DISCOVER.value for call in calls)
    feed_discovery_calls = [call for call in calls if call[0] == TaskType.DISCOVER_FEEDS.value]
    assert feed_discovery_calls == [
        (
            TaskType.DISCOVER_FEEDS.value,
            {"user_id": test_user.id, "trigger": "onboarding"},
        )
    ]
    assert queue_call_commit_counts
    assert set(queue_call_commit_counts) == {0}
    assert commit_count == 1
    db_session.refresh(test_user)
    assert test_user.twitter_username == "willem_aw"
    assert test_user.has_completed_onboarding is True
    assert test_user.reading_experience == "briefing"
    first_run = (
        db_session.query(OnboardingFirstEditionRun)
        .filter(OnboardingFirstEditionRun.user_id == test_user.id)
        .one()
    )
    assert first_run.status == "active"
    assert backfill_calls[0][1]["first_edition_run_id"] == first_run.id
    scrape_call = next(call for call in calls if call[0] == TaskType.SCRAPE.value)
    assert scrape_call[1]["first_edition_run_id"] == first_run.id
    first_run_sources = (
        db_session.query(OnboardingFirstEditionSource)
        .filter(OnboardingFirstEditionSource.run_id == first_run.id)
        .all()
    )
    assert {source.display_name for source in first_run_sources} == {
        "Example Substack",
        "Example Podcast",
        "r/MachineLearning",
    }


@pytest.mark.usefixtures("stub_valid_feed_url")
def test_onboarding_complete_rolls_back_checkpoint_when_queue_batch_fails(
    db_session,
    monkeypatch,
    test_user,
) -> None:
    queued_types: list[TaskType] = []

    class FailingQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            queued_types.extend(request.task_type for request in requests)
            raise RuntimeError("queue insert failed")

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FailingQueueGateway(),
    )

    request = OnboardingCompleteRequest.model_validate(
        {
            "selected_sources": [
                {
                    "suggestion_type": "substack",
                    "title": "Atomic Feed",
                    "feed_url": "https://atomic.example/feed",
                }
            ],
            "selected_subreddits": ["MachineLearning"],
            "profile_summary": "AI infrastructure",
            "inferred_topics": ["AI"],
        }
    )

    with pytest.raises(RuntimeError, match="queue insert failed"):
        complete_onboarding(db_session, test_user.id, request)

    assert set(queued_types) == {
        TaskType.BACKFILL_FEEDS,
        TaskType.SCRAPE,
        TaskType.ONBOARDING_DISCOVER,
        TaskType.DISCOVER_FEEDS,
    }
    db_session.rollback()

    persisted_user = db_session.get(type(test_user), test_user.id)
    assert persisted_user is not None
    assert persisted_user.has_completed_onboarding is False
    assert (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .count()
        == 0
    )
    assert (
        db_session.query(OnboardingFirstEditionRun)
        .filter(OnboardingFirstEditionRun.user_id == test_user.id)
        .count()
        == 0
    )
    assert db_session.query(ProcessingTask).count() == 0


@pytest.mark.asyncio
async def test_audio_discovery_rolls_back_run_when_queue_batch_fails(
    db_session,
    monkeypatch,
    test_user,
) -> None:
    async def fake_build_audio_lane_plan(_transcript, _locale):
        return SimpleNamespace(
            topic_summary="AI and robotics",
            inferred_topics=["AI", "robotics"],
            lanes=[
                SimpleNamespace(
                    name="Newsletters",
                    goal="Find newsletters.",
                    target="feeds",
                    queries=["AI newsletters"],
                )
            ],
        )

    class FailingQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            assert [request.task_type for request in requests] == [TaskType.ONBOARDING_DISCOVER]
            raise RuntimeError("queue insert failed")

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints._build_audio_lane_plan",
        fake_build_audio_lane_plan,
    )
    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FailingQueueGateway(),
    )

    with pytest.raises(RuntimeError, match="queue insert failed"):
        await start_audio_discovery(
            db_session,
            test_user.id,
            OnboardingAudioDiscoverRequest(transcript="AI and robotics", locale="en-US"),
        )

    db_session.rollback()
    assert (
        db_session.query(OnboardingDiscoveryRun)
        .filter(OnboardingDiscoveryRun.user_id == test_user.id)
        .count()
        == 0
    )
    assert db_session.query(OnboardingDiscoveryLane).count() == 0
    assert db_session.query(ProcessingTask).count() == 0


def test_onboarding_complete_does_not_enqueue_duplicate_feed_discovery(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    db_session.add(
        ProcessingTask(
            owner_user_id=test_user.id,
            task_type=TaskType.DISCOVER_FEEDS.value,
            payload={"user_id": test_user.id, "trigger": "onboarding"},
            status="completed",
            queue_name="content",
        )
    )
    db_session.commit()
    calls: list[TaskType] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend(request.task_type for request in requests)
            return [42] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post("/api/onboarding/complete", json={})

    assert response.status_code == 200
    assert TaskType.DISCOVER_FEEDS not in calls


def test_onboarding_complete_replaces_failed_feed_discovery_task(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    db_session.add(
        ProcessingTask(
            owner_user_id=test_user.id,
            task_type=TaskType.DISCOVER_FEEDS.value,
            payload={"user_id": test_user.id, "trigger": "onboarding"},
            status="failed",
            queue_name="content",
        )
    )
    db_session.commit()
    calls: list[TaskType] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend(request.task_type for request in requests)
            return [42] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post("/api/onboarding/complete", json={})

    assert response.status_code == 200
    assert TaskType.DISCOVER_FEEDS in calls


def test_onboarding_complete_rejects_invalid_selected_feed_atomically(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    def fake_validate(_scraper_type, config, **_kwargs):
        if config["feed_url"].endswith("not-a-feed"):
            raise ValueError("not an RSS/Atom feed")
        return config

    monkeypatch.setattr(
        "app.services.scraper_configs.validate_and_normalize_scraper_config",
        fake_validate,
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "atom",
                    "title": "Initially valid feed",
                    "feed_url": "https://example.com/feed.xml",
                },
                {
                    "suggestion_type": "atom",
                    "title": "Invalid feed",
                    "feed_url": "https://example.com/not-a-feed",
                },
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "not an RSS/Atom feed"
    db_session.expire_all()
    assert db_session.query(UserScraperConfig).filter_by(user_id=test_user.id).count() == 0
    assert db_session.get(type(test_user), test_user.id).has_completed_onboarding is False


def test_onboarding_complete_reports_feed_sandbox_outage_and_rolls_back(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    def fake_validate(_scraper_type, config, **_kwargs):
        if config["feed_url"].endswith("unavailable.xml"):
            raise FeedResearchRuntimeError("E2B unavailable")
        return config

    monkeypatch.setattr(
        "app.services.scraper_configs.validate_and_normalize_scraper_config",
        fake_validate,
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "atom",
                    "title": "Initially valid feed",
                    "feed_url": "https://example.com/feed.xml",
                },
                {
                    "suggestion_type": "atom",
                    "title": "Unavailable feed",
                    "feed_url": "https://example.com/unavailable.xml",
                },
            ]
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Feed validation is temporarily unavailable"
    db_session.expire_all()
    assert db_session.query(UserScraperConfig).filter_by(user_id=test_user.id).count() == 0
    assert db_session.get(type(test_user), test_user.id).has_completed_onboarding is False


@pytest.mark.usefixtures("stub_valid_feed_url")
def test_onboarding_complete_persists_selected_feed_reddit_and_aggregator(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend((request.task_type.value, request.payload or {}) for request in requests)
            return [45] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "atom",
                    "title": "Generated Dog Tech Feed",
                    "feed_url": "https://example.com/dog-tech.xml",
                }
            ],
            "selected_subreddits": ["dogs"],
            "selected_aggregators": [
                {"key": "brutalist", "title": "Brutalist Report", "topics": ["science"]}
            ],
            "profile_summary": "Interested in technology and dogs",
            "inferred_topics": ["technology", "dogs"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["configured_source_count"] == 3

    configs = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .order_by(UserScraperConfig.id.asc())
        .all()
    )
    atom_config = next(config for config in configs if config.scraper_type == "atom")
    reddit_config = next(config for config in configs if config.scraper_type == "reddit")
    aggregator_config = next(config for config in configs if config.scraper_type == "aggregator")

    assert atom_config.display_name == "Generated Dog Tech Feed"
    assert atom_config.config["feed_url"] == "https://example.com/dog-tech.xml"
    assert reddit_config.config["subreddit"] == "dogs"
    assert aggregator_config.config["key"] == "brutalist"
    assert aggregator_config.config["topics"] == ["science"]

    backfill_calls = [
        payload for task_type, payload in calls if task_type == TaskType.BACKFILL_FEEDS.value
    ]
    assert len(backfill_calls) == 1
    assert backfill_calls[0]["config_ids"] == [atom_config.id]

    scrape_calls = [payload for task_type, payload in calls if task_type == TaskType.SCRAPE.value]
    assert scrape_calls
    assert set(scrape_calls[0]["sources"]) == {"Reddit", "brutalist"}

    discovery_calls = [
        payload for task_type, payload in calls if task_type == TaskType.ONBOARDING_DISCOVER.value
    ]
    assert discovery_calls
    assert discovery_calls[0]["profile_summary"] == "Interested in technology and dogs"
    assert discovery_calls[0]["inferred_topics"] == ["technology", "dogs"]


def test_onboarding_complete_queues_selected_aggregator_scrapes(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend((request.task_type.value, request.payload or {}) for request in requests)
            return [43] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_aggregators": [
                {"key": "sciurls", "title": "SciURLs"},
                {"key": "finurls", "title": "FinURLs"},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["configured_source_count"] == 2

    configs = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .filter(UserScraperConfig.scraper_type == "aggregator")
        .all()
    )
    assert {config.config["key"] for config in configs} == {"sciurls", "finurls"}
    scrape_payload = next(
        payload for task_type, payload in calls if task_type == TaskType.SCRAPE.value
    )
    assert scrape_payload["sources"] == ["sciurls", "finurls"]
    assert isinstance(scrape_payload["first_edition_run_id"], int)


def test_onboarding_complete_ignores_reddit_aggregator(
    client,
    db_session,
    monkeypatch,
    test_user,
) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend((request.task_type.value, request.payload or {}) for request in requests)
            return [44] * len(requests)

    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_aggregators": [
                {"key": "reddit", "title": "Reddit"},
                {"key": "sciurls", "title": "SciURLs"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["configured_source_count"] == 1

    configs = (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .filter(UserScraperConfig.scraper_type == "aggregator")
        .all()
    )
    assert [config.config["key"] for config in configs] == ["sciurls"]
    scrape_payload = next(
        payload for task_type, payload in calls if task_type == TaskType.SCRAPE.value
    )
    assert scrape_payload["sources"] == ["sciurls"]
    assert isinstance(scrape_payload["first_edition_run_id"], int)


def test_onboarding_complete_rejects_invalid_twitter_username(client):
    response = client.post(
        "/api/onboarding/complete",
        json={"twitter_username": "bad username!"},
    )
    assert response.status_code == 400
    assert "Twitter username" in response.json()["detail"]


def test_onboarding_tutorial_complete(client, db_session, test_user):
    run = OnboardingFirstEditionRun(
        user_id=test_user.id,
        status="active",
        revision=1,
    )
    db_session.add(run)
    db_session.commit()
    response = client.post("/api/onboarding/tutorial-complete")
    assert response.status_code == 200
    assert response.json()["has_completed_new_user_tutorial"] is True
    db_session.refresh(run)
    assert run.status == "completed"

    db_session.refresh(test_user)
    assert test_user.has_completed_new_user_tutorial is True


def test_onboarding_fast_discover_returns_empty_without_search_results(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discovery_exa_queries",
        lambda *_args, **_kwargs: [],
    )
    response = client.post(
        "/api/onboarding/fast-discover",
        json={"profile_summary": "AI engineer", "inferred_topics": ["AI", "ML"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "recommended_substacks" in data
    assert "recommended_pods" in data
    assert "recommended_subreddits" in data
    assert data["recommended_substacks"] == []
    assert data["recommended_pods"] == []
    assert data["recommended_subreddits"] == []


def test_onboarding_fast_discover_does_not_use_static_defaults(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discovery_exa_queries",
        lambda *_args, **_kwargs: [],
    )

    response = client.post(
        "/api/onboarding/fast-discover",
        json={"profile_summary": "AI engineer", "inferred_topics": ["AI", "ML"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recommended_substacks"] == []
    assert data["recommended_pods"] == []
    assert data["recommended_subreddits"] == []


@pytest.mark.parametrize(
    "error_type",
    [FeedResearchRuntimeError, FeedResearchDeadlineExceeded],
)
def test_onboarding_fast_discover_reports_feed_sandbox_outage(
    client,
    monkeypatch,
    error_type,
):
    def _unavailable(*_args, **_kwargs):
        raise error_type("E2B unavailable")

    monkeypatch.setattr("app.routers.api.onboarding.fast_discover", _unavailable)

    response = client.post(
        "/api/onboarding/fast-discover",
        json={"profile_summary": "AI engineer", "inferred_topics": ["AI"]},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Feed discovery is temporarily unavailable"


def test_onboarding_profile_requires_interests(client, monkeypatch):
    def fake_build_profile(_payload):
        return {
            "profile_summary": "Summary",
            "inferred_topics": ["AI"],
            "candidate_sources": [],
        }

    monkeypatch.setattr("app.routers.api.onboarding.build_onboarding_profile", fake_build_profile)

    response = client.post(
        "/api/onboarding/profile",
        json={"first_name": "Ada", "interest_topics": []},
    )
    assert response.status_code == 422


def test_onboarding_parse_voice(client, monkeypatch):
    def fake_get_basic_agent(_model, output_cls, _system_prompt):
        class FakeAgent:
            def run_sync(self, _prompt, model_settings=None):
                return SimpleNamespace(
                    data=output_cls(
                        first_name="Ada",
                        interest_topics=["AI", "AI", " climate tech "],
                        confidence=0.92,
                    )
                )

        return FakeAgent()

    monkeypatch.setattr("app.services.onboarding.llm_plans.get_basic_agent", fake_get_basic_agent)

    response = client.post(
        "/api/onboarding/parse-voice",
        json={"transcript": "I'm Ada and I like AI and climate tech."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["first_name"] == "Ada"
    assert data["interest_topics"] == ["AI", "climate tech"]
    assert data["missing_fields"] == []


def test_onboarding_audio_discover_creates_run(client, db_session, monkeypatch, test_user) -> None:
    def fake_get_basic_agent(_model, _output_cls, _system_prompt):
        class FakeAgent:
            async def run(self, _prompt, model_settings=None):
                return SimpleNamespace(
                    data=SimpleNamespace(
                        topic_summary="AI and robotics",
                        inferred_topics=["AI", "robotics"],
                        lanes=[
                            SimpleNamespace(
                                name="Newsletters",
                                goal="Find newsletters.",
                                target="feeds",
                                queries=["AI newsletter", "robotics RSS"],
                            ),
                            SimpleNamespace(
                                name="Podcasts",
                                goal="Find podcasts.",
                                target="podcasts",
                                queries=["AI podcast", "robotics podcast"],
                            ),
                            SimpleNamespace(
                                name="Reddit",
                                goal="Find subreddits.",
                                target="reddit",
                                queries=["AI subreddit", "robotics subreddit"],
                            ),
                        ],
                    )
                )

        return FakeAgent()

    calls: list[dict] = []

    class FakeQueueGateway:
        def enqueue_many_in_session(self, db, requests):
            assert db is db_session
            calls.extend(request.payload or {} for request in requests)
            return [99] * len(requests)

    monkeypatch.setattr("app.services.onboarding.llm_plans.get_basic_agent", fake_get_basic_agent)
    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: FakeQueueGateway(),
    )

    response = client.post(
        "/api/onboarding/audio-discover",
        json={"transcript": "I want AI and robotics updates.", "locale": "en-US"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] > 0
    assert len(data["lanes"]) == 3
    assert calls and calls[0].get("run_id") == data["run_id"]

    run = (
        db_session.query(OnboardingDiscoveryRun)
        .filter(OnboardingDiscoveryRun.user_id == test_user.id)
        .first()
    )
    assert run is not None
    lanes = (
        db_session.query(OnboardingDiscoveryLane)
        .filter(OnboardingDiscoveryLane.run_id == run.id)
        .all()
    )
    assert len(lanes) == 3


def test_onboarding_discovery_status_returns_suggestions(client, db_session, test_user):
    run = OnboardingDiscoveryRun(
        user_id=test_user.id,
        status="completed",
        topic_summary="AI topics",
        inferred_topics=["AI"],
    )
    db_session.add(run)
    db_session.flush()

    db_session.add(
        OnboardingDiscoveryLane(
            run_id=run.id,
            lane_name="Newsletters",
            goal="Find feeds.",
            target="feeds",
            status="completed",
            query_count=2,
            completed_queries=2,
            queries=["AI newsletter", "AI RSS"],
        )
    )
    db_session.add(
        OnboardingDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="podcast_rss",
            site_url="https://example.com",
            feed_url="https://example.com/rss.xml",
            title="AI Podcast",
            rationale="Strong coverage.",
            score=0.9,
            status="new",
        )
    )
    db_session.add(
        OnboardingDiscoverySuggestion(
            run_id=run.id,
            user_id=test_user.id,
            suggestion_type="reddit",
            site_url="https://reddit.com/r/MachineLearning",
            subreddit="MachineLearning",
            title="MachineLearning",
            rationale="Active community.",
            score=0.8,
            status="new",
        )
    )
    db_session.commit()

    response = client.get(f"/api/onboarding/discovery-status?run_id={run.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["run_status"] == "completed"
    assert data["suggestions"]["recommended_pods"][0]["feed_url"] == "https://example.com/rss.xml"
    assert data["suggestions"]["recommended_subreddits"][0]["subreddit"] == "MachineLearning"


def test_onboarding_complete_seeds_news(client, db_session, monkeypatch, test_user):
    news_items = [
        Content(
            url=f"https://example.com/news-{idx}",
            content_type=ContentType.NEWS.value,
            status=ContentStatus.COMPLETED.value,
            content_metadata={},
        )
        for idx in range(3)
    ]
    db_session.add_all(news_items)
    db_session.commit()

    response = client.post("/api/onboarding/complete", json={})
    assert response.status_code == 200

    seeded = (
        db_session.query(ContentStatusEntry)
        .filter(ContentStatusEntry.user_id == test_user.id)
        .filter(ContentStatusEntry.status == "inbox")
        .all()
    )
    assert {entry.content_id for entry in seeded} >= {item.id for item in news_items}


@pytest.mark.usefixtures("stub_valid_feed_url")
def test_onboarding_complete_seeds_selected_feed_content(
    client,
    db_session,
    monkeypatch,
    test_user,
):
    selected_feed_url = "https://example.substack.com/feed"
    matching_items = [
        Content(
            url="https://example.substack.com/p/article",
            content_type=ContentType.ARTICLE.value,
            status=ContentStatus.COMPLETED.value,
            content_metadata={"feed_url": selected_feed_url},
        ),
        Content(
            url="https://example.substack.com/p/podcast",
            content_type=ContentType.PODCAST.value,
            status=ContentStatus.COMPLETED.value,
            content_metadata={"feed_url": selected_feed_url},
        ),
    ]
    non_matching_item = Content(
        url="https://other.example.com/p/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={"feed_url": "https://other.example.com/feed"},
    )
    db_session.add_all([*matching_items, non_matching_item])
    db_session.commit()

    response = client.post(
        "/api/onboarding/complete",
        json={
            "selected_sources": [
                {
                    "suggestion_type": "substack",
                    "title": "Example Substack",
                    "feed_url": selected_feed_url,
                }
            ]
        },
    )
    assert response.status_code == 200

    seeded_ids = {
        entry.content_id
        for entry in db_session.query(ContentStatusEntry)
        .filter(ContentStatusEntry.user_id == test_user.id)
        .filter(ContentStatusEntry.status == "inbox")
        .all()
    }
    assert {item.id for item in matching_items} <= seeded_ids
    assert non_matching_item.id not in seeded_ids
