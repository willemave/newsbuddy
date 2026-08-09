from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from app.models.contracts import TaskType
from app.models.domain.discovery import DiscoveryRunResult
from app.pipeline.handlers import discover_feeds
from app.pipeline.handlers.discover_feeds import DiscoverFeedsHandler
from app.pipeline.task_models import TaskEnvelope


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        id=1,
        task_type=TaskType.DISCOVER_FEEDS,
        payload={"user_id": 7, "trigger": "cron"},
    )


def test_discover_feeds_handler_rejects_invalid_payload_without_retry() -> None:
    result = DiscoverFeedsHandler().handle(
        TaskEnvelope(id=1, task_type=TaskType.DISCOVER_FEEDS, payload={}),
        SimpleNamespace(db_factory=lambda: nullcontext(object())),
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_message == "Missing user_id"


def test_discover_feeds_handler_does_not_report_persisted_failure_as_success(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        discover_feeds,
        "run_feed_discovery",
        lambda **_kwargs: DiscoveryRunResult(
            run_id=44,
            feeds=0,
            podcasts=0,
            youtube=0,
            status="failed",
        ),
    )
    projected: list[int] = []
    monkeypatch.setattr(
        discover_feeds,
        "ensure_weekly_discovery_session",
        lambda _db, *, user_id: projected.append(user_id),
    )

    result = DiscoverFeedsHandler().handle(
        _task(),
        SimpleNamespace(db_factory=lambda: nullcontext(object())),
    )

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "Feed discovery run 44 finished with status failed"
    assert projected == []


def test_discover_feeds_handler_projects_only_completed_run(monkeypatch) -> None:
    monkeypatch.setattr(
        discover_feeds,
        "run_feed_discovery",
        lambda **_kwargs: DiscoveryRunResult(
            run_id=45,
            feeds=2,
            podcasts=1,
            youtube=1,
            status="completed",
        ),
    )
    projected: list[int] = []
    monkeypatch.setattr(
        discover_feeds,
        "ensure_weekly_discovery_session",
        lambda _db, *, user_id: projected.append(user_id),
    )

    result = DiscoverFeedsHandler().handle(
        _task(),
        SimpleNamespace(db_factory=lambda: nullcontext(object())),
    )

    assert result.success is True
    assert projected == [7]


def test_discover_feeds_handler_retries_projection_without_rerunning_paid_discovery(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        discover_feeds,
        "run_feed_discovery",
        lambda **_kwargs: DiscoveryRunResult(
            run_id=46,
            feeds=2,
            podcasts=1,
            youtube=0,
            status="completed",
        ),
    )
    monkeypatch.setattr(
        discover_feeds,
        "ensure_weekly_discovery_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection unavailable")),
    )

    result = DiscoverFeedsHandler().handle(
        _task(),
        SimpleNamespace(db_factory=lambda: nullcontext(object())),
    )

    assert result.success is False
    assert result.retryable is True
    assert result.error_message == "projection unavailable"
