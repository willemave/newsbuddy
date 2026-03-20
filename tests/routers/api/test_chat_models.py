"""Tests for chat API models."""

from __future__ import annotations

from datetime import UTC, datetime

from app.routers.api.chat_models import (
    AssistantScreenContextDto,
    ChatMessageDisplayType,
    ChatMessageDto,
    ChatMessageRole,
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


def test_assistant_screen_context_truncates_visible_content_ids() -> None:
    """AssistantScreenContextDto should cap visible IDs to the supported limit."""
    context = AssistantScreenContextDto.model_validate(
        {
            "screen_type": "long_form",
            "visible_content_ids": list(range(1, 26)),
        }
    )

    assert context.visible_content_ids == list(range(1, 13))
