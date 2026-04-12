from __future__ import annotations

from types import SimpleNamespace

from app.models.api.common import OnboardingSuggestion
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import (
    Content,
    ContentStatusEntry,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    OnboardingDiscoverySuggestion,
    UserScraperConfig,
)
from app.services.queue import TaskType


def test_onboarding_complete_creates_configs(client, db_session, monkeypatch, test_user) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeQueueGateway:
        def enqueue(self, task_type, content_id=None, payload=None, queue_name=None, dedupe=None):
            del content_id, queue_name, dedupe
            calls.append((task_type.value, payload or {}))
            return 42

    monkeypatch.setattr(
        "app.services.onboarding.get_task_queue_gateway",
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
            "news_list_preference_prompt": (
                "Prefer semiconductors, AI infrastructure, and product launches."
            ),
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
    db_session.refresh(test_user)
    assert test_user.twitter_username == "willem_aw"
    assert test_user.news_list_preference_prompt.startswith("Prefer semiconductors")
    assert test_user.has_completed_onboarding is True


def test_onboarding_complete_rejects_invalid_twitter_username(client):
    response = client.post(
        "/api/onboarding/complete",
        json={"twitter_username": "bad username!"},
    )
    assert response.status_code == 400
    assert "Twitter username" in response.json()["detail"]


def test_onboarding_complete_blank_prompt_resets_to_default(client, db_session, test_user):
    test_user.news_list_preference_prompt = "Only include chip foundry updates."
    db_session.commit()

    response = client.post(
        "/api/onboarding/complete",
        json={"news_list_preference_prompt": "   "},
    )

    assert response.status_code == 200

    db_session.refresh(test_user)
    assert test_user.news_list_preference_prompt is None


def test_onboarding_tutorial_complete(client, db_session, test_user):
    response = client.post("/api/onboarding/tutorial-complete")
    assert response.status_code == 200
    assert response.json()["has_completed_new_user_tutorial"] is True

    db_session.refresh(test_user)
    assert test_user.has_completed_new_user_tutorial is True


def test_onboarding_fast_discover_defaults(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.onboarding._run_discovery_exa_queries",
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


def test_onboarding_fast_discover_defaults_have_rationale(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.onboarding._run_discovery_exa_queries",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.onboarding._load_curated_defaults",
        lambda: {
            "substack": [
                OnboardingSuggestion(
                    suggestion_type="substack",
                    title="Example Feed",
                    feed_url="https://example.com/feed.xml",
                    site_url="https://example.com",
                    is_default=True,
                )
            ],
            "atom": [],
            "podcast_rss": [],
            "reddit": [],
        },
    )

    response = client.post(
        "/api/onboarding/fast-discover",
        json={"profile_summary": "AI engineer", "inferred_topics": ["AI", "ML"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recommended_substacks"]
    assert data["recommended_substacks"][0]["rationale"]


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

    monkeypatch.setattr("app.services.onboarding.get_basic_agent", fake_get_basic_agent)

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
        def enqueue(self, task_type, content_id=None, payload=None, queue_name=None, dedupe=None):
            del task_type, content_id, queue_name, dedupe
            calls.append(payload or {})
            return 99

    monkeypatch.setattr("app.services.onboarding.get_basic_agent", fake_get_basic_agent)
    monkeypatch.setattr(
        "app.services.onboarding.get_task_queue_gateway",
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
    monkeypatch.setattr("app.services.onboarding._load_curated_defaults", lambda: {})

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


def test_onboarding_complete_seeds_selected_feed_content(
    client,
    db_session,
    monkeypatch,
    test_user,
):
    monkeypatch.setattr("app.services.onboarding._load_curated_defaults", lambda: {})

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
