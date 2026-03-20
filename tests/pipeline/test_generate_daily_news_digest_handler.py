"""Tests for daily news digest task handler."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.pipeline.handlers.generate_daily_news_digest import GenerateDailyNewsDigestHandler
from app.pipeline.task_models import TaskEnvelope
from app.services.daily_news_digest import DailyDigestUpsertResult
from app.services.queue import TaskType


def _build_context():
    @contextmanager
    def _db_factory():
        yield Mock()

    return SimpleNamespace(
        db_factory=_db_factory,
        llm_service=Mock(),
    )


def test_handler_generates_digest_from_valid_payload() -> None:
    handler = GenerateDailyNewsDigestHandler()
    context = _build_context()
    task = TaskEnvelope(
        id=11,
        task_type=TaskType.GENERATE_DAILY_NEWS_DIGEST,
        retry_count=0,
        payload={
            "user_id": 7,
            "local_date": "2026-02-28",
            "timezone": "America/New_York",
            "coverage_end_at": "2026-02-28T06:00:00",
            "force_regenerate": True,
            "skip_if_empty": True,
        },
    )

    with patch(
        "app.pipeline.handlers.generate_daily_news_digest.upsert_daily_news_digest_for_user_day"
    ) as mock_upsert:
        mock_upsert.return_value = DailyDigestUpsertResult(
            digest_id=101,
            local_date=date(2026, 2, 28),
            source_count=8,
            created=True,
        )
        result = handler.handle(task, context)

    assert result.success is True
    assert result.error_message is None
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args.kwargs["coverage_end_at"] == datetime(2026, 2, 28, 6, 0, 0)
    assert mock_upsert.call_args.kwargs["skip_if_empty"] is True


def test_handler_rejects_invalid_user_id() -> None:
    handler = GenerateDailyNewsDigestHandler()
    context = _build_context()
    task = TaskEnvelope(
        id=12,
        task_type=TaskType.GENERATE_DAILY_NEWS_DIGEST,
        retry_count=0,
        payload={"user_id": "bad", "local_date": "2026-02-28"},
    )

    result = handler.handle(task, context)
    assert result.success is False
    assert result.retryable is False
    assert "user_id" in (result.error_message or "")


def test_handler_rejects_invalid_local_date() -> None:
    handler = GenerateDailyNewsDigestHandler()
    context = _build_context()
    task = TaskEnvelope(
        id=13,
        task_type=TaskType.GENERATE_DAILY_NEWS_DIGEST,
        retry_count=0,
        payload={"user_id": 7, "local_date": "bad-date"},
    )

    result = handler.handle(task, context)
    assert result.success is False
    assert result.retryable is False
    assert "local_date" in (result.error_message or "")
