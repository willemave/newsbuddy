from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.contracts import TaskType
from app.pipeline.handlers import agent_data_sync
from app.pipeline.handlers.agent_data_sync import SyncAgentDataHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.agent_data_sync import AgentDataSyncResult


def _context(db: Mock) -> TaskContext:
    @contextmanager
    def db_factory():
        yield db

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=None,
        worker_id="test-worker",
        db_factory=db_factory,
    )


def test_incremental_sync_skips_index_when_manifest_revision_is_current(monkeypatch) -> None:
    db = Mock()
    enqueue_index = Mock()
    monkeypatch.setattr(
        agent_data_sync,
        "sync_agent_data_for_user",
        Mock(return_value=AgentDataSyncResult(7, 12, (), ())),
    )
    monkeypatch.setattr(
        agent_data_sync,
        "read_agent_data_manifest",
        Mock(return_value={"revision": 12, "complete": True}),
    )
    monkeypatch.setattr(agent_data_sync, "enqueue_agent_data_index", enqueue_index)

    result = SyncAgentDataHandler().handle(
        TaskEnvelope(id=1, task_type=TaskType.SYNC_AGENT_DATA, payload={"user_id": 7}),
        _context(db),
    )

    assert result.success is True
    enqueue_index.assert_not_called()
    db.commit.assert_called_once_with()


def test_incremental_sync_indexes_when_manifest_revision_is_stale(monkeypatch) -> None:
    db = Mock()
    enqueue_index = Mock()
    monkeypatch.setattr(
        agent_data_sync,
        "sync_agent_data_for_user",
        Mock(return_value=AgentDataSyncResult(7, 13, ("knowledge/new.md",), ())),
    )
    monkeypatch.setattr(
        agent_data_sync,
        "read_agent_data_manifest",
        Mock(return_value={"revision": 12, "complete": True}),
    )
    monkeypatch.setattr(agent_data_sync, "enqueue_agent_data_index", enqueue_index)

    result = SyncAgentDataHandler().handle(
        TaskEnvelope(id=2, task_type=TaskType.SYNC_AGENT_DATA, payload={"user_id": 7}),
        _context(db),
    )

    assert result.success is True
    enqueue_index.assert_called_once_with(db, user_id=7)
