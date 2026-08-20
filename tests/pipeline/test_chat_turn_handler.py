"""Focused coverage for durable chat-turn ordering and terminal behavior."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from app.models.contracts import MessageProcessingStatus, TaskStatus, TaskType
from app.models.db import ChatMessage, ChatSession, ProcessingTask
from app.pipeline.handlers.chat_turn import (
    CHAT_TURN_FAILED_MESSAGE,
    ChatTurnHandler,
)
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.chat_turn_queue import build_chat_turn_context
from app.services.chat_turn_runtime import (
    ChatTurnLeaseCheckError,
    QueuedChatTurnOutcome,
)


def _seed_turn(db, user, *, session: ChatSession | None = None):
    if session is None:
        session = ChatSession(
            user_id=user.id,
            title="Queued chat",
            session_type="knowledge_chat",
            llm_model="openai:gpt-5.6-terra",
            llm_provider="openai",
        )
        db.add(session)
        db.flush()
    assert session.id is not None
    context = build_chat_turn_context(
        session,
        visible_session_id=session.id,
        user_prompt="What changed?",
        kind="article",
        source="realtime",
    )
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        processing_context=context.model_dump(mode="json"),
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db.add(message)
    db.flush()
    assert message.id is not None
    task = ProcessingTask(
        owner_user_id=user.id,
        task_type=TaskType.CHAT_TURN.value,
        payload={"user_id": user.id, "session_id": session.id, "message_id": message.id},
        status=TaskStatus.PROCESSING.value,
        queue_name="chat",
    )
    db.add(task)
    db.commit()
    return session, message, task, context


def _envelope(task: ProcessingTask) -> TaskEnvelope:
    assert task.id is not None
    assert isinstance(task.payload, dict)
    assert task.retry_count is not None
    return TaskEnvelope(
        id=task.id,
        task_type=TaskType.CHAT_TURN,
        payload=dict(task.payload),
        retry_count=task.retry_count,
    )


def _context(db_session_factory) -> TaskContext:
    @contextmanager
    def db_factory():
        db = db_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return cast(
        TaskContext,
        SimpleNamespace(
            db_factory=db_factory,
            settings=SimpleNamespace(queue=SimpleNamespace(max_retries=3)),
            renew_current_lease=lambda: True,
        ),
    )


def test_chat_turn_completes_and_terminal_redelivery_is_a_noop(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    session, message, task, accepted_context = _seed_turn(db_session, test_user)
    session.llm_model = "anthropic:claude-opus-4-6"
    session.context_snapshot = "Newer mutable context"
    db_session.commit()
    calls = []

    def complete(_task, turn_context, *, ensure_lease) -> QueuedChatTurnOutcome:
        assert ensure_lease() is True
        calls.append(turn_context)
        with db_session_factory() as db:
            row = db.query(ChatMessage).filter(ChatMessage.id == message.id).one()
            row.status = MessageProcessingStatus.COMPLETED.value
            db.commit()
        return QueuedChatTurnOutcome.COMPLETED

    monkeypatch.setattr("app.pipeline.handlers.chat_turn._run_chat_turn", complete)
    handler = ChatTurnHandler()
    envelope = _envelope(task)

    assert handler.handle(envelope, _context(db_session_factory)).success is True
    assert calls == [accepted_context]
    assert calls[0].session.model == "openai:gpt-5.6-terra"
    assert calls[0].session.context_snapshot is None
    assert handler.handle(envelope, _context(db_session_factory)).success is True
    assert calls == [accepted_context]


def test_chat_turn_defers_behind_earlier_processing_message(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    session, earlier, _, _ = _seed_turn(db_session, test_user)
    _, later, later_task, _ = _seed_turn(db_session, test_user, session=session)
    monkeypatch.setattr(
        "app.pipeline.handlers.chat_turn._run_chat_turn",
        lambda *_args: pytest.fail("Later turn must not run before its predecessor"),
    )

    result = ChatTurnHandler().handle(_envelope(later_task), _context(db_session_factory))

    assert result.deferred is True
    assert result.retry_delay_seconds == 2
    db_session.expire_all()
    assert (
        db_session.get(ChatMessage, earlier.id).status == MessageProcessingStatus.PROCESSING.value
    )
    assert db_session.get(ChatMessage, later.id).status == MessageProcessingStatus.PROCESSING.value

    earlier_row = db_session.get(ChatMessage, earlier.id)
    earlier_row.status = MessageProcessingStatus.FAILED.value
    db_session.commit()

    def complete_later(_task, _turn_context, **_kwargs) -> QueuedChatTurnOutcome:
        with db_session_factory() as db:
            row = db.get(ChatMessage, later.id)
            row.status = MessageProcessingStatus.COMPLETED.value
            db.commit()
        return QueuedChatTurnOutcome.COMPLETED

    monkeypatch.setattr("app.pipeline.handlers.chat_turn._run_chat_turn", complete_later)
    assert (
        ChatTurnHandler()
        .handle(
            _envelope(later_task),
            _context(db_session_factory),
        )
        .success
    )


@pytest.mark.parametrize("invalid_state", ["inactive_user", "archived_session", "wrong_owner"])
def test_chat_turn_rejects_invalid_lifecycle_boundaries_without_provider_work(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
    invalid_state: str,
) -> None:
    session, message, task, _ = _seed_turn(db_session, test_user)
    if invalid_state == "inactive_user":
        test_user.is_active = False
    elif invalid_state == "archived_session":
        session.is_archived = True
    else:
        task.owner_user_id = None
    db_session.commit()
    monkeypatch.setattr(
        "app.pipeline.handlers.chat_turn._run_chat_turn",
        lambda *_args: pytest.fail("Invalid turn must not call a provider"),
    )

    result = ChatTurnHandler().handle(_envelope(task), _context(db_session_factory))

    assert result.success is False
    assert result.retryable is False
    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    expected_status = (
        MessageProcessingStatus.PROCESSING.value
        if invalid_state == "wrong_owner"
        else MessageProcessingStatus.FAILED.value
    )
    assert persisted.status == expected_status


def test_unexpected_chat_executor_exception_preserves_message_for_retry(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    _, message, task, _ = _seed_turn(db_session, test_user)

    def fail(*_args) -> None:
        raise RuntimeError("raw provider secret")

    monkeypatch.setattr("app.pipeline.handlers.chat_turn._run_chat_turn", fail)

    result = ChatTurnHandler().handle(_envelope(task), _context(db_session_factory))

    assert result.success is False
    assert result.retryable is True
    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted.status == MessageProcessingStatus.PROCESSING.value
    assert persisted.error is None


def test_chat_turn_reclaim_budget_exhaustion_terminalizes_without_provider(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    _, message, task, _ = _seed_turn(db_session, test_user)
    task.retry_count = 4
    db_session.commit()
    monkeypatch.setattr(
        "app.pipeline.handlers.chat_turn._run_chat_turn",
        lambda *_args: pytest.fail("exhausted reclaim must not call a provider"),
    )

    result = ChatTurnHandler().handle(_envelope(task), _context(db_session_factory))

    assert result.success is False
    assert result.retryable is False
    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted.status == MessageProcessingStatus.FAILED.value
    assert persisted.error == CHAT_TURN_FAILED_MESSAGE


def test_chat_turn_ownership_loss_does_not_terminalize_canonical_message(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    _, message, task, _ = _seed_turn(db_session, test_user)
    monkeypatch.setattr(
        "app.pipeline.handlers.chat_turn._run_chat_turn",
        lambda *_args, **_kwargs: QueuedChatTurnOutcome.OWNERSHIP_LOST,
    )

    result = ChatTurnHandler().handle(_envelope(task), _context(db_session_factory))

    assert result.success is True
    db_session.expire_all()
    assert (
        db_session.get(ChatMessage, message.id).status == MessageProcessingStatus.PROCESSING.value
    )


def test_chat_turn_lease_verification_error_retries_without_message_write(
    db_session,
    db_session_factory,
    monkeypatch,
    test_user,
) -> None:
    _, message, task, _ = _seed_turn(db_session, test_user)

    def fail_lease_check(*_args, **_kwargs):
        raise ChatTurnLeaseCheckError("database unavailable")

    monkeypatch.setattr("app.pipeline.handlers.chat_turn._run_chat_turn", fail_lease_check)

    result = ChatTurnHandler().handle(_envelope(task), _context(db_session_factory))

    assert result.success is False
    assert result.retryable is True
    db_session.expire_all()
    assert (
        db_session.get(ChatMessage, message.id).status == MessageProcessingStatus.PROCESSING.value
    )
