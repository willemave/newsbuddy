import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from app.core.settings import get_settings
from app.models.contracts import ContentType
from app.models.db import ChatSession, Content
from app.routers.api.chat import _extract_messages_for_display
from app.services import chat_agent
from app.services.chat_agent import (
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
    assert deps.content is None
    assert "Overview text" not in deps.article_context
    assert "full article body" not in deps.article_context


def test_build_context_prompt_parts_marks_snapshot_as_reference_material() -> None:
    session = ChatSession(
        user_id=123,
        title="News chat",
        session_type="article_brain",
        context_snapshot="News bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-6",
    )

    parts = _build_context_prompt_parts(
        None,
        session,
        "News bullets:\n- Bullet A\n- Bullet B",
        "Session Context",
    )

    rendered = "\n".join(parts)
    assert "Provided reference context is available below." in rendered
    assert "do not ask the user to paste it again" in rendered
    assert "Session Context:\nNews bullets:\n- Bullet A\n- Bullet B" in rendered


def test_build_run_user_prompt_includes_snapshot_context() -> None:
    session = ChatSession(
        user_id=123,
        title="News chat",
        session_type="article_brain",
        context_snapshot="News bullets:\n- Bullet A\n- Bullet B",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-6",
    )

    deps = ChatDeps(
        session=session,
        content=None,
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

    async def _fake_run_in_threadpool(*_args, **_kwargs):
        return SimpleNamespace(
            output="Here are a few useful directions.",
            all_messages=[],
            tool_calls=[],
        )

    monkeypatch.setattr(
        chat_agent,
        "_build_chat_deps",
        lambda _db, current_session, **_kwargs: ChatDeps(
            session=current_session,
            content=content,
            article_context="Article context",
        ),
    )
    monkeypatch.setattr(chat_agent, "resolve_effective_api_key", lambda **_kwargs: None)
    monkeypatch.setattr(chat_agent, "_log_chat_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_agent, "run_in_threadpool", _fake_run_in_threadpool)

    assert session.id is not None
    result = asyncio.run(generate_initial_suggestions(db_session, session))

    assert result is not None
    display_messages = _extract_messages_for_display(db_session, session.id)
    assert [message.role.value for message in display_messages] == ["assistant"]
    assert display_messages[0].content == "Here are a few useful directions."
    assert "You are starting a new conversation" not in display_messages[0].content


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
        llm_model="openai:gpt-5.5",
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
        llm_model="openai:gpt-5.5",
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
        llm_model="openai:gpt-5.5",
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
            session=current_session,
            content=None,
            article_context=None,
        )

    async def _fake_run_in_threadpool(*_args, **_kwargs):
        return SimpleNamespace(
            output="Mocked assistant reply",
            all_messages=[],
            tool_calls=[],
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
