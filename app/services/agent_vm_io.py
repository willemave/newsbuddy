"""Shared bounded I/O helpers for agent VM providers."""

from __future__ import annotations

from time import monotonic

from app.services.agent_vm_runtime import (
    AgentVmDeadlineExceeded,
    AgentVmError,
    AgentVmFileSizeLimitExceeded,
)

AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0
AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS = 300.0
AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS = 30.0
AGENT_VM_HYDRATION_TIMEOUT_SECONDS = 300.0
AGENT_VM_MAX_FILE_BYTES = 20_000_000
AGENT_VM_MAX_LISTED_FILES = 500


def truncate_output(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n[... truncated ...]"


def command_event_text(event: object) -> str:
    for attr in ("line", "text", "data"):
        value = getattr(event, attr, None)
        if value is not None:
            return str(value)
    return str(event)


def remaining_deadline_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AgentVmDeadlineExceeded("Agent VM deadline was exceeded")
    return remaining


def bounded_operation_timeout(
    *,
    deadline: float | None,
    requested_timeout: float | None,
    default_timeout: float,
    maximum_timeout: float,
) -> float:
    timeout = default_timeout if requested_timeout is None else float(requested_timeout)
    if timeout <= 0:
        raise AgentVmError("Agent VM operation timeout must be positive")
    timeout = min(timeout, maximum_timeout)
    remaining = remaining_deadline_seconds(deadline)
    return min(timeout, remaining) if remaining is not None else timeout


def bounded_file_read_limit(max_bytes: int | None) -> int:
    if max_bytes is not None and max_bytes <= 0:
        raise AgentVmError("Agent VM file-read limit must be positive")
    return min(max_bytes or AGENT_VM_MAX_FILE_BYTES, AGENT_VM_MAX_FILE_BYTES)


def validate_file_write_size(path: str, text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > AGENT_VM_MAX_FILE_BYTES:
        raise AgentVmFileSizeLimitExceeded(f"VM file exceeds limit: {path}")
