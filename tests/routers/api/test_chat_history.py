import json

from app.models.schema import ChatMessage, ChatSession
from app.routers.api.chat import (
    _extract_last_message_preview,
    _extract_messages_for_display,
)


def _multi_step_message_list() -> str:
    usage = {
        "input_tokens": 10,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 10,
        "input_audio_tokens": 0,
        "cache_audio_read_tokens": 0,
        "output_audio_tokens": 0,
        "details": {
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "input_tokens": 10,
            "output_tokens": 10,
        },
    }
    payload = [
        {
            "parts": [
                {
                    "content": "Dig deeper into these digest bullets.",
                    "timestamp": "2026-03-08T17:05:02.295881Z",
                    "part_kind": "user-prompt",
                }
            ],
            "timestamp": "2026-03-08T17:05:02.296029Z",
            "instructions": None,
            "kind": "request",
            "run_id": "run-1",
            "metadata": None,
        },
        {
            "parts": [
                {
                    "content": "I'll research each of these digest bullets first.",
                    "id": None,
                    "provider_name": None,
                    "provider_details": None,
                    "part_kind": "text",
                },
                {
                    "tool_name": "exa_web_search",
                    "args": {"query": "Tesla robotaxi"},
                    "tool_call_id": "tool-1",
                    "id": None,
                    "provider_name": None,
                    "provider_details": None,
                    "part_kind": "tool-call",
                },
            ],
            "usage": usage,
            "model_name": "claude-opus-4-5-20251101",
            "timestamp": "2026-03-08T17:05:07.394654Z",
            "kind": "response",
            "provider_name": "anthropic",
            "provider_url": "https://api.anthropic.com",
            "provider_details": None,
            "finish_reason": "tool_call",
            "run_id": "run-1",
            "metadata": None,
        },
        {
            "parts": [
                {
                    "tool_name": "exa_web_search",
                    "content": "Found 6 relevant sources.",
                    "tool_call_id": "tool-1",
                    "metadata": None,
                    "timestamp": "2026-03-08T17:05:08.983851Z",
                    "part_kind": "tool-return",
                }
            ],
            "timestamp": "2026-03-08T17:05:16.456414Z",
            "instructions": None,
            "kind": "request",
            "run_id": "run-1",
            "metadata": None,
        },
        {
            "parts": [
                {
                    "content": "Final deep-dive answer.",
                    "id": None,
                    "provider_name": None,
                    "provider_details": None,
                    "part_kind": "text",
                }
            ],
            "usage": usage,
            "model_name": "claude-opus-4-5-20251101",
            "timestamp": "2026-03-08T17:06:38.689805Z",
            "kind": "response",
            "provider_name": "anthropic",
            "provider_url": "https://api.anthropic.com",
            "provider_details": None,
            "finish_reason": "stop",
            "run_id": "run-1",
            "metadata": None,
        },
    ]
    return json.dumps(payload)


def test_extract_messages_for_display_hides_intermediate_agent_scaffolding(db_session) -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    db_message = ChatMessage(
        session_id=session.id,
        message_list=_multi_step_message_list(),
        status="completed",
    )
    db_session.add(db_message)
    db_session.commit()

    display_messages = _extract_messages_for_display(db_session, session.id)

    assert [message.role.value for message in display_messages] == ["user", "tool", "assistant"]
    assert display_messages[0].content == "Dig deeper into these digest bullets."
    assert display_messages[1].display_type.value == "process_summary"
    assert display_messages[1].content == "Thinking • Searched the web and reviewed sources"
    assert display_messages[2].content == "Final deep-dive answer."


def test_extract_messages_for_display_omits_process_summary_for_simple_turn(db_session) -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    payload = json.dumps(
        [
            {
                "parts": [
                    {
                        "content": "What changed?",
                        "timestamp": "2026-03-08T17:05:02.295881Z",
                        "part_kind": "user-prompt",
                    }
                ],
                "timestamp": "2026-03-08T17:05:02.296029Z",
                "instructions": None,
                "kind": "request",
                "run_id": "run-1",
                "metadata": None,
            },
            {
                "parts": [
                    {
                        "content": "Here is the direct answer.",
                        "id": None,
                        "provider_name": None,
                        "provider_details": None,
                        "part_kind": "text",
                    }
                ],
                "usage": {},
                "model_name": "claude-opus-4-5-20251101",
                "timestamp": "2026-03-08T17:06:38.689805Z",
                "kind": "response",
                "provider_name": "anthropic",
                "provider_url": "https://api.anthropic.com",
                "provider_details": None,
                "finish_reason": "stop",
                "run_id": "run-1",
                "metadata": None,
            },
        ]
    )

    db_message = ChatMessage(
        session_id=session.id,
        message_list=payload,
        status="completed",
    )
    db_session.add(db_message)
    db_session.commit()

    display_messages = _extract_messages_for_display(db_session, session.id)

    assert [message.role.value for message in display_messages] == ["user", "assistant"]
    assert all(message.display_type.value == "message" for message in display_messages)


def test_extract_last_message_preview_prefers_final_assistant_text(db_session) -> None:
    session = ChatSession(
        user_id=123,
        title="Digest chat",
        session_type="daily_digest_brain",
        llm_provider="anthropic",
        llm_model="anthropic:claude-opus-4-5-20251101",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    db_message = ChatMessage(
        session_id=session.id,
        message_list=_multi_step_message_list(),
        status="completed",
    )
    db_session.add(db_message)
    db_session.commit()
    db_session.refresh(db_message)

    preview, role = _extract_last_message_preview(db_message)

    assert preview == "Final deep-dive answer."
    assert role == "assistant"
