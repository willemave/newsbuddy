"""Translate VM tool events into durable advisory chat progress."""

from __future__ import annotations

from typing import Literal

from app.services.chat_partial_stream import DurableChatToolProgressWriter


def numeric_tool_payload_value(
    payload: dict[str, object],
    key: str,
) -> int | float | None:
    value = payload.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def tool_event_status(
    event: str,
    payload: dict[str, object],
) -> Literal["running", "failed", "completed"]:
    if event.endswith(("_started", "_progress")):
        return "running"
    if event.endswith("_failed") or payload.get("exit_code") not in (None, 0):
        return "failed"
    return "completed"


def agent_vm_tool_log_context(
    payload: dict[str, object],
    *,
    sandbox_acquired: bool,
) -> dict[str, object]:
    return {
        "sandbox_acquired": sandbox_acquired,
        "sandbox_id": payload.get("sandbox_id"),
        "sandbox_reused": payload.get("sandbox_reused"),
        "sandbox_acquisition_ms": payload.get("sandbox_acquisition_ms"),
        "sandbox_provider_acquisition_ms": payload.get("sandbox_provider_acquisition_ms"),
        "sandbox_hydration_ms": payload.get("sandbox_hydration_ms"),
        "execution_ms": payload.get("execution_ms"),
        "exit_code": payload.get("exit_code"),
        "path": payload.get("path"),
        "chars": payload.get("chars"),
        "failure_class": payload.get("failure_class"),
    }


def publish_tool_progress(
    writer: DurableChatToolProgressWriter | None,
    *,
    event: str,
    payload: dict[str, object],
) -> None:
    if writer is None:
        return
    tool_name = event.removesuffix("_started").removesuffix("_progress").removesuffix("_failed")
    status = tool_event_status(event, payload)
    detail = payload.get("stdout") or payload.get("error")
    writer.publish(
        tool_name=tool_name,
        status=status,
        detail=str(detail) if detail else None,
    )
