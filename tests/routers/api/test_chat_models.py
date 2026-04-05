"""Tests for chat API models."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.api.chat import (
    AssistantScreenContextDto,
    ChatMessageDisplayType,
    ChatMessageDto,
    ChatMessageRole,
    CouncilSelectRequest,
    CouncilStartRequest,
)


def test_chat_message_role_includes_tool() -> None:
    """ChatMessageRole should accept tool messages."""
    assert ChatMessageRole("tool") is ChatMessageRole.TOOL


def test_chat_message_display_type_includes_process_summary() -> None:
    """ChatMessageDisplayType should accept process-summary rows."""
    assert ChatMessageDisplayType("process_summary") is ChatMessageDisplayType.PROCESS_SUMMARY


def test_chat_message_dto_defaults_to_standard_display_type() -> None:
    """ChatMessageDto should default to the standard display type."""
    message = ChatMessageDto(
        id=1,
        session_id=2,
        role=ChatMessageRole.ASSISTANT,
        content="Answer",
        timestamp=datetime.now(UTC),
    )

    assert message.display_type is ChatMessageDisplayType.MESSAGE
    assert message.process_label is None


def test_chat_message_dto_accepts_council_candidates() -> None:
    """ChatMessageDto should preserve council candidate metadata."""
    message = ChatMessageDto.model_validate(
        {
            "id": 9,
            "session_id": 2,
            "role": "assistant",
            "content": "Analyst branch",
            "timestamp": datetime.now(UTC),
            "council_candidates": [
                {
                    "persona_id": "analyst",
                    "persona_name": "Analyst",
                    "child_session_id": 11,
                    "content": "Candidate reply",
                    "status": "completed",
                    "order": 0,
                }
            ],
            "active_council_child_session_id": 11,
        }
    )

    assert message.council_candidates[0].persona_name == "Analyst"
    assert message.active_council_child_session_id == 11


def test_council_request_models_validate_basic_payloads() -> None:
    """Council request DTOs should accept the expected payload shape."""
    start_request = CouncilStartRequest.model_validate({"message": "Debate this topic."})
    select_request = CouncilSelectRequest.model_validate({"child_session_id": 12})

    assert start_request.message == "Debate this topic."
    assert select_request.child_session_id == 12


def test_assistant_screen_context_truncates_visible_content_ids() -> None:
    """AssistantScreenContextDto should cap visible IDs to the supported limit."""
    context = AssistantScreenContextDto.model_validate(
        {
            "screen_type": "long_form",
            "visible_content_ids": list(range(1, 26)),
        }
    )

    assert context.visible_content_ids == list(range(1, 13))


def test_assistant_screen_context_preserves_dto_schema_name() -> None:
    """Assistant screen context should keep the DTO schema component name."""
    assert AssistantScreenContextDto.model_json_schema()["title"] == "AssistantScreenContextDto"
