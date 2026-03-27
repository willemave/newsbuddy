"""Tests for integration sync task handler."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from app.pipeline.handlers.sync_integration import SyncIntegrationHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _build_context() -> TaskContext:
    @contextmanager
    def _db_context():
        yield None

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=_db_context,
    )


def test_sync_integration_skips_when_feature_disabled(monkeypatch):
    """Handler should no-op when X bookmark sync is disabled."""
    monkeypatch.setattr(
        "app.pipeline.handlers.sync_integration.get_settings",
        lambda: SimpleNamespace(x_bookmark_sync_enabled=False),
    )
    sync_mock = Mock()
    monkeypatch.setattr(
        "app.pipeline.handlers.sync_integration.sync_x_sources_for_user",
        sync_mock,
    )

    handler = SyncIntegrationHandler()
    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SYNC_INTEGRATION,
        payload={"user_id": 1, "provider": "x"},
    )

    result = handler.handle(task, _build_context())

    assert result.success is True
    sync_mock.assert_not_called()


def test_sync_integration_runs_when_feature_enabled(monkeypatch):
    """Handler should invoke bookmark sync when feature flag is enabled."""
    monkeypatch.setattr(
        "app.pipeline.handlers.sync_integration.get_settings",
        lambda: SimpleNamespace(x_bookmark_sync_enabled=True),
    )

    fake_db = object()

    @contextmanager
    def _fake_get_db():
        yield fake_db

    monkeypatch.setattr("app.pipeline.handlers.sync_integration.get_db", _fake_get_db)
    sync_mock = Mock(
        return_value=SimpleNamespace(
            status="success",
            fetched=1,
            accepted=1,
            filtered_out=0,
            errored=0,
            created=1,
            reused=0,
            channels={},
        )
    )
    monkeypatch.setattr(
        "app.pipeline.handlers.sync_integration.sync_x_sources_for_user",
        sync_mock,
    )

    handler = SyncIntegrationHandler()
    task = TaskEnvelope(
        id=2,
        task_type=TaskType.SYNC_INTEGRATION,
        payload={"user_id": 42, "provider": "x"},
    )

    result = handler.handle(task, _build_context())

    assert result.success is True
    sync_mock.assert_called_once_with(fake_db, user_id=42)
