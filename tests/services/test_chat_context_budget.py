from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.services.chat_context_budget import (
    HISTORICAL_TOOL_RESULT_MAX_TOKENS,
    available_chat_history_tokens,
    estimate_tokens,
    parse_historical_message_row,
    trim_message_history_to_token_budget,
)


def test_history_budget_keeps_newest_complete_tool_turn() -> None:
    old_turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="old question")]),
        ModelResponse(parts=[TextPart(content="old answer")]),
    ]
    new_turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="new question")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="execute_bash",
                    args={"command": "pwd"},
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="execute_bash",
                    content="/data/workspace",
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="new answer")]),
    ]
    new_turn_tokens = estimate_tokens(ModelMessagesTypeAdapter.dump_json(new_turn).decode("utf-8"))

    selected = trim_message_history_to_token_budget(
        [*old_turn, *new_turn],
        max_tokens=new_turn_tokens,
    )

    assert selected == new_turn


def test_historical_tool_output_has_its_own_bound() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="execute_bash",
                    content="x" * 100_000,
                    tool_call_id="call-1",
                )
            ]
        )
    ]
    raw = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")

    parsed = parse_historical_message_row(raw)

    part = parsed[0].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, str)
    assert estimate_tokens(part.content) <= HISTORICAL_TOOL_RESULT_MAX_TOKENS + 1


def test_fixed_request_context_is_reserved_before_history() -> None:
    assert (
        available_chat_history_tokens(
            static_system_prompt="system",
            dynamic_system_prompt="x" * 300_000,
            user_prompt="question",
        )
        == 0
    )
