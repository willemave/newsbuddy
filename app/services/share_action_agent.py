"""Agent loop for VM-backed ShareSheet actions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.api.share_actions import ShareActionAgentResult
from app.models.db import LlmTask
from app.services.agent_toolset import (
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
)
from app.services.agent_vm_runtime import (
    AgentVmDeadlineExceeded,
    AgentVmSession,
    agent_vm_session_log_payload,
)
from app.services.agent_vm_sessions import create_agent_vm_session
from app.services.llm_models import build_pydantic_model, resolve_model_provider
from app.services.llm_tasks import require_llm_task_id
from app.services.prompt_library import load_prompt
from app.services.vendor_usage import record_model_usage

logger = get_logger(__name__)

OUTPUT_RESULT_JSON = "output/result.json"
INPUT_REQUEST_JSON = "input/request.json"
INPUT_ACTION_SKILL = "input/action-skill.md"
INPUT_OUTPUT_SCHEMA = "input/output-schema.json"


class ShareActionAgentExecutionError(RuntimeError):
    """Raised when the Share Action agent cannot produce a valid result."""

    def __init__(
        self,
        message: str,
        *,
        agent_log_events: list[dict[str, Any]],
        sandbox_provider: str,
        sandbox_id: str | None,
    ) -> None:
        super().__init__(message)
        self.agent_log_events = agent_log_events
        self.sandbox_provider = sandbox_provider
        self.sandbox_id = sandbox_id


@dataclass(frozen=True)
class ShareActionAgentRunResult:
    """Result payload from a Share Action agent run."""

    result: ShareActionAgentResult
    model_provider: str
    model_name: str
    sandbox_provider: str
    sandbox_id: str | None
    agent_log_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ShareActionAgentDeps:
    """Dependencies exposed to the Share Action pydantic-ai tool layer."""

    task: LlmTask
    sandbox: AgentVmSession
    user_id: int
    llm_task_id: int
    agent_log_events: list[dict[str, Any]]


ShareActionSandboxFactory = Callable[[LlmTask], AgentVmSession]


def run_share_action_agent(
    *,
    task: LlmTask,
    sandbox_factory: ShareActionSandboxFactory | None = None,
) -> ShareActionAgentRunResult:
    """Run a Share Action agent and read its typed output artifact."""
    settings = get_settings()
    deadline = monotonic() + settings.llm_task_sandbox_timeout_seconds
    llm_task_id = require_llm_task_id(task)
    user_id = _require_int(task.user_id, "LLM task user id")
    sandbox = (
        sandbox_factory(task)
        if sandbox_factory is not None
        else _create_configured_sandbox(task, deadline=deadline)
    )
    agent_log_events: list[dict[str, Any]] = []
    try:
        _append_agent_log_event(
            agent_log_events,
            "sandbox_started",
            agent_vm_session_log_payload(sandbox),
        )
        _prepare_sandbox_inputs(sandbox, task=task)
        model_spec = settings.llm_task_model
        provider = resolve_model_provider(model_spec)
        model, base_model_settings = build_pydantic_model(model_spec)
        agent: Agent[ShareActionAgentDeps, str] = Agent(
            model,
            deps_type=ShareActionAgentDeps,
            output_type=str,
            system_prompt=_build_system_prompt(task),
        )
        _register_tools(agent, task=task)
        deps = ShareActionAgentDeps(
            task=task,
            sandbox=sandbox,
            user_id=user_id,
            llm_task_id=llm_task_id,
            agent_log_events=agent_log_events,
        )
        try:
            result = agent.run_sync(
                _build_user_prompt(task),
                deps=deps,
                model_settings=_build_runtime_model_settings(
                    base_model_settings,
                    deadline=deadline,
                ),
            )
        except Exception as exc:
            _append_agent_log_event(
                agent_log_events,
                "agent_failed",
                {"error": str(exc), "failure_class": type(exc).__name__},
            )
            raise ShareActionAgentExecutionError(
                str(exc),
                agent_log_events=agent_log_events,
                sandbox_provider=sandbox.provider,
                sandbox_id=sandbox.sandbox_id,
            ) from exc
        _append_agent_log_event(
            agent_log_events,
            "agent_completed",
            {"output_chars": len(str(result.output or ""))},
        )
        record_model_usage(
            "share_action_run",
            result,
            model_spec=model_spec,
            persist={
                "provider": provider,
                "feature": "share_action",
                "operation": "share_action.run",
                "source": "queue",
                "user_id": user_id,
                "metadata": {
                    "llm_task_id": llm_task_id,
                    "mode": task.mode,
                    "workflow_key": task.workflow_key,
                },
            },
        )
        parsed = _read_result_json(sandbox)
        return ShareActionAgentRunResult(
            result=parsed,
            model_provider=provider,
            model_name=model_spec,
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
            agent_log_events=agent_log_events,
        )
    finally:
        sandbox.close()


def _register_tools(agent: Agent[ShareActionAgentDeps, str], *, task: LlmTask) -> None:
    register_agent_vm_tools(
        agent,
        session_getter=lambda deps: deps.sandbox,
        log_event=_append_agent_log_event,
        user_id_getter=lambda deps: deps.user_id,
        metadata_getter=lambda deps: {
            "llm_task_id": deps.llm_task_id,
            "mode": deps.task.mode,
        },
        config=AgentToolsetConfig(
            feature="share_action",
            operation_prefix="share_action",
            source="queue",
            tool_policy=AgentToolPolicy.from_mapping(task.tool_policy),
        ),
    )


def _create_configured_sandbox(
    task: LlmTask,
    *,
    deadline: float | None = None,
) -> AgentVmSession:
    return create_agent_vm_session(
        user_id=_require_int(task.user_id, "LLM task user id"),
        llm_task_id=require_llm_task_id(task),
        vm_namespace=_require_str(task.vm_namespace, "LLM task VM namespace"),
        workspace_path=_require_str(task.workspace_path, "LLM task workspace path"),
        shared_workspace_path=_require_str(
            task.shared_workspace_path,
            "LLM task shared workspace path",
        ),
        feature=f"share_action.{task.mode}",
        deadline=deadline,
    )


def _prepare_sandbox_inputs(sandbox: AgentVmSession, *, task: LlmTask) -> None:
    sandbox.execute_bash("mkdir -p input output scratch")
    request_payload = {
        "llm_task_id": task.id,
        "mode": task.mode,
        "workflow_key": task.workflow_key,
        "approval_policy": task.approval_policy,
        "allowed_actions": task.allowed_actions,
        "input": task.input_json,
    }
    sandbox.write_file(INPUT_REQUEST_JSON, json.dumps(request_payload, indent=2, sort_keys=True))
    sandbox.write_file(INPUT_ACTION_SKILL, _load_mode_prompt(task))
    sandbox.write_file(
        INPUT_OUTPUT_SCHEMA,
        json.dumps(ShareActionAgentResult.model_json_schema(), indent=2, sort_keys=True),
    )


def _build_system_prompt(task: LlmTask) -> str:
    return (
        "You run a Newsly ShareSheet workflow in a VM.\n"
        "Use only the provided tools. Do not call Newsly internal APIs from bash.\n"
        "Use the web_search tool for web research.\n"
        "Always write output/result.json matching input/output-schema.json. The host validates "
        "that artifact and applies any product action.\n\n"
        f"{_load_mode_prompt(task)}"
    )


def _build_user_prompt(task: LlmTask) -> str:
    return (
        "Run the Share Action now.\n"
        f"Mode: {task.mode}\n"
        f"Request: {INPUT_REQUEST_JSON}\n"
        f"Mode guidance: {INPUT_ACTION_SKILL}\n"
        f"Output schema: {INPUT_OUTPUT_SCHEMA}\n"
        f"Required final artifact: {OUTPUT_RESULT_JSON}\n"
    )


def _load_mode_prompt(task: LlmTask) -> str:
    return load_prompt(f"llm_tasks/share_action.{task.mode}")


def _build_runtime_model_settings(
    base_model_settings: ModelSettings | None,
    *,
    deadline: float,
) -> ModelSettings:
    runtime_settings = dict(base_model_settings or {})
    runtime_settings["timeout"] = _remaining_run_timeout(deadline)
    return cast(ModelSettings, runtime_settings)


def _remaining_run_timeout(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AgentVmDeadlineExceeded("Share Action agent deadline was exceeded")
    return remaining


def _read_result_json(sandbox: AgentVmSession) -> ShareActionAgentResult:
    try:
        raw_json = sandbox.read_file(OUTPUT_RESULT_JSON, max_bytes=1_000_000)
    except Exception as exc:
        raise ShareActionAgentExecutionError(
            "Share Action agent did not write output/result.json",
            agent_log_events=[],
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
        ) from exc
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ShareActionAgentExecutionError(
            f"Share Action result JSON is invalid: {exc}",
            agent_log_events=[],
            sandbox_provider=sandbox.provider,
            sandbox_id=sandbox.sandbox_id,
        ) from exc
    return ShareActionAgentResult.model_validate(parsed)


def _append_agent_log_event(
    deps_or_events: ShareActionAgentDeps | list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    events = (
        deps_or_events.agent_log_events
        if isinstance(deps_or_events, ShareActionAgentDeps)
        else deps_or_events
    )
    events.append(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
    )


def _require_str(value: str | None, label: str) -> str:
    if not value:
        raise ShareActionAgentExecutionError(
            f"{label} is missing",
            agent_log_events=[],
            sandbox_provider="unknown",
            sandbox_id=None,
        )
    return value


def _require_int(value: int | None, label: str) -> int:
    if value is None:
        raise ShareActionAgentExecutionError(
            f"{label} is missing",
            agent_log_events=[],
            sandbox_provider="unknown",
            sandbox_id=None,
        )
    return int(value)
