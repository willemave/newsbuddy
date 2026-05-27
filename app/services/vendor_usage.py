"""Shared vendor usage tracking helpers for per-run aggregation."""

from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from typing import Any

from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band

_USAGE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("vendor_usage_context", default=None)


def start_usage_context() -> Token:
    """Start a new usage aggregation context."""
    return _USAGE_CONTEXT.set(
        {
            "total": {
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "steps": {},
        }
    )


def end_usage_context(token: Token) -> None:
    """End the current usage context."""
    _USAGE_CONTEXT.reset(token)


def snapshot_usage() -> dict[str, Any] | None:
    """Return a snapshot of the current usage aggregation."""
    usage = _USAGE_CONTEXT.get()
    if not usage:
        return None
    return deepcopy(usage)


def record_model_usage(
    step: str,
    result: object,
    *,
    model_spec: str | None = None,
    persist: dict[str, Any] | None = None,
) -> None:
    """Record usage for a single model call into the current context."""
    usage_context = _USAGE_CONTEXT.get()
    usage = extract_usage_from_result(result)
    if not usage:
        return

    if usage_context is not None:
        step_entry = usage_context["steps"].setdefault(
            step,
            {
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
            },
        )
        if model_spec:
            step_entry["model_spec"] = model_spec

        step_entry["calls"] += 1
        for key in (
            "input_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "output_tokens",
            "total_tokens",
        ):
            value = usage.get(key)
            if value is None:
                continue
            step_entry[key] += value
            usage_context["total"][key] += value

    if persist:
        record_vendor_usage_out_of_band(
            provider=persist.get("provider"),
            model=model_spec or persist.get("model") or "unknown",
            feature=persist["feature"],
            operation=persist["operation"],
            source=persist.get("source"),
            usage=usage,
            request_id=persist.get("request_id"),
            task_id=persist.get("task_id"),
            content_id=persist.get("content_id"),
            session_id=persist.get("session_id"),
            message_id=persist.get("message_id"),
            user_id=persist.get("user_id"),
            metadata=persist.get("metadata"),
        )
