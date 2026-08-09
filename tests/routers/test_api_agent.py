"""Tests for additive machine-oriented agent endpoints."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.api.onboarding import (
    OnboardingCompleteResponse,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverResponse,
)
from app.models.db import ProcessingTask, ProcessingTaskUserAccess


def test_agent_job_status_returns_processing_task(client, db_session, test_user):
    """Job status endpoint should expose persisted queue task state."""
    task = ProcessingTask(
        task_type="process_content",
        content_id=42,
        payload={"user_id": 1},
        status="pending",
        queue_name="content",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(ProcessingTaskUserAccess(task_id=task.id, user_id=test_user.id))
    db_session.commit()

    response = client.get(f"/api/jobs/{task.id}")

    assert response.status_code == 200
    assert response.json()["id"] == task.id
    assert response.json()["status"] == "pending"
    assert response.json()["payload"] == {}


def test_agent_job_status_hides_other_users_tasks(
    client_factory,
    db_session,
    test_user,
    user_factory,
) -> None:
    task = ProcessingTask(
        task_type="analyze_url",
        payload={"user_id": test_user.id, "secret": "private"},
        error_message="provider credential leaked here",
        status="failed",
        queue_name="content",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(ProcessingTaskUserAccess(task_id=task.id, user_id=test_user.id))
    db_session.commit()
    other_user = user_factory()

    with client_factory(user=other_user) as other_client:
        response = other_client.get(f"/api/jobs/{task.id}")

    assert response.status_code == 404


def test_agent_search_returns_external_results(client, monkeypatch):
    """Agent search should wrap provider-backed external results only."""

    monkeypatch.setattr(
        "app.queries.search_external_results.exa_search",
        lambda query, num_results: [
            SimpleNamespace(
                title=f"Web {query}",
                url="https://example.com/web",
                snippet="web result",
                published_date="2026-03-08T00:00:00Z",
            )
        ],
    )
    monkeypatch.setattr(
        "app.queries.search_external_results.search_podcast_episodes",
        lambda query, limit: [
            SimpleNamespace(
                title=f"Podcast {query}",
                episode_url="https://example.com/podcast",
                snippet="podcast result",
                source="podcast-index",
                provider="podcast-index",
                feed_url="https://example.com/feed.xml",
                published_at="2026-03-07T00:00:00Z",
                score=0.9,
            )
        ],
    )

    response = client.post(
        "/api/agent/search",
        json={"query": "ai agents", "limit": 5, "include_podcasts": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert [result["kind"] for result in data["results"]] == ["web", "podcast"]


def test_agent_onboarding_routes_delegate_to_wrappers(client, monkeypatch):
    """Simplified onboarding routes should stay thin and machine-oriented."""

    async def fake_start(*_args, **_kwargs):
        return {"run_id": 11, "status": "pending", "job_id": None}

    monkeypatch.setattr("app.routers.api.agent.start_agent_onboarding.execute", fake_start)
    monkeypatch.setattr(
        "app.routers.api.agent.get_agent_onboarding_status.execute",
        lambda *_args, **_kwargs: OnboardingDiscoveryStatusResponse(
            run_id=11,
            run_status="completed",
            topic_summary="AI infra",
            inferred_topics=["ai", "infra"],
            lanes=[],
            suggestions=OnboardingFastDiscoverResponse(),
        ),
    )
    monkeypatch.setattr(
        "app.routers.api.agent.complete_agent_onboarding.execute",
        lambda *_args, **_kwargs: OnboardingCompleteResponse(
            status="completed",
            task_id=91,
            inbox_count_estimate=5,
            configured_source_count=2,
            longform_status="queued",
            has_completed_onboarding=True,
            has_completed_new_user_tutorial=True,
        ),
    )

    start_response = client.post(
        "/api/agent/onboarding",
        json={"brief": "I want AI and startup coverage"},
    )
    status_response = client.get("/api/agent/onboarding/11")
    complete_response = client.post(
        "/api/agent/onboarding/11/complete",
        json={"accept_all": True},
    )

    assert start_response.status_code == 200
    assert start_response.json()["run_id"] == 11
    assert status_response.status_code == 200
    assert status_response.json()["run_status"] == "completed"
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"
