"""Transaction-boundary tests for durable chat turn acceptance."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.commands import create_assistant_turn, send_chat_message
from app.models.api.chat import AssistantTurnRequest, SendChatMessageRequest
from app.models.db import ChatMessage, ChatSession, ProcessingTask


class _FailingQueueGateway:
    def enqueue_many_in_session(self, _db, _requests):
        raise RuntimeError("queue unavailable")


def test_send_message_queue_failure_rolls_back_processing_message(
    db_session,
    monkeypatch,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Atomic chat",
        session_type="knowledge_chat",
        llm_model="openai:gpt-5.6-terra",
        llm_provider="openai",
    )
    db_session.add(session)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.chat_turn_queue.get_task_queue_gateway",
        lambda: _FailingQueueGateway(),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        send_chat_message.execute(
            db_session,
            user_id=test_user.id,
            session_id=session.id,
            request=SendChatMessageRequest(message="Will this queue?"),
        )
    db_session.rollback()

    assert db_session.query(ChatMessage).filter(ChatMessage.session_id == session.id).count() == 0
    assert db_session.query(ProcessingTask).count() == 0


def test_new_assistant_session_queue_failure_rolls_back_session_and_message(
    db_session,
    monkeypatch,
    test_user,
) -> None:
    existing_session_count = db_session.query(ChatSession).count()
    monkeypatch.setattr(
        "app.services.chat_turn_queue.get_task_queue_gateway",
        lambda: _FailingQueueGateway(),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        create_assistant_turn.execute(
            db_session,
            user_id=test_user.id,
            request=AssistantTurnRequest(
                message="Find something useful",
                screen_context={"screen_type": "knowledge_hub", "screen_title": "Knowledge"},
            ),
        )
    db_session.rollback()

    assert db_session.query(ChatSession).count() == existing_session_count
    assert db_session.query(ChatMessage).count() == 0
    assert db_session.query(ProcessingTask).count() == 0


@pytest.mark.parametrize(
    ("session_overrides", "expected_status"),
    [
        ({"is_archived": True}, 409),
        ({"is_hidden_from_history": True}, 404),
    ],
)
def test_direct_send_rejects_unavailable_sessions(
    db_session,
    test_user,
    session_overrides: dict[str, bool],
    expected_status: int,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Unavailable chat",
        session_type="knowledge_chat",
        llm_model="openai:gpt-5.6-terra",
        llm_provider="openai",
        **session_overrides,
    )
    db_session.add(session)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        send_chat_message.execute(
            db_session,
            user_id=test_user.id,
            session_id=session.id,
            request=SendChatMessageRequest(message="Invisible work"),
        )

    assert exc_info.value.status_code == expected_status
    assert db_session.query(ChatMessage).filter(ChatMessage.session_id == session.id).count() == 0
