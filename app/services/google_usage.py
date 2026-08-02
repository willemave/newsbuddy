"""Token-usage extraction for Google GenAI responses."""

from __future__ import annotations


def _as_int(value: object) -> int | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_non_none(primary: object, fallback: object) -> object:
    return primary if primary is not None else fallback


def extract_google_usage_details(response: object) -> dict[str, int] | None:
    """Extract token usage from Google GenAI responses when available."""
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return None

    input_tokens = _as_int(
        _first_non_none(
            getattr(usage_metadata, "prompt_token_count", None),
            getattr(usage_metadata, "input_token_count", None),
        )
    )
    output_tokens = _as_int(
        _first_non_none(
            getattr(usage_metadata, "candidates_token_count", None),
            getattr(usage_metadata, "output_token_count", None),
        )
    )
    total_tokens = _as_int(getattr(usage_metadata, "total_token_count", None))

    details: dict[str, int] = {}
    if input_tokens is not None:
        details["input"] = input_tokens
    if output_tokens is not None:
        details["output"] = output_tokens
    if total_tokens is not None:
        details["total"] = total_tokens
    return details or None
