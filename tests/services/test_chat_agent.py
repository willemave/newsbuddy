import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from sqlalchemy.orm import object_session

from app.core.settings import get_settings
from app.models.contracts import ContentType, LlmTaskStatus, MessageProcessingStatus
from app.models.db import ChatMessage, ChatSession, Content, LlmTask
from app.queries.chat_read_models import extract_messages_for_display
from app.services import chat_agent
from app.services.chat_agent import (
    ARTICLE_CHAT_TURN_SPEC,
    ChatDeps,
    _build_chat_deps,
    _build_context_prompt_parts,
    _build_run_user_prompt,
    _dump_messages_json,
    build_article_context,
    create_processing_message,
    generate_initial_suggestions,
    load_message_history,
    save_messages,
)
from app.services.chat_turn_queue import build_chat_turn_context
from app.services.chat_turn_runtime import ChatUsageSnapshot, QueuedChatTurnOutcome
from app.services.sandbox_runtime import (
    LocalPersonalLibrarySandboxSession,
    SandboxRuntimeUnavailableError,
)


def test_build_article_context_includes_full_transcript_with_budget(db_session) -> None:
    transcript = "a" * 5000
    content = Content(content_type=ContentType.PODCAST.value, url="https://example.com")
    content.content_metadata = {"transcript": transcript}
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    context = build_article_context(
        db_session,
        content,
        include_full_text=True,
        max_tokens=5000,
    )

    assert context is not None
    assert transcript in context


def test_build_article_context_prefers_summary_over_full_text_when_requested(db_session) -> None:
    content_text = "b" * 5000
    content = Content(content_type=ContentType.ARTICLE.value, url="https://example.com")
    content.content_metadata = {
        "content": content_text,
        "summary": {
            "overview": "Overview text",
            "bullet_points": [
                {"text": "Point one", "category": "key_finding"},
                {"text": "Point two", "category": "methodology"},
                {"text": "Point three", "category": "conclusion"},
            ],
            "quotes": [{"text": "Quote text", "context": "Author"}],
            "topics": ["AI", "Productivity"],
            "questions": ["What changes next?"],
            "counter_arguments": ["Skeptics argue this is premature."],
            "classification": "to_read",
        },
        "summary_kind": "long_structured",
        "summary_version": 1,
    }
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    context = build_article_context(
        db_session,
        content,
        include_full_text=False,
        max_tokens=5000,
    )

    assert context is not None
    assert "Overview text" in context
    assert "Point one" in context
    assert "Quote text" in context
    assert "Skeptics argue this is premature." in context
    assert content_text not in context


def test_build_article_context_falls_back_to_summary_when_budget_exceeded(db_session) -> None:
    content_text = "c" * 5000
    content = Content(content_type=ContentType.ARTICLE.value, url="https://example.com")
    content.content_metadata = {
        "content": content_text,
        "summary": {
            "overview": "Short overview",
            "bullet_points": [
                {"text": "Point one", "category": "key_finding"},
                {"text": "Point two", "category": "methodology"},
                {"text": "Point three", "category": "conclusion"},
            ],
            "quotes": [],
            "topics": ["AI"],
        },
        "summary_kind": "long_structured",
        "summary_version": 1,
    }
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    context = build_article_context(
        db_session,
        content,
        include_full_text=True,
        max_tokens=50,
    )

    assert context is not None
    assert "Short overview" in context
    assert content_text not in context


def test_build_chat_deps_prefers_session_context_snapshot(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Article title",
    )
    content.content_metadata = {
        "content": "full article body",
        "summary": {"overview": "Overview text", "bullet_points": [{"text": "Point one"}]},
    }
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    session = ChatSession(
        user_id=123,
        content_id=content.id,
        title="News chat",
        session_type="article_brain",
        context_snapshot="News bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-6",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.article_context == "News bullets:\n- Bullet A\n- Bullet B"
    assert deps.context_label == "Session Context"
    assert deps.has_content is False
    assert not any(isinstance(value, (ChatSession, Content)) for value in vars(deps).values())
    assert "Overview text" not in deps.article_context
    assert "full article body" not in deps.article_context


def test_build_chat_deps_uses_processed_content_for_knowledge_chat(db_session) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/knowledge-article",
        title="Knowledge article",
        content_metadata={"content": "full processed article body"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    session = ChatSession(
        user_id=123,
        content_id=content.id,
        title="Knowledge chat",
        session_type="knowledge_chat",
        context_snapshot="Compact pre-processing snapshot",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.has_content is True
    assert not any(isinstance(value, (ChatSession, Content)) for value in vars(deps).values())
    assert deps.context_label == "Article Context"
    assert deps.article_context is not None
    assert "full processed article body" in deps.article_context
    assert "Compact pre-processing snapshot" not in deps.article_context


def test_article_chat_enables_sandboxed_bash() -> None:
    assert ARTICLE_CHAT_TURN_SPEC.tool_policy["execute_bash"] is True


def test_local_chat_sandbox_rejects_bash(tmp_path: Path) -> None:
    session = LocalPersonalLibrarySandboxSession(library_root=tmp_path)

    with pytest.raises(SandboxRuntimeUnavailableError, match="requires the isolated E2B"):
        session.execute_bash("touch ../escaped", timeout_seconds=5)

    assert not (tmp_path.parent / "escaped").exists()


def test_build_context_prompt_parts_marks_snapshot_as_reference_material() -> None:
    parts = _build_context_prompt_parts(
        None,
        None,
        "News bullets:\n- Bullet A\n- Bullet B",
        "Session Context",
    )

    rendered = "\n".join(parts)
    assert "Provided reference context is available below." in rendered
    assert "do not ask the user to paste it again" in rendered
    assert "Session Context:\nNews bullets:\n- Bullet A\n- Bullet B" in rendered


def test_build_run_user_prompt_includes_snapshot_context() -> None:
    deps = ChatDeps(
        session_id=123,
        user_id=123,
        has_context_snapshot=True,
        article_context="News bullets:\n- Bullet A\n- Bullet B",
        context_label="Session Context",
    )

    prompt = _build_run_user_prompt("Dig deeper into these news bullets.", deps)

    assert "Use the provided session context below as the source material" in prompt
    assert "Session Context:\nNews bullets:\n- Bullet A\n- Bullet B" in prompt
    assert prompt.endswith("User request:\nDig deeper into these news bullets.")


def test_dump_messages_json_restores_user_visible_prompt(db_session) -> None:
    session = ChatSession(
        user_id=123,
        title="News chat",
        session_type="article_brain",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-6",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    assert session.id is not None

    create_processing_message(
        db_session,
        session.id,
        (
            "Use the provided session context below as the source material.\n\n"
            "Session Context:\n- Bullet A"
        ),
    )
    messages = load_message_history(db_session, session.id, completed_only=False)

    stored_json = _dump_messages_json(
        messages,
        display_user_prompt="Dig deeper into these news bullets.",
    )
    payload = json.loads(stored_json)

    assert payload[0]["parts"][0]["content"] == "Dig deeper into these news bullets."
    assert "Session Context" not in payload[0]["parts"][0]["content"]


def test_dump_messages_json_rewrites_first_user_prompt_not_system_part() -> None:
    stored_json = _dump_messages_json(
        [
            ModelRequest(
                parts=[
                    SystemPromptPart(content="internal system prompt"),
                    UserPromptPart(content="Use the provided session context below..."),
                ]
            )
        ],
        display_user_prompt="What changed?",
    )
    payload = json.loads(stored_json)

    assert payload[0]["parts"][0]["content"] == "internal system prompt"
    assert payload[0]["parts"][1]["content"] == "What changed?"


def test_load_message_history_defaults_to_completed_and_can_exclude_active_row(
    db_session,
) -> None:
    session = ChatSession(
        user_id=123,
        title="History chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.4",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    assert session.id is not None
    completed = save_messages(
        db_session,
        session.id,
        [
            ModelRequest(parts=[UserPromptPart(content="Prior question")]),
            ModelResponse(parts=[TextPart(content="Prior answer")]),
        ],
    )
    active = create_processing_message(db_session, session.id, "Current question")

    history = load_message_history(
        db_session,
        session.id,
        exclude_message_id=active.id,
    )
    serialized = json.loads(_dump_messages_json(history))

    assert completed.id is not None
    assert len(serialized) == 2
    assert serialized[0]["parts"][0]["content"] == "Prior question"
    assert "Current question" not in json.dumps(serialized)


def test_generate_initial_suggestions_persists_assistant_only_transcript(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Article title",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    session = ChatSession(
        user_id=test_user.id,
        content_id=content.id,
        title="Article chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.4",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    user_id = int(test_user.id)
    assert session.id is not None
    session_id = int(session.id)
    db_session.close()

    agent_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    usage_calls: list[tuple[object, ChatUsageSnapshot, int, int | None, str]] = []

    async def _fake_run_in_threadpool(*args, **kwargs):
        assert not db_session.in_transaction()
        assert object_session(session) is None
        agent_calls.append((args, kwargs))
        return SimpleNamespace(
            output="Here are a few useful directions.",
            new_messages=lambda: [
                ModelResponse(
                    parts=[
                        ToolCallPart(tool_name="search_personal_library", args={}),
                        TextPart(content="Here are a few useful directions."),
                    ]
                )
            ],
        )

    def _fake_build_chat_deps(_db, current_session, **_kwargs):  # noqa: ANN001
        assert _db is not db_session
        return ChatDeps(
            session_id=int(current_session.id),
            user_id=int(current_session.user_id),
            content_id=current_session.content_id,
            has_content=True,
            article_context="Article context",
        )

    def _capture_usage(result, snapshot, session_id, message_id, context):  # noqa: ANN001
        usage_calls.append((result, snapshot, session_id, message_id, context))

    monkeypatch.setattr(
        chat_agent,
        "_build_chat_deps",
        _fake_build_chat_deps,
    )
    monkeypatch.setattr(chat_agent, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(chat_agent, "_log_chat_usage", _capture_usage)
    monkeypatch.setattr(chat_agent, "run_in_threadpool", _fake_run_in_threadpool)

    result = asyncio.run(
        generate_initial_suggestions(
            session_id,
            source="queue",
            task_id=77,
        )
    )

    assert result is not None
    assert result.output_text == "Here are a few useful directions."
    display_messages = extract_messages_for_display(
        db_session,
        session_id,
        user_id=user_id,
    )
    assert [message.role.value for message in display_messages] == ["assistant"]
    assert display_messages[0].content == "Here are a few useful directions."
    assert "You are starting a new conversation" not in display_messages[0].content

    assert len(agent_calls) == 1
    args, _kwargs = agent_calls[0]
    assert args[1] == "openai:gpt-5.4"
    assert args[2] == chat_agent.INITIAL_QUESTIONS_PROMPT
    assert args[4] == []
    assert len(usage_calls) == 1
    _, usage_snapshot, usage_session_id, usage_message_id, usage_context = usage_calls[0]
    assert usage_snapshot.user_id == user_id
    assert usage_snapshot.model == "openai:gpt-5.4"
    assert usage_session_id == session_id
    assert usage_message_id is None
    assert usage_context == "initial_suggestions"

    task = db_session.query(LlmTask).filter(LlmTask.user_id == user_id).one()
    assert task.status == LlmTaskStatus.COMPLETED.value
    assert task.input_json["operation"] == "initial_suggestions"
    assert task.input_json["queue_task_id"] == 77
    assert task.output_json["chat_session_id"] == session_id
    assert task.output_json["message_id"] == display_messages[0].source_message_id
    assert task.output_json["tool_names"] == ["search_personal_library"]


def test_generate_initial_suggestions_skips_sessions_without_context(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Empty chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.4",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    user_id = int(test_user.id)
    assert session.id is not None
    session_id = int(session.id)
    db_session.close()

    async def _unexpected_agent_call(*_args, **_kwargs):
        raise AssertionError("No-context suggestions must not call the model")

    monkeypatch.setattr(chat_agent, "run_in_threadpool", _unexpected_agent_call)

    result = asyncio.run(generate_initial_suggestions(session_id))

    assert result is None
    assert db_session.query(LlmTask).filter(LlmTask.user_id == user_id).count() == 0
    assert db_session.query(ChatMessage).filter(ChatMessage.session_id == session_id).count() == 0


def test_generate_initial_suggestions_records_failure_without_partial_message(
    db_session,
    test_user,
    monkeypatch,
    tmp_path,
) -> None:
    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/failing-article",
        title="Failing article",
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    session = ChatSession(
        user_id=test_user.id,
        content_id=content.id,
        title="Failing article chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.4",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    user_id = int(test_user.id)
    assert session.id is not None
    session_id = int(session.id)
    db_session.close()
    sandbox_closed: list[bool] = []
    sandbox = LocalPersonalLibrarySandboxSession(library_root=tmp_path)
    monkeypatch.setattr(sandbox, "close", lambda: sandbox_closed.append(True))

    monkeypatch.setattr(
        chat_agent,
        "_build_chat_deps",
        lambda _db, current_session, **_kwargs: ChatDeps(
            session_id=int(current_session.id),
            user_id=int(current_session.user_id),
            content_id=current_session.content_id,
            has_content=True,
            article_context="Article context",
            sandbox_session=sandbox,
        ),
    )
    monkeypatch.setattr(chat_agent, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_agent,
        "_log_chat_usage",
        lambda *_args, **_kwargs: pytest.fail("Failed calls must not record usage"),
    )

    async def _failing_agent_call(*_args, **_kwargs):
        assert not db_session.in_transaction()
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chat_agent, "run_in_threadpool", _failing_agent_call)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(generate_initial_suggestions(session_id))

    assert sandbox_closed == [True]
    assert db_session.query(ChatMessage).filter(ChatMessage.session_id == session_id).count() == 0
    task = db_session.query(LlmTask).filter(LlmTask.user_id == user_id).one()
    assert task.status == LlmTaskStatus.FAILED.value
    assert task.error_type == "RuntimeError"
    assert task.error_message == "provider unavailable"


def test_build_chat_deps_prepares_personal_library_runtime(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)
    monkeypatch.setattr(settings, "chat_sandbox_provider", "local")

    content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Library Article",
        source="Example Source",
        content_metadata={
            "content": "Saved content body",
            "summary": {"full_markdown": "# Library Article\n\nSaved summary"},
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    session = ChatSession(
        user_id=test_user.id,
        content_id=content.id,
        title="Library Chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.personal_library_error is None
    assert deps.sandbox_session is not None
    files = deps.sandbox_session.list_files()
    assert "library-article" in files
    deps.sandbox_session.close()


def test_build_chat_deps_keeps_local_personal_library_read_only(
    db_session,
    test_user,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_root", tmp_path / "personal_markdown")
    monkeypatch.setattr(settings, "personal_markdown_enabled", False)
    monkeypatch.setattr(settings, "chat_sandbox_provider", "local")
    session = ChatSession(
        user_id=test_user.id,
        title="Research chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.sandbox_session is not None
    with pytest.raises(SandboxRuntimeUnavailableError, match="requires the isolated E2B"):
        deps.sandbox_session.execute_bash("printf 'available'", timeout_seconds=5)
    deps.sandbox_session.close()


def test_build_chat_deps_skips_personal_library_sync_when_sandbox_disabled(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "personal_markdown_enabled", True)
    monkeypatch.setattr(settings, "chat_sandbox_provider", "disabled")

    sync_calls: list[int] = []

    def _unexpected_sync(_db, *, user_id: int):  # noqa: ANN001
        sync_calls.append(user_id)
        raise AssertionError("personal markdown sync should not run when sandbox is disabled")

    monkeypatch.setattr(chat_agent, "sync_personal_markdown_library_for_user", _unexpected_sync)

    session = ChatSession(
        user_id=test_user.id,
        title="No Sandbox Chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    deps = _build_chat_deps(db_session, session, include_full_text=True)

    assert deps.sandbox_session is None
    assert deps.personal_library_error is None
    assert sync_calls == []


def test_run_chat_turn_builds_deps_with_library_tools_enabled(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Council-capable Chat",
        session_type="knowledge_chat",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    captured_flags: list[bool] = []

    def _fake_build_chat_deps(
        db,
        current_session,
        include_full_text: bool = False,
        *,
        include_library_tools: bool = True,
    ) -> ChatDeps:
        del db, include_full_text
        captured_flags.append(include_library_tools)
        return ChatDeps(
            session_id=int(current_session.id),
            user_id=int(current_session.user_id),
            content_id=current_session.content_id,
            article_context=None,
        )

    async def _fake_run_in_threadpool(*_args, **_kwargs):
        return SimpleNamespace(
            output="Mocked assistant reply",
            new_messages=lambda: [
                ModelResponse(parts=[TextPart(content="Mocked assistant reply")])
            ],
        )

    monkeypatch.setattr(chat_agent, "_build_chat_deps", _fake_build_chat_deps)
    monkeypatch.setattr(chat_agent, "load_message_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_agent, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(chat_agent, "_log_chat_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_agent, "run_in_threadpool", _fake_run_in_threadpool)

    result = asyncio.run(chat_agent.run_chat_turn(db_session, session, "Use the personal library."))

    assert result.output_text == "Mocked assistant reply"
    assert captured_flags == [True]


def test_process_message_async_persists_completion_usage_and_ledger(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    session = ChatSession(
        user_id=test_user.id,
        title="Detached article chat",
        session_type="knowledge_chat",
        context_snapshot="Saved context",
        llm_provider="openai",
        llm_model="openai:gpt-5.6-terra",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    assert session.id is not None
    session_id = int(session.id)
    message = create_processing_message(db_session, session_id, "What changed?")
    assert message.id is not None
    message_id = int(message.id)
    usage_calls: list[tuple[int, int | None, str]] = []

    turn_context = build_chat_turn_context(
        session,
        visible_session_id=session_id,
        user_prompt="What changed?",
        kind="article",
        source="queue",
    )
    monkeypatch.setattr(
        chat_agent,
        "_build_chat_deps_from_values",
        lambda _db, **kwargs: ChatDeps(
            session_id=kwargs["session_id"],
            user_id=kwargs["user_id"],
            content_id=kwargs["content_id"],
            has_context_snapshot=True,
            article_context="Saved context",
            context_label="Session Context",
        ),
    )
    monkeypatch.setattr(chat_agent, "load_message_history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_agent, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_agent,
        "_log_chat_usage",
        lambda _result, _snapshot, sid, mid, context: usage_calls.append((sid, mid, context)),
    )

    async def _fake_run_in_threadpool(_func, _model, prompt, deps, history, **_kwargs):
        assert prompt in {"What changed?", "Will the old worker win?"}
        assert deps.session_id == session_id
        assert deps.user_id == test_user.id
        assert history == []
        messages = [
            ModelRequest(parts=[UserPromptPart(content="model-facing prompt")]),
            ModelResponse(parts=[TextPart(content="A detached answer")]),
        ]
        return SimpleNamespace(
            output="A detached answer",
            new_messages=lambda: messages,
        )

    monkeypatch.setattr(chat_agent, "run_in_threadpool", _fake_run_in_threadpool)

    asyncio.run(
        chat_agent.process_message_async(
            session_id,
            message_id,
            "What changed?",
            turn_context=turn_context,
            stream_generation=0,
            ensure_lease=lambda: True,
        )
    )

    db_session.expire_all()
    persisted_message = db_session.query(ChatMessage).filter_by(id=message_id).one()
    ledger = (
        db_session.query(LlmTask)
        .filter(LlmTask.workflow_key == "chat.article.v1")
        .order_by(LlmTask.id.desc())
        .first()
    )
    assert persisted_message.status == MessageProcessingStatus.COMPLETED.value
    assert json.loads(persisted_message.message_list)[0]["parts"][0]["content"] == "What changed?"
    assert usage_calls == [(session_id, message_id, "async")]
    assert ledger is not None
    assert ledger.status == LlmTaskStatus.COMPLETED.value
    assert ledger.output_json["message_id"] == message_id

    superseded_message = create_processing_message(
        db_session,
        session_id,
        "Will the old worker win?",
    )
    assert superseded_message.id is not None
    superseded_context = build_chat_turn_context(
        session,
        visible_session_id=session_id,
        user_prompt="Will the old worker win?",
        kind="article",
        source="queue",
    )
    outcome = asyncio.run(
        chat_agent.process_message_async(
            session_id,
            superseded_message.id,
            "Will the old worker win?",
            turn_context=superseded_context,
            stream_generation=1,
            ensure_lease=lambda: False,
        )
    )

    db_session.expire_all()
    superseded_row = db_session.get(ChatMessage, superseded_message.id)
    assert superseded_row is not None
    cancelled_ledger = (
        db_session.query(LlmTask)
        .filter(LlmTask.workflow_key == "chat.article.v1")
        .order_by(LlmTask.id.desc())
        .first()
    )
    assert outcome == QueuedChatTurnOutcome.OWNERSHIP_LOST
    assert superseded_row.status == MessageProcessingStatus.PROCESSING.value
    assert superseded_row.stream_generation == 1
    assert cancelled_ledger is not None
    assert cancelled_ledger.status == LlmTaskStatus.CANCELLED.value
