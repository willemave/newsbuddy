"""Utility helpers for cleaning and repairing JSON payloads from LLM responses."""

from __future__ import annotations

import json

from app.core.logging import get_logger

logger = get_logger(__name__)


def _balance_structures(payload: str) -> str:
    """Append closing delimiters to balance objects and arrays."""

    stack: list[str] = []
    for char in payload:
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack:
            expected = stack[-1]
            if char == expected:
                stack.pop()

    balanced = payload
    for closing in reversed(stack):
        balanced += closing
    return balanced


def try_repair_truncated_json(json_str: str) -> str | None:
    """Attempt to repair truncated JSON by balancing structures and tagging truncated sections."""

    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError:
        pass

    full_markdown_pos = json_str.rfind('"full_markdown"')
    if full_markdown_pos != -1:
        after_field = json_str[full_markdown_pos:]
        if after_field.count('"') >= 2:
            value_start = after_field.find(':"') + 2 + full_markdown_pos
            if value_start > full_markdown_pos + 2:
                patched = json_str[:value_start]
                patched += json_str[value_start:].rstrip()
                if not patched.endswith('"'):
                    patched += '\\n\\n[Content truncated due to length]"'
                else:
                    patched = patched[:-1] + '\\n\\n[Content truncated due to length]"'

                patched = _balance_structures(patched)

                try:
                    json.loads(patched)
                    logger.info("Repaired truncated JSON inside full_markdown field")
                    return patched
                except json.JSONDecodeError:
                    pass

    repaired = json_str
    if repaired.count('"') % 2 != 0:
        repaired += '"'
    repaired = _balance_structures(repaired)

    try:
        json.loads(repaired)
        logger.info("Repaired truncated JSON by balancing braces and brackets")
        return repaired
    except json.JSONDecodeError:
        for index in range(len(json_str) - 1, 0, -1):
            if json_str[index] in {"]", "}"}:
                truncated = json_str[: index + 1]
                open_braces = truncated.count("{") - truncated.count("}")
                open_brackets = truncated.count("[") - truncated.count("]")
                truncated += "]" * open_brackets
                truncated += "}" * open_braces

                try:
                    json.loads(truncated)
                    logger.info("Repaired JSON by truncating to last balanced delimiter")
                    return truncated
                except json.JSONDecodeError:
                    continue

        return None
