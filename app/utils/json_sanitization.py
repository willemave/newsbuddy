"""Helpers for keeping persisted JSON values safe for PostgreSQL text operations."""

from __future__ import annotations

from typing import Any

_ALLOWED_CONTROL_CHARACTERS = {"\n", "\r", "\t"}


def strip_disallowed_control_characters(value: str) -> str:
    """Remove C0 controls that are unsafe or meaningless in persisted metadata."""
    return "".join(
        character
        for character in value
        if ord(character) >= 32 or character in _ALLOWED_CONTROL_CHARACTERS
    )


def sanitize_json_value(value: Any) -> Any:
    """Recursively sanitize JSON strings while preserving literal escape text."""
    if isinstance(value, dict):
        return {
            strip_disallowed_control_characters(key)
            if isinstance(key, str)
            else key: sanitize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, str):
        return strip_disallowed_control_characters(value)
    return value
