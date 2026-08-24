"""Focused coverage for retry-fenced durable chat partials."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    FinalResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from app.models.contracts import MessageProcessingStatus
from app.models.db import ChatMessage, ChatSession
from app.services.chat_agent import (
    ARTICLE_BACKGROUND_TURN_LIFECYCLE,
    update_message_completed,
    update_message_failed,
)
from app.services.chat_partial_stream import (
    DurableChatPartialWriter,
    DurableChatToolProgressWriter,
    build_final_text_event_stream_handler,
    initialize_chat_stream_attempt,
)
from app.services.chat_turn_queue import build_chat_turn_context
from app.services.chat_turn_runtime import (
    ChatTurnLeaseCheckError,
    ChatTurnOwnershipLost,
    QueuedChatTurnOutcome,
    require_current_chat_lease,
)
from app.services.queued_chat_turn import execute_queued_chat_turn


def test_new_stream_generation_clears_partial_and_rejects_old_writer(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Streaming chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db_session.add(message)
    db_session.commit()
    assert session.id is not None
    assert message.id is not None

    initialize_chat_stream_attempt(db_session, message_id=message.id, stream_generation=1)
    db_session.commit()
    old_writer = DurableChatPartialWriter(
        session_factory=db_session_factory,
        message_id=message.id,
        stream_generation=1,
        minimum_interval_seconds=0,
    )
    assert old_writer.publish("First attempt") is True

    initialize_chat_stream_attempt(db_session, message_id=message.id, stream_generation=2)
    db_session.commit()
    assert old_writer.publish("Stale overwrite", force=True) is False

    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted is not None
    assert persisted.stream_generation == 2
    assert persisted.stream_revision == 0
    assert persisted.partial_text is None


def test_tool_progress_is_retry_fenced_and_separate_from_partial_text(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Tool progress",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db_session.add(message)
    db_session.commit()
    assert message.id is not None

    initialize_chat_stream_attempt(db_session, message_id=message.id, stream_generation=1)
    db_session.commit()
    old_writer = DurableChatToolProgressWriter(
        session_factory=db_session_factory,
        message_id=message.id,
        stream_generation=1,
        minimum_interval_seconds=0,
    )
    assert old_writer.publish(
        tool_name="execute_bash",
        status="running",
        detail="first stdout chunk",
    )

    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted is not None
    assert persisted.partial_text is None
    assert persisted.tool_progress == {
        "tool_name": "execute_bash",
        "status": "running",
        "detail": "first stdout chunk",
        "updated_at": persisted.tool_progress["updated_at"],
    }
    assert persisted.tool_progress_revision == 1

    initialize_chat_stream_attempt(db_session, message_id=message.id, stream_generation=2)
    db_session.commit()
    assert old_writer.publish(tool_name="execute_bash", status="completed") is False

    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted is not None
    assert persisted.tool_progress is None
    assert persisted.tool_progress_revision == 0


@pytest.mark.asyncio
async def test_event_handler_omits_tool_planning_text_and_publishes_final_text() -> None:
    class RecordingWriter:
        def __init__(self) -> None:
            self.values: list[tuple[str, bool]] = []

        def publish(self, text: str, *, force: bool = False) -> bool:
            self.values.append((text, force))
            return True

        def is_ready(self, *, force: bool = False) -> bool:
            return True

    async def events(values: list[Any]):
        for value in values:
            yield value

    writer = RecordingWriter()
    handler = build_final_text_event_stream_handler(cast(DurableChatPartialWriter, writer))
    context = cast(RunContext[Any], None)

    await handler(
        context,
        events(
            [
                PartStartEvent(index=0, part=TextPart("I should search first")),
                FinalResultEvent(tool_name="search_web", tool_call_id="call-1"),
                PartEndEvent(index=0, part=TextPart("I should search first")),
            ]
        ),
    )
    assert writer.values == []

    await handler(
        context,
        events(
            [
                PartStartEvent(index=0, part=TextPart("Hel")),
                FinalResultEvent(tool_name=None, tool_call_id=None),
                PartDeltaEvent(index=0, delta=TextPartDelta("lo")),
                PartEndEvent(index=0, part=TextPart("Hello")),
            ]
        ),
    )
    assert writer.values[-1] == ("Hello", True)
    assert all("search" not in text for text, _ in writer.values)


def test_advisory_partial_persistence_failure_does_not_escape() -> None:
    attempts = 0

    class FailingSessionContext:
        def __enter__(self):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("database unavailable")

        def __exit__(self, *_args) -> None:
            return None

    def failing_session_factory():
        return FailingSessionContext()

    writer = DurableChatPartialWriter(
        session_factory=cast(Any, failing_session_factory),
        message_id=10,
        stream_generation=2,
        minimum_interval_seconds=0,
    )

    assert writer.publish("Advisory text") is False
    assert writer.publish("More advisory text") is False
    assert attempts == 1


def test_terminal_write_rejects_stale_generation_even_when_row_is_already_terminal(
    db_session,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Completed by newer attempt",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        status=MessageProcessingStatus.COMPLETED.value,
        stream_generation=2,
    )
    db_session.add(message)
    db_session.commit()
    assert message.id is not None

    with pytest.raises(ChatTurnOwnershipLost):
        update_message_completed(
            db_session,
            message.id,
            [],
            expected_stream_generation=1,
        )
    with pytest.raises(ChatTurnOwnershipLost):
        update_message_failed(
            db_session,
            message.id,
            "Stale failure",
            expected_stream_generation=1,
        )


def test_failure_before_ledger_creation_still_terminalizes_attempt_generation(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Early failure",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db_session.add(message)
    db_session.commit()
    assert session.id is not None
    assert message.id is not None
    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session.id,
        user_prompt="Fail early",
        kind="article",
        source="queue",
    )

    def fail_input(_turn):
        raise RuntimeError("raw preparation failure")

    def must_not_prepare(*_args):
        raise AssertionError("preparation must not run")

    async def must_not_execute(*_args):
        raise AssertionError("provider must not run")

    result = asyncio.run(
        execute_queued_chat_turn(
            session_factory=db_session_factory,
            session_snapshot=turn_context.session,
            session_id=session.id,
            message_id=message.id,
            source="queue",
            task_id=10,
            stream_generation=3,
            lifecycle=ARTICLE_BACKGROUND_TURN_LIFECYCLE,
            input_json=fail_input,
            prepare=must_not_prepare,
            execute=must_not_execute,
            persist=lambda *_args: {},
            mark_message_failed=lambda db, message_id, error, generation: update_message_failed(
                db,
                message_id,
                error,
                expected_stream_generation=generation,
                commit=False,
            ),
            raw_result=lambda result: result,
            record_usage=lambda *_args: None,
            ensure_lease=lambda: True,
            cleanup=lambda _prepared: None,
        )
    )

    db_session.expire_all()
    persisted = db_session.get(ChatMessage, message.id)
    assert persisted is not None
    assert result.outcome == QueuedChatTurnOutcome.FAILED
    assert persisted.status == MessageProcessingStatus.FAILED.value
    assert persisted.stream_generation == 3
    assert persisted.error == "This chat turn could not be completed. Please retry."


def test_lease_is_renewed_after_preparation_before_provider_submission(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Lease fence",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.flush()
    message = ChatMessage(
        session_id=session.id,
        message_list="[]",
        status=MessageProcessingStatus.PROCESSING.value,
    )
    db_session.add(message)
    db_session.commit()
    assert session.id is not None
    assert message.id is not None
    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session.id,
        user_prompt="Do provider work",
        kind="article",
        source="queue",
    )
    provider_called = False

    async def execute(*_args):
        nonlocal provider_called
        provider_called = True
        return object()

    result = asyncio.run(
        execute_queued_chat_turn(
            session_factory=db_session_factory,
            session_snapshot=turn_context.session,
            session_id=session.id,
            message_id=message.id,
            source="queue",
            task_id=11,
            stream_generation=1,
            lifecycle=ARTICLE_BACKGROUND_TURN_LIFECYCLE,
            input_json=lambda _turn: {"prompt": "Do provider work"},
            prepare=lambda _db, _turn: object(),
            execute=execute,
            persist=lambda *_args: {},
            mark_message_failed=lambda *_args: None,
            raw_result=lambda result: result,
            record_usage=lambda *_args: None,
            ensure_lease=lambda: False,
            cleanup=lambda _prepared: None,
        )
    )

    assert result.outcome == QueuedChatTurnOutcome.OWNERSHIP_LOST
    assert provider_called is False


def test_exact_lease_check_distinguishes_loss_from_verification_error() -> None:
    with pytest.raises(ChatTurnOwnershipLost):
        require_current_chat_lease(lambda: False)

    def raise_database_error() -> bool:
        raise RuntimeError("database unavailable")

    with pytest.raises(ChatTurnLeaseCheckError):
        require_current_chat_lease(raise_database_error)
