from __future__ import annotations

from typing import cast

from app.models.contracts import LlmTaskKind, LlmTaskMode, TaskType
from app.pipeline.handlers.run_llm_task import RunLlmTaskHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
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


class _SessionContext:
    def __init__(self, db_session) -> None:
        self.db_session = db_session

    def __enter__(self):
        return self.db_session

    def __exit__(self, *_exc_info) -> None:
        return None
