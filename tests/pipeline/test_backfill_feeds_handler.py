"""Tests for onboarding feed backfill handler."""

from contextlib import nullcontext
from typing import cast
from unittest.mock import Mock

from app.models.contracts import BriefingFirstRunSourceOutcome
from app.pipeline.handlers.backfill_feeds import BackfillFeedsHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def test_backfill_feeds_handler_runs_batch(monkeypatch) -> None:
    calls: list[tuple[int, int, int]] = []
    recorded_progress: list[dict[str, object]] = []

    def fake_backfill(request):
        calls.append((request.user_id, request.config_id, request.count))
        return Mock(saved=3, scraped=3, duplicates=0, errors=0)

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.backfill_feed_for_config",
        fake_backfill,
    )

    def fake_record_result(_db, **kwargs):
        recorded_progress.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.record_feed_source_result",
        fake_record_result,
    )

    handler = BackfillFeedsHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={
            "user_id": 7,
            "config_ids": [11, 12],
            "count": 2,
            "first_edition_run_id": 99,
        },
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )

    result = handler.handle(task, context)

    assert result.success is True
    assert sorted(calls) == [(7, 11, 2), (7, 12, 2)]
    assert sorted(recorded_progress, key=lambda value: cast(int, value["config_id"])) == [
        {
            "run_id": 99,
            "config_id": 11,
            "processed_item_count": 3,
            "outcome": BriefingFirstRunSourceOutcome.PROCESSED,
        },
        {
            "run_id": 99,
            "config_id": 12,
            "processed_item_count": 3,
            "outcome": BriefingFirstRunSourceOutcome.PROCESSED,
        },
    ]


def test_backfill_feeds_handler_records_partial_failure_as_terminal(monkeypatch) -> None:
    recorded_progress: list[dict[str, object]] = []

    def fake_backfill(request):
        if request.config_id == 12:
            raise RuntimeError("feed unavailable")
        result = Mock(saved=2, scraped=2, duplicates=0, errors=0)
        return result

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.backfill_feed_for_config",
        fake_backfill,
    )

    def fake_record_result(_db, **kwargs):
        recorded_progress.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.record_feed_source_result",
        fake_record_result,
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=2,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={
            "user_id": 7,
            "config_ids": [11, 12],
            "count": 2,
            "first_edition_run_id": 99,
        },
    )

    result = BackfillFeedsHandler().handle(task, context)

    assert result.success is True
    outcomes = {cast(int, value["config_id"]): value["outcome"] for value in recorded_progress}
    assert outcomes == {
        11: BriefingFirstRunSourceOutcome.PROCESSED,
        12: BriefingFirstRunSourceOutcome.UNAVAILABLE,
    }


def test_backfill_feeds_handler_retries_when_progress_cannot_be_recorded(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.backfill_feed_for_config",
        lambda _request: Mock(saved=2, scraped=2, duplicates=0, errors=0),
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.record_feed_source_result",
        Mock(side_effect=RuntimeError("database unavailable")),
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=3,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={
            "user_id": 7,
            "config_ids": [11],
            "count": 2,
            "first_edition_run_id": 99,
        },
    )

    result = BackfillFeedsHandler().handle(task, context)

    assert result.success is False


def test_backfill_feeds_handler_fails_when_e2b_outage_returns_only_errors(monkeypatch) -> None:
    recorded_progress: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.backfill_feed_for_config",
        lambda _request: Mock(saved=0, scraped=0, duplicates=0, errors=1),
    )

    def fake_record_result(_db, **kwargs):
        recorded_progress.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.record_feed_source_result",
        fake_record_result,
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
        db_factory=lambda: nullcontext(Mock()),
    )
    task = TaskEnvelope(
        id=4,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={
            "user_id": 7,
            "config_ids": [11],
            "count": 2,
            "first_edition_run_id": 99,
        },
    )

    result = BackfillFeedsHandler().handle(task, context)

    assert result.success is False
    assert recorded_progress == [
        {
            "run_id": 99,
            "config_id": 11,
            "processed_item_count": 0,
            "outcome": BriefingFirstRunSourceOutcome.UNAVAILABLE,
        }
    ]


def test_backfill_feeds_handler_rejects_invalid_payload() -> None:
    handler = BackfillFeedsHandler()
    task = TaskEnvelope(
        id=2,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={"user_id": 0, "config_ids": [], "count": 2},
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test",
    )

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is False
