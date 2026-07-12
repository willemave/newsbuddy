"""Tests for onboarding feed backfill handler."""

from contextlib import nullcontext
from unittest.mock import Mock

from app.pipeline.handlers.backfill_feeds import BackfillFeedsHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def test_backfill_feeds_handler_runs_batch(monkeypatch) -> None:
    calls: list[tuple[int, int, int]] = []
    recorded_progress: dict[str, object] = {}

    class FakeResult:
        def __init__(self, config_id: int) -> None:
            self.config_id = config_id
            self.saved = 3
            self.scraped = 3
            self.duplicates = 0
            self.errors = 0

    def fake_backfill(request):
        calls.append((request.user_id, request.config_id, request.count))
        return FakeResult(request.config_id)

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.backfill_feed_for_config",
        fake_backfill,
    )

    def fake_mark_complete(_db, **kwargs):
        recorded_progress.update(kwargs)
        return len(kwargs["config_ids"])

    monkeypatch.setattr(
        "app.pipeline.handlers.backfill_feeds.mark_feed_sources_complete",
        fake_mark_complete,
    )

    handler = BackfillFeedsHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.BACKFILL_FEEDS,
        retry_count=0,
        payload={"user_id": 7, "config_ids": [11, 12], "count": 2},
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
    assert recorded_progress == {
        "user_id": 7,
        "config_ids": [11, 12],
        "processed_item_counts": {11: 3, 12: 3},
    }


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
