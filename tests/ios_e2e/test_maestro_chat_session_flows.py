"""Maestro-backed iOS chat-session end-to-end regressions."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.models.schema import ChatMessage, ChatSession
from app.services.chat_agent import ChatRunResult, save_messages

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


def test_chat_session_council_button_starts_council_and_switches_branches(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    chat_session_factory,
    db_session,
    monkeypatch,
) -> None:
    """Starting council from the in-session composer should switch mocked branch replies."""
    content = create_sample_content(sample_article_long)
    test_user.council_personas = [
        {
            "id": "paul_graham",
            "display_name": "Paul Graham",
            "instruction_prompt": "",
            "sort_order": 0,
        },
        {
            "id": "ben_thompson",
            "display_name": "Ben Thompson",
            "instruction_prompt": "",
            "sort_order": 1,
        },
        {
            "id": "byrne_hobart",
            "display_name": "Byrne Hobart",
            "instruction_prompt": "",
            "sort_order": 2,
        },
    ]
    db_session.commit()
    db_session.refresh(test_user)

    session = chat_session_factory(
        user=test_user,
        content=content,
        title="Existing Chat Session",
        session_type="knowledge_chat",
    )
    save_messages(
        db_session,
        session.id,
        [
            ModelRequest(parts=[UserPromptPart(content="Summarize the article.")]),
            ModelResponse(parts=[TextPart(content="Initial mocked assistant reply.")]),
        ],
        display_user_prompt="Summarize the article.",
    )

    async def _fake_run_chat_turn(db, branch_session, user_prompt, source="chat"):
        del source
        assistant_text = f"{branch_session.council_persona_name} mocked council reply"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, branch_session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
            all_messages=messages,
            tool_calls=[],
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    run_ios_flow(
        "chat_session_start_council.yaml",
        extra_env={
            "CHAT_SESSION_ID": str(session.id),
            "PRIMARY_PERSONA_NAME": "Paul Graham",
            "SECONDARY_PERSONA_NAME": "Ben Thompson",
            "PRIMARY_PERSONA_REPLY": "Paul Graham mocked council reply",
            "SECONDARY_PERSONA_REPLY": "Ben Thompson mocked council reply",
        },
    )

    db_session.expire_all()
    parent_session = db_session.query(ChatSession).filter(ChatSession.id == session.id).one()
    assert parent_session.council_mode is True
    assert parent_session.active_child_session_id is not None


def test_chat_session_council_failure_surfaces_error_banner(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    chat_session_factory,
    db_session,
) -> None:
    """Council start failures should remain visible after transcript messages."""
    content = create_sample_content(sample_article_long)
    session = chat_session_factory(
        user=test_user,
        content=content,
        title="Existing Chat Session",
        session_type="knowledge_chat",
    )
    save_messages(
        db_session,
        session.id,
        [
            ModelRequest(parts=[UserPromptPart(content="Summarize the article.")]),
            ModelResponse(parts=[TextPart(content="Initial mocked assistant reply.")]),
        ],
        display_user_prompt="Summarize the article.",
    )

    run_ios_flow(
        "chat_session_council_error_banner.yaml",
        extra_env={
            "CHAT_SESSION_ID": str(session.id),
            "ERROR_TEXT": "Add at least two experts in Settings before using the council",
        },
    )


def test_chat_session_long_transcript_scroll_preserves_jump_to_latest(
    run_ios_flow,
    test_user,
    chat_session_factory,
    db_session,
    completed_chat_processors_factory,
    monkeypatch,
) -> None:
    """Long transcripts should stay scrollable and preserve jump-to-latest behavior."""
    latest_assistant = "Scroll matrix assistant turn 24"
    older_user = "Scroll matrix user turn 12"
    follow_up = "Scroll matrix follow-up"
    follow_up_reply = "Scroll matrix follow-up reply"
    session = chat_session_factory(
        user=test_user,
        title="Long Transcript Scroll Session",
        session_type="knowledge_chat",
    )
    for turn in range(1, 25):
        save_messages(
            db_session,
            session.id,
            [
                ModelRequest(parts=[UserPromptPart(content=f"Scroll matrix user turn {turn}")]),
                ModelResponse(parts=[TextPart(content=f"Scroll matrix assistant turn {turn}")]),
            ],
            display_user_prompt=f"Scroll matrix user turn {turn}",
        )

    _fake_process_message_async, _fake_process_assistant_turn_async = (
        completed_chat_processors_factory(assistant_reply=follow_up_reply)
    )
    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
    )

    run_ios_flow(
        "chat_session_long_transcript_scroll.yaml",
        extra_env={
            "CHAT_SESSION_ID": str(session.id),
            "LATEST_ASSISTANT": latest_assistant,
            "OLDER_USER": older_user,
            "FOLLOW_UP": follow_up,
            "FOLLOW_UP_REPLY": follow_up_reply,
        },
    )

    db_session.expire_all()
    message = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert message is not None
    assert message.status == "completed"
    assert follow_up in (message.message_list or "")
    assert follow_up_reply in (message.message_list or "")
