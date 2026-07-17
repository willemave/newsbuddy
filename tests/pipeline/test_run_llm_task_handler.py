from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from app.models.contracts import LlmTaskKind, LlmTaskMode, TaskType
from app.pipeline.handlers.run_llm_task import RunLlmTaskHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.learning_deck_generation import LearningDeckGenerationWaiting
from app.services.llm_tasks import create_llm_task


def test_run_llm_task_handler_dispatches_share_action(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    llm_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_CONTENT,
        workflow_key="share_action.add_content.v1",
    )
    db_session.commit()
    calls: list[int] = []

    def fake_run_share_action_task(_db, *, llm_task_id: int):
        calls.append(llm_task_id)

    monkeypatch.setattr(
        "app.pipeline.handlers.run_llm_task.run_share_action_task",
        fake_run_share_action_task,
    )

    class Context:
        def db_factory(self):
            return _SessionContext(db_session)

    result = RunLlmTaskHandler().handle(
        TaskEnvelope(
            id=1,
            task_type=TaskType.RUN_LLM_TASK,
            payload={"llm_task_id": llm_task.id, "user_id": test_user.id},
        ),
        cast(TaskContext, Context()),
    )

    assert result.success is True
    assert calls == [llm_task.id]


def test_run_llm_task_handler_rejects_missing_task_id() -> None:
    result = RunLlmTaskHandler().handle(
        TaskEnvelope(id=1, task_type=TaskType.RUN_LLM_TASK, payload={}),
        cast(TaskContext, object()),
    )

    assert result.success is False
    assert result.retryable is False


def test_run_llm_task_handler_dispatches_learning_deck(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    llm_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
        subject_id=44,
    )
    db_session.commit()
    calls: list[int] = []
    monkeypatch.setattr(
        "app.pipeline.handlers.run_llm_task.run_learning_deck_task",
        lambda _db, *, llm_task_id, **_kwargs: calls.append(llm_task_id),
    )

    result = RunLlmTaskHandler().handle(
        TaskEnvelope(
            id=2,
            task_type=TaskType.RUN_LLM_TASK,
            payload={"llm_task_id": llm_task.id},
        ),
        cast(TaskContext, SimpleContext(db_session)),
    )

    assert result.success is True
    assert calls == [llm_task.id]


def test_run_llm_task_handler_defers_learning_deck_source_wait(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    llm_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
        subject_id=44,
    )
    db_session.commit()

    def wait_for_source(_db, *, llm_task_id: int, **_kwargs) -> None:
        assert llm_task_id == llm_task.id
        raise LearningDeckGenerationWaiting("still processing", retry_delay_seconds=90)

    monkeypatch.setattr(
        "app.pipeline.handlers.run_llm_task.run_learning_deck_task",
        wait_for_source,
    )

    result = RunLlmTaskHandler().handle(
        TaskEnvelope(
            id=3,
            task_type=TaskType.RUN_LLM_TASK,
            payload={"llm_task_id": llm_task.id},
        ),
        cast(TaskContext, SimpleContext(db_session)),
    )

    assert result.deferred is True
    assert result.retry_delay_seconds == 90


class _SessionContext:
    def __init__(self, db_session) -> None:
        self.db_session = db_session

    def __enter__(self):
        return self.db_session

    def __exit__(self, *_exc_info) -> None:
        return None


class SimpleContext:
    def __init__(self, db_session) -> None:
        self.db_session = db_session
        self.queue_service = SimpleNamespace(renew_lease=lambda *_args, **_kwargs: True)
        self.settings = SimpleNamespace(queue=SimpleNamespace(worker_timeout_seconds=300))
        self.worker_id = "test-worker"

    def db_factory(self):
        return _SessionContext(self.db_session)
