"""Tests for truthful onboarding discovery queue outcomes."""

from unittest.mock import Mock

from app.models.db import OnboardingDiscoveryLane, OnboardingDiscoveryRun
from app.pipeline.handlers import onboarding_discover
from app.pipeline.handlers.onboarding_discover import OnboardingDiscoverHandler
from app.pipeline.task_models import TaskEnvelope
from app.services.onboarding.config import FAST_DISCOVER_TIMEOUT_SECONDS
from app.services.onboarding.discovery_types import OnboardingDiscoveryExecutionResult
from app.services.queue import TaskType
from tests.support.feed_subscription_test_helpers import build_task_context


def test_audio_discovery_handler_reports_persisted_failure(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        onboarding_discover,
        "run_audio_discovery",
        lambda _db, _run_id, *, user_id: OnboardingDiscoveryExecutionResult(
            success=False,
            error_message="provider failed",
        ),
    )
    context = build_task_context(db_session, queue_gateway=Mock())

    result = OnboardingDiscoverHandler().handle(
        TaskEnvelope(
            id=1,
            task_type=TaskType.ONBOARDING_DISCOVER,
            payload={"user_id": 42, "run_id": 7},
        ),
        context,
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_message == "provider failed"


def test_enrich_discovery_handler_reports_service_failure(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        onboarding_discover,
        "run_discover_enrich",
        lambda *_args, **_kwargs: OnboardingDiscoveryExecutionResult(
            success=False,
            error_message="llm unavailable",
        ),
    )
    context = build_task_context(db_session, queue_gateway=Mock())

    result = OnboardingDiscoverHandler().handle(
        TaskEnvelope(
            id=2,
            task_type=TaskType.ONBOARDING_DISCOVER,
            payload={"user_id": 42, "profile_summary": "AI"},
        ),
        context,
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_message == "llm unavailable"


def test_enrich_discovery_handler_reprojects_weekly_session(
    db_session,
    monkeypatch,
) -> None:
    """Successful enrichment must refresh a weekly seed created by the parallel task."""
    monkeypatch.setattr(
        onboarding_discover,
        "run_discover_enrich",
        lambda *_args, **_kwargs: OnboardingDiscoveryExecutionResult(
            success=True,
            run_id=91,
        ),
    )
    projected_user_ids: list[int] = []
    monkeypatch.setattr(
        onboarding_discover,
        "ensure_weekly_discovery_session",
        lambda _db, *, user_id: projected_user_ids.append(user_id),
    )
    context = build_task_context(db_session, queue_gateway=Mock())

    result = OnboardingDiscoverHandler().handle(
        TaskEnvelope(
            id=3,
            task_type=TaskType.ONBOARDING_DISCOVER,
            payload={"user_id": 42, "profile_summary": "AI"},
        ),
        context,
    )

    assert result.success is True
    assert projected_user_ids == [42]


def test_audio_discovery_service_persists_and_returns_failure(
    db_session,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fail_search(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("exa unavailable")

    run = OnboardingDiscoveryRun(
        user_id=42,
        status="pending",
        topic_summary="AI",
        inferred_topics=["AI"],
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        OnboardingDiscoveryLane(
            run_id=run.id,
            lane_name="Feeds",
            target="feeds",
            queries=["AI feeds"],
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.onboarding.audio_discovery_run._run_discovery_exa_queries",
        fail_search,
    )

    result = onboarding_discover.run_audio_discovery(
        db_session,
        int(run.id),
        user_id=42,
    )

    db_session.refresh(run)
    lane = db_session.query(OnboardingDiscoveryLane).filter_by(run_id=run.id).one()
    assert result == OnboardingDiscoveryExecutionResult(
        success=False,
        error_message="exa unavailable",
    )
    assert run.status == "failed"
    assert run.error_message == "exa unavailable"
    assert lane.status == "failed"
    assert captured["request_timeout_seconds"] == FAST_DISCOVER_TIMEOUT_SECONDS


def test_audio_discovery_handler_rejects_run_owned_by_another_user(
    db_session,
) -> None:
    run = OnboardingDiscoveryRun(
        user_id=42,
        status="pending",
        topic_summary="AI",
        inferred_topics=["AI"],
    )
    db_session.add(run)
    db_session.commit()
    context = build_task_context(db_session, queue_gateway=Mock())

    result = OnboardingDiscoverHandler().handle(
        TaskEnvelope(
            id=4,
            task_type=TaskType.ONBOARDING_DISCOVER,
            payload={"user_id": 43, "run_id": int(run.id)},
        ),
        context,
    )

    db_session.refresh(run)
    assert result.success is False
    assert result.retryable is False
    assert result.error_message == "Discovery run not found"
    assert run.status == "pending"


def test_audio_discovery_handler_retries_unexpected_value_error(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        onboarding_discover,
        "run_audio_discovery",
        Mock(side_effect=ValueError("unexpected persistence failure")),
    )
    context = build_task_context(db_session, queue_gateway=Mock())

    result = OnboardingDiscoverHandler().handle(
        TaskEnvelope(
            id=5,
            task_type=TaskType.ONBOARDING_DISCOVER,
            payload={"user_id": 42, "run_id": 7},
        ),
        context,
    )

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "unexpected persistence failure"
