"""Approximate context budgeting shared by chat prompt construction."""

from __future__ import annotations

import json
import math

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)

CONTEXT_WINDOW_TOKENS = 64_000
SYSTEM_AND_ARTICLE_BUDGET_RATIO = 0.75
TOKEN_CHARS_PER_TOKEN = 4
CHAT_OUTPUT_RESERVE_TOKENS = 8_000
CHAT_TOOL_SCHEMA_RESERVE_TOKENS = 4_000
CHAT_HISTORY_MAX_TOKENS = 16_000
HISTORICAL_TOOL_RESULT_MAX_TOKENS = 2_000


def estimate_tokens(text: str | None) -> int:
    """Approximate token count using character length."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / TOKEN_CHARS_PER_TOKEN))


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate text to an approximate token budget."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * TOKEN_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def available_chat_history_tokens(
    *,
    static_system_prompt: str,
    dynamic_system_prompt: str,
    user_prompt: str,
) -> int:
    """Reserve output and tool schemas, then give history the remaining input budget."""
    fixed_input_tokens = estimate_tokens(
        f"{static_system_prompt}\n{dynamic_system_prompt}\n{user_prompt}"
    )
    available = (
        CONTEXT_WINDOW_TOKENS
        - CHAT_OUTPUT_RESERVE_TOKENS
        - CHAT_TOOL_SCHEMA_RESERVE_TOKENS
        - fixed_input_tokens
    )
    return max(0, min(CHAT_HISTORY_MAX_TOKENS, available))


def parse_historical_message_row(raw_json: str) -> list[ModelMessage]:
    """Bound historical tool output before validating a complete persisted turn."""
    payload = json.loads(raw_json)
    if not isinstance(payload, list):
        return []
    for message in payload:
        if not isinstance(message, dict):
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("part_kind") not in {
                "tool-return",
                "builtin-tool-return",
            }:
                continue
            content = part.get("content")
            serialized = (
                content
                if isinstance(content, str)
                else json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
            )
            if estimate_tokens(serialized) > HISTORICAL_TOOL_RESULT_MAX_TOKENS:
                part["content"] = truncate_to_token_budget(
                    serialized,
                    HISTORICAL_TOOL_RESULT_MAX_TOKENS,
                )
    return ModelMessagesTypeAdapter.validate_python(payload)


def trim_message_history_to_token_budget(
    messages: list[ModelMessage],
    *,
    max_tokens: int,
) -> list[ModelMessage]:
    """Keep newest whole user turns without splitting their tool-call sequence."""
    if max_tokens <= 0 or not messages:
        return []
    turns = _complete_history_turns(messages)
    selected: list[list[ModelMessage]] = []
    used_tokens = 0
    for turn in reversed(turns):
        turn_tokens = estimate_tokens(ModelMessagesTypeAdapter.dump_json(turn).decode("utf-8"))
        if used_tokens + turn_tokens > max_tokens:
            break
        selected.append(turn)
        used_tokens += turn_tokens
    return [message for turn in reversed(selected) for message in turn]


def _complete_history_turns(messages: list[ModelMessage]) -> list[list[ModelMessage]]:
    turns: list[list[ModelMessage]] = []
    current: list[ModelMessage] = []
    for message in messages:
        starts_user_turn = isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        )
        if starts_user_turn and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns
