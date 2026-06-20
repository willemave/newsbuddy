"""Host-mediated tools exposed to generic LLM task VMs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.core.settings import get_settings
from app.models.db import LlmTask
from app.services.agent_toolset import AgentToolPolicy
from app.services.exa_client import ExaSearchResult, exa_search
from app.services.llm_tasks import LlmTaskError, require_llm_task_id

LLM_TASK_TOOL_TOKEN_TYPE = "llm_task_tool"
LLM_TASK_WEB_SEARCH_TOOL = "web_search"


class LlmTaskToolAuthError(ValueError):
    """Raised when a VM tool token is absent, invalid, or scoped incorrectly."""


class LlmTaskToolPermissionError(ValueError):
    """Raised when a task is not permitted to use a host-mediated tool."""


def create_llm_task_tool_token(
    task: LlmTask,
    *,
    tool_name: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a short-lived token scoped to one user, task, and tool."""
    settings = get_settings()
    user_id = _require_task_user_id(task)
    llm_task_id = require_llm_task_id(task)
    now = datetime.now(UTC)
    expires = now + (expires_delta or timedelta(seconds=settings.llm_task_tool_token_ttl_seconds))
    payload = {
        "sub": str(user_id),
        "type": LLM_TASK_TOOL_TOKEN_TYPE,
        "llm_task_id": llm_task_id,
        "tool": tool_name,
        "exp": expires,
        "iat": now,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_llm_task_tool_token(
    token: str,
    *,
    expected_llm_task_id: int,
    expected_tool_name: str,
) -> int:
    """Validate a VM tool token and return its scoped user id."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise LlmTaskToolAuthError("Invalid LLM task tool token") from exc

    if payload.get("type") != LLM_TASK_TOOL_TOKEN_TYPE:
        raise LlmTaskToolAuthError("Invalid LLM task tool token type")
    if payload.get("tool") != expected_tool_name:
        raise LlmTaskToolAuthError("LLM task tool token is not scoped to this tool")

    try:
        user_id = int(payload["sub"])
        llm_task_id = int(payload["llm_task_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmTaskToolAuthError("LLM task tool token has invalid claims") from exc

    if llm_task_id != int(expected_llm_task_id):
        raise LlmTaskToolAuthError("LLM task tool token is not scoped to this task")
    return user_id


def run_llm_task_web_search(
    task: LlmTask,
    *,
    query: str,
    num_results: int,
    category: str | None,
) -> list[ExaSearchResult]:
    """Run the task-scoped host-mediated web search tool."""
    if not AgentToolPolicy.from_mapping(task.tool_policy).web_search:
        raise LlmTaskToolPermissionError(
            f"LLM task is not allowed to use {LLM_TASK_WEB_SEARCH_TOOL}"
        )
    return exa_search(
        query,
        num_results=num_results,
        category=category,
        max_characters=2500,
        raise_on_error=True,
        telemetry={
            "feature": "llm_task_tool",
            "operation": "llm_task.web_search",
            "source": "vm",
            "user_id": _require_task_user_id(task),
            "metadata": {
                "llm_task_id": require_llm_task_id(task),
                "task_kind": task.task_kind,
                "mode": task.mode,
                "workflow_key": task.workflow_key,
            },
        },
    )


def _require_task_user_id(task: LlmTask) -> int:
    if task.user_id is None:
        raise LlmTaskError("LLM task is missing a user id")
    return int(task.user_id)
