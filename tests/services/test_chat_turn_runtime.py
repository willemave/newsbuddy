"""Tests for shared chat turn runtime helpers."""

from types import SimpleNamespace

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from sqlalchemy.orm import object_session

from app.models.contracts import (
    LlmTaskApprovalPolicy,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    MessageProcessingStatus,
)
from app.models.db import ChatMessage, ChatSession, LlmTask
from app.models.internal.chat_turn import ChatTurnSessionSnapshot
from app.services import chat_turn_runtime
from app.services.llm_models import DEFAULT_MODEL
from app.services.llm_task_turn_tracker import LlmTaskTurnSpec

TEST_TURN_SPEC = LlmTaskTurnSpec(
    task_kind=LlmTaskKind.ARTICLE_CHAT,
    mode=LlmTaskMode.ARTICLE_CHAT,
    workflow_key="test.chat.runtime.v1",
    approval_policy={"default": LlmTaskApprovalPolicy.APPROVAL_REQUIRED.value},
)
TEST_LIFECYCLE = chat_turn_runtime.DetachedChatTurnLifecycle(
    task_spec=TEST_TURN_SPEC,
    running_note="Test turn running",
    completed_note="Test turn completed",
    failed_note="Test turn failed",
    usage_context="test",
)


def test_extract_tool_names_uses_only_new_message_parts() -> None:
    result = SimpleNamespace(
        all_messages=[ModelResponse(parts=[ToolCallPart(tool_name="old_tool", args={})])],
        new_messages=lambda: [
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="search_web", args={"query": "AI"}),
                    TextPart(content="I found a source."),
                    ToolCallPart(tool_name="find_feed_options", args={"url": "https://x.test"}),
                ]
            )
        ],
    )

    assert chat_turn_runtime.extract_tool_names(result) == [
        "search_web",
        "find_feed_options",
    ]


def test_extract_tool_names_returns_empty_for_non_agent_result() -> None:
    assert chat_turn_runtime.extract_tool_names(SimpleNamespace()) == []


def test_get_or_create_cached_agent_scopes_by_namespace_model_and_credential() -> None:
    chat_turn_runtime.clear_agent_cache_for_tests()
    calls: list[str] = []

    def _factory(label: str) -> object:
        calls.append(label)
        return object()

    first = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5.6-terra",
        "user-key",
        lambda: _factory("first"),
    )
    second = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5.6-terra",
        "user-key",
        lambda: _factory("second"),
    )
    other_model = chat_turn_runtime.get_or_create_cached_agent(
        "article_chat",
        "openai:gpt-5-mini",
        "user-key",
        lambda: _factory("other_model"),
    )
    other_namespace = chat_turn_runtime.get_or_create_cached_agent(
        "contextual_assistant",
        "openai:gpt-5.6-terra",
        "user-key",
        lambda: _factory("other_namespace"),
    )

    assert first is second
    assert other_model is not first
    assert other_namespace is not first
    assert calls == ["first", "other_model", "other_namespace"]
    chat_turn_runtime.clear_agent_cache_for_tests()


def test_chat_usage_snapshot_captures_validated_session_fields() -> None:
    session = ChatSession(
        user_id=42,
        llm_model="",
        content_id=99,
        session_type="article",
    )

    snapshot = chat_turn_runtime.ChatUsageSnapshot.from_session(session)

    assert snapshot.user_id == 42
    assert snapshot.model == DEFAULT_MODEL
    assert snapshot.content_id == 99
    assert snapshot.session_type == "article"


def test_detached_turn_builds_directly_from_queued_session_snapshot() -> None:
    snapshot = ChatTurnSessionSnapshot(
        user_id=42,
        effective_session_id=7,
        visible_session_id=7,
        model="anthropic:claude-opus-4-6",
        provider="anthropic",
        content_id=99,
        session_type="knowledge_chat",
    )

    turn = chat_turn_runtime.snapshot_detached_chat_turn_from_snapshot(
        snapshot,
        message_id=8,
        source="queue",
        task_id=73,
    )

    assert turn.session_id == 7
    assert turn.message_id == 8
    assert turn.user_id == 42
    assert turn.model == "anthropic:claude-opus-4-6"
    assert turn.provider == "anthropic"
    assert turn.content_id == 99
    assert turn.task_id == 73


def test_detached_turn_primitives_close_prepare_session_and_complete_ledger(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Runtime success",
        session_type="knowledge_chat",
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
    session_id = int(session.id)
    message_id = int(message.id)
    with db_session_factory() as prepare_db:
        loaded_session = prepare_db.query(ChatSession).filter(ChatSession.id == session_id).one()
        turn = chat_turn_runtime.snapshot_detached_chat_turn(
            loaded_session,
            message_id=message_id,
            source="test",
            task_id=73,
        )
        turn, tracker = chat_turn_runtime.start_detached_chat_turn(
            prepare_db,
            turn=turn,
            lifecycle=TEST_LIFECYCLE,
            input_json={
                "chat_session_id": turn.session_id,
                "message_id": turn.message_id,
                "queue_task_id": turn.task_id,
            },
        )
        assert turn.session_id == session_id
        assert turn.message_id == message_id
        assert turn.user_id == test_user.id
        prepared_session = loaded_session
        chat_turn_runtime.mark_detached_chat_turn_running(
            prepare_db,
            turn=turn,
            tracker=tracker,
            lifecycle=TEST_LIFECYCLE,
        )

    assert object_session(prepared_session) is None
    assert turn.llm_task_id is not None and turn.llm_task_id > 0

    with db_session_factory() as persist_db:
        persisted_session = persist_db.query(ChatSession).filter(ChatSession.id == session_id).one()
        db_message = persist_db.query(ChatMessage).filter(ChatMessage.id == turn.message_id).one()
        db_message.status = MessageProcessingStatus.COMPLETED.value
        db_message.message_list = '[{"kind":"response","parts":[]}]'
        assert persisted_session.id == session_id
        chat_turn_runtime.complete_detached_chat_turn(
            persist_db,
            session=persisted_session,
            turn=turn,
            tracker=tracker,
            lifecycle=TEST_LIFECYCLE,
            output_json={
                "chat_session_id": turn.session_id,
                "message_id": turn.message_id,
            },
        )

    db_session.expire_all()
    persisted_message = db_session.query(ChatMessage).filter(ChatMessage.id == message_id).one()
    persisted_session = db_session.query(ChatSession).filter(ChatSession.id == session_id).one()
    task = db_session.query(LlmTask).filter(LlmTask.id == turn.llm_task_id).one()
    assert persisted_message.status == MessageProcessingStatus.COMPLETED.value
    assert persisted_session.last_message_at is not None
    assert task.status == LlmTaskStatus.COMPLETED.value
    assert task.input_json["queue_task_id"] == 73
    assert task.output_json == {"chat_session_id": session_id, "message_id": message_id}


def test_detached_turn_failure_marks_message_and_ledger_failed(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Runtime failure",
        session_type="knowledge_chat",
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
    session_id = int(session.id)
    message_id = int(message.id)

    def _mark_failed(db, failed_message_id, error):  # noqa: ANN001
        row = db.query(ChatMessage).filter(ChatMessage.id == failed_message_id).one()
        row.status = MessageProcessingStatus.FAILED.value
        row.error = error

    with db_session_factory() as prepare_db:
        loaded_session = prepare_db.query(ChatSession).filter(ChatSession.id == session_id).one()
        turn = chat_turn_runtime.snapshot_detached_chat_turn(
            loaded_session,
            message_id=message_id,
            source="test",
            task_id=None,
        )
        turn, tracker = chat_turn_runtime.start_detached_chat_turn(
            prepare_db,
            turn=turn,
            lifecycle=TEST_LIFECYCLE,
            input_json={"chat_session_id": turn.session_id},
        )
        chat_turn_runtime.mark_detached_chat_turn_running(
            prepare_db,
            turn=turn,
            tracker=tracker,
            lifecycle=TEST_LIFECYCLE,
        )

    error = RuntimeError("provider exploded")
    chat_turn_runtime.persist_detached_turn_failure(
        session_factory=db_session_factory,
        tracker=tracker,
        lifecycle=TEST_LIFECYCLE,
        message_id=message_id,
        error=error,
        mark_message_failed=_mark_failed,
    )
    assert turn.llm_task_id is not None

    db_session.expire_all()
    persisted_message = db_session.query(ChatMessage).filter(ChatMessage.id == message_id).one()
    task = db_session.query(LlmTask).filter(LlmTask.id == turn.llm_task_id).one()
    assert persisted_message.status == MessageProcessingStatus.FAILED.value
    assert persisted_message.error == "provider exploded"
    assert task.status == LlmTaskStatus.FAILED.value
    assert task.error_type == "RuntimeError"
    assert task.error_message == "provider exploded"


def test_detached_turn_failure_supports_message_less_turns(
    db_session,
    db_session_factory,
    test_user,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Message-less runtime failure",
        session_type="knowledge_chat",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    assert session.id is not None
    session_id = int(session.id)

    with db_session_factory() as prepare_db:
        loaded_session = prepare_db.query(ChatSession).filter(ChatSession.id == session_id).one()
        turn = chat_turn_runtime.snapshot_detached_chat_turn(
            loaded_session,
            message_id=None,
            source="test",
            task_id=None,
        )
        turn, tracker = chat_turn_runtime.start_detached_chat_turn(
            prepare_db,
            turn=turn,
            lifecycle=TEST_LIFECYCLE,
            input_json={"chat_session_id": turn.session_id},
        )
        chat_turn_runtime.mark_detached_chat_turn_running(
            prepare_db,
            turn=turn,
            tracker=tracker,
            lifecycle=TEST_LIFECYCLE,
        )

    chat_turn_runtime.persist_detached_turn_failure(
        session_factory=db_session_factory,
        tracker=tracker,
        lifecycle=TEST_LIFECYCLE,
        message_id=None,
        error=RuntimeError("initial turn failed"),
        mark_message_failed=None,
    )

    db_session.expire_all()
    task = db_session.query(LlmTask).filter(LlmTask.user_id == test_user.id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert task.error_type == "RuntimeError"
    assert task.error_message == "initial turn failed"
