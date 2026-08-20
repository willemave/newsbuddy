"""Lifecycle coverage for queued dig-deeper fanout."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from app.core.settings import Settings
from app.models.contracts import ContentStatus, ContentType, MessageProcessingStatus, TaskStatus
from app.models.db import ChatMessage, ChatSession, Content, ProcessingTask
from app.pipeline.handlers.dig_deeper import DigDeeperHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.chat_turn_runtime import ChatTurnLeaseCheckError, QueuedChatTurnOutcome
from app.services.dig_deeper import prepare_dig_deeper_task_message
from app.services.queue import TaskType


def _settings(max_retries: int = 3) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(queue=SimpleNamespace(max_retries=max_retries)),
    )


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def _require_payload(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def test_dig_deeper_handler_terminally_skips_inactive_user(
    db_session,
    user_factory,
    monkeypatch,
) -> None:
    user = user_factory(is_active=False)
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/deleted-user-story",
        title="Deleted User Story",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.commit()

    @contextmanager
    def db_factory():
        yield db_session

    def unexpected_chat_processing(*_args, **_kwargs) -> None:
        raise AssertionError("inactive user must not reach chat processing")

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        unexpected_chat_processing,
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=_settings(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=db_factory,
    )
    task = TaskEnvelope(
        id=41,
        task_type=TaskType.DIG_DEEPER,
        content_id=content.id,
        payload={"user_id": user.id},
    )

    result = DigDeeperHandler().handle(task, context)

    assert result.success is True
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessage).count() == 0


def test_dig_deeper_escaped_failure_retries_without_duplicate_message(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/retry-safe-story",
        title="Retry-safe Story",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.flush()
    task_row = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        content_id=content.id,
        payload={"user_id": test_user.id, "initial_message": "Explain this."},
        status=TaskStatus.PROCESSING.value,
        queue_name="chat",
    )
    db_session.add(task_row)
    db_session.commit()

    @contextmanager
    def db_factory():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    attempts: list[tuple[int, int, str, int | None]] = []

    def fail_before_chat_wrapper_returns(
        session_id: int,
        message_id: int,
        prompt: str,
        *,
        task_id: int | None = None,
        **_kwargs,
    ) -> None:
        attempts.append((session_id, message_id, prompt, task_id))
        raise RuntimeError("worker interrupted")

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        fail_before_chat_wrapper_returns,
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=_settings(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=db_factory,
    )
    envelope = TaskEnvelope(
        id=_require_id(task_row.id),
        owner_user_id=int(test_user.id),
        task_type=TaskType.DIG_DEEPER,
        content_id=_require_id(content.id),
        payload=_require_payload(task_row.payload).copy(),
    )

    first_result = DigDeeperHandler().handle(envelope, context)

    db_session.refresh(task_row)
    messages = db_session.query(ChatMessage).all()
    assert first_result.success is False
    assert first_result.retryable is True
    assert len(messages) == 1
    assert messages[0].status == MessageProcessingStatus.PROCESSING.value
    assert _require_payload(task_row.payload)["message_id"] == messages[0].id

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        lambda *_args, **_kwargs: QueuedChatTurnOutcome.COMPLETED,
    )
    redelivered = envelope.model_copy(update={"payload": _require_payload(task_row.payload).copy()})

    second_result = DigDeeperHandler().handle(redelivered, context)

    assert second_result.success is True
    assert db_session.query(ChatMessage).count() == 1
    assert len(attempts) == 1


def test_dig_deeper_retry_budget_terminalizes_the_owned_generation(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/retry-budget-story",
        title="Retry Budget Story",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.flush()
    task_row = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        content_id=content.id,
        payload={"user_id": test_user.id, "initial_message": "Explain this."},
        status=TaskStatus.PROCESSING.value,
        queue_name="chat",
    )
    db_session.add(task_row)
    db_session.commit()

    @contextmanager
    def db_factory():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exhausted retry budget must not call the provider")
        ),
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=_settings(max_retries=3),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=db_factory,
        lease_renewer=lambda: True,
    )
    task = TaskEnvelope(
        id=_require_id(task_row.id),
        owner_user_id=int(test_user.id),
        task_type=TaskType.DIG_DEEPER,
        content_id=_require_id(content.id),
        payload=_require_payload(task_row.payload).copy(),
        retry_count=4,
    )

    result = DigDeeperHandler().handle(task, context)

    assert result.success is False
    assert result.retryable is False
    message = db_session.query(ChatMessage).one()
    assert message.status == MessageProcessingStatus.FAILED.value
    assert message.stream_generation == 4
    assert message.partial_text is None


def test_dig_deeper_redelivery_resumes_atomically_prepared_message(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/reclaimed-story",
        title="Reclaimed Story",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.flush()
    task_row = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        content_id=content.id,
        payload={"user_id": test_user.id, "initial_message": "Resume this."},
        status=TaskStatus.PROCESSING.value,
        queue_name="chat",
    )
    db_session.add(task_row)
    db_session.commit()

    prepared = prepare_dig_deeper_task_message(
        db_session,
        task_id=_require_id(task_row.id),
        content=content,
        user_id=int(test_user.id),
        initial_message="Resume this.",
    )
    session_id, message_id, prompt, status, accepted_context = prepared
    assert status is MessageProcessingStatus.PROCESSING
    assert prompt == "Resume this."
    task_row.payload = {
        **_require_payload(task_row.payload),
        "session_id": str(session_id),
        "message_id": str(message_id),
    }
    session = db_session.get(ChatSession, session_id)
    assert session is not None
    session.llm_model = "anthropic:claude-opus-4-6"
    session.context_snapshot = "Mutable context changed after acceptance"
    db_session.commit()

    @contextmanager
    def db_factory():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    resumed: list[tuple[int, int, str, int | None, object, int, bool]] = []

    def run_existing_message(
        received_session_id: int,
        received_message_id: int,
        received_prompt: str,
        *,
        task_id: int | None = None,
        turn_context,
        stream_generation: int,
        ensure_lease,
    ) -> QueuedChatTurnOutcome:
        resumed.append(
            (
                received_session_id,
                received_message_id,
                received_prompt,
                task_id,
                turn_context,
                stream_generation,
                ensure_lease(),
            )
        )
        return QueuedChatTurnOutcome.COMPLETED

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        run_existing_message,
    )
    db_session.refresh(task_row)
    context = TaskContext(
        queue_service=Mock(),
        settings=_settings(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=db_factory,
        lease_renewer=lambda: True,
    )
    redelivered = TaskEnvelope(
        id=_require_id(task_row.id),
        owner_user_id=int(test_user.id),
        task_type=TaskType.DIG_DEEPER,
        content_id=_require_id(content.id),
        payload=_require_payload(task_row.payload).copy(),
    )

    result = DigDeeperHandler().handle(redelivered, context)

    assert result.success is True
    assert resumed == [
        (
            session_id,
            message_id,
            prompt,
            _require_id(task_row.id),
            accepted_context,
            0,
            True,
        )
    ]
    assert db_session.query(ChatMessage).count() == 1
    db_session.refresh(task_row)
    assert _require_payload(task_row.payload)["session_id"] == str(session_id)
    assert _require_payload(task_row.payload)["message_id"] == str(message_id)


def test_dig_deeper_retries_when_terminal_lease_cannot_be_verified(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/terminal-write-outage",
        title="Terminal Write Outage",
        source="example.com",
        status=ContentStatus.COMPLETED.value,
        content_metadata={},
    )
    db_session.add(content)
    db_session.flush()
    task_row = ProcessingTask(
        owner_user_id=test_user.id,
        task_type=TaskType.DIG_DEEPER.value,
        content_id=content.id,
        payload={"user_id": test_user.id, "initial_message": "Try once."},
        status=TaskStatus.PROCESSING.value,
        queue_name="chat",
    )
    db_session.add(task_row)
    db_session.commit()

    @contextmanager
    def db_factory():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(
        "app.pipeline.handlers.dig_deeper.run_dig_deeper_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ChatTurnLeaseCheckError("lease check unavailable")
        ),
    )
    context = TaskContext(
        queue_service=Mock(),
        settings=_settings(),
        llm_service=Mock(),
        worker_id="test-worker",
        db_factory=db_factory,
    )
    task = TaskEnvelope(
        id=_require_id(task_row.id),
        owner_user_id=int(test_user.id),
        task_type=TaskType.DIG_DEEPER,
        content_id=_require_id(content.id),
        payload=_require_payload(task_row.payload).copy(),
    )

    result = DigDeeperHandler().handle(task, context)

    assert result.success is False
    assert result.retryable is True
    message = db_session.query(ChatMessage).one()
    assert message.status == MessageProcessingStatus.PROCESSING.value
