from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.model_defaults import CHEAP_MODEL_SPEC
from app.models.contracts import LlmTaskKind, LlmTaskMode
from app.services import share_action_agent
from app.services.agent_vm_runtime import AgentVmDeadlineExceeded, AgentVmSession
from app.services.llm_tasks import create_llm_task
from app.services.share_action_agent import ShareActionAgentExecutionError


class _FakeAgentResult:
    output = "done"

    @property
    def usage(self) -> object:
        return SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)


class _FakeAgent:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tool(self, func):
        return func

    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        return _FakeAgentResult()


class _DeadlineAgent(_FakeAgent):
    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        raise AgentVmDeadlineExceeded("agent run expired")


class _FakeSandbox:
    provider = "local"
    sandbox_id = "sandbox-share-action"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False

    def execute_bash(
        self,
        _command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    def write_file(self, path: str, text: str) -> None:
        self.files[path] = text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == share_action_agent.OUTPUT_RESULT_JSON:
            return '{"action":"no_action","confidence":0.0}'
        return self.files[path]

    def close(self) -> None:
        self.closed = True


def test_share_action_agent_uses_cheap_vm_task_model(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_CONTENT,
        workflow_key="share_action.add_content.v1",
        tool_policy={},
        input_json={"url": "https://example.com/story"},
    )
    db_session.commit()
    captured_model_specs: list[str] = []

    def fake_build_pydantic_model(model_spec: str):
        captured_model_specs.append(model_spec)
        return object(), {}

    monkeypatch.setattr(share_action_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(share_action_agent, "build_pydantic_model", fake_build_pydantic_model)

    result = share_action_agent.run_share_action_agent(
        task=task,
        sandbox_factory=lambda _task: cast(AgentVmSession, _FakeSandbox()),
    )

    assert result.model_name == CHEAP_MODEL_SPEC
    assert result.model_provider == "openai"
    assert captured_model_specs == [CHEAP_MODEL_SPEC]


def test_share_action_agent_passes_overall_deadline_to_generic_vm_session(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_CONTENT,
        workflow_key="share_action.add_content.v1",
        tool_policy={},
        input_json={"url": "https://example.com/story"},
    )
    db_session.commit()
    sandbox = _FakeSandbox()
    calls: list[dict[str, object]] = []

    def fake_create_agent_vm_session(**kwargs):
        calls.append(kwargs)
        return sandbox

    monkeypatch.setattr(share_action_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        share_action_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(
        share_action_agent,
        "create_agent_vm_session",
        fake_create_agent_vm_session,
    )
    monkeypatch.setattr(share_action_agent, "monotonic", lambda: 200.0)

    share_action_agent.run_share_action_agent(task=task)

    assert calls == [
        {
            "user_id": test_user.id,
            "llm_task_id": task.id,
            "vm_namespace": task.vm_namespace,
            "workspace_path": task.workspace_path,
            "shared_workspace_path": task.shared_workspace_path,
            "feature": f"share_action.{task.mode}",
            "deadline": 200.0 + share_action_agent.get_settings().llm_task_sandbox_timeout_seconds,
        }
    ]
    assert sandbox.closed is True


def test_share_action_agent_closes_sandbox_when_agent_deadline_expires(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_CONTENT,
        workflow_key="share_action.add_content.v1",
        tool_policy={},
        input_json={"url": "https://example.com/story"},
    )
    db_session.commit()
    sandbox = _FakeSandbox()
    monkeypatch.setattr(share_action_agent, "Agent", _DeadlineAgent)
    monkeypatch.setattr(
        share_action_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )

    with pytest.raises(ShareActionAgentExecutionError, match="agent run expired") as exc_info:
        share_action_agent.run_share_action_agent(
            task=task,
            sandbox_factory=lambda _task: cast(AgentVmSession, sandbox),
        )

    assert sandbox.closed is True
    assert any(
        event["event_type"] == "agent_failed"
        and event["payload"]["failure_class"] == "AgentVmDeadlineExceeded"
        for event in exc_info.value.agent_log_events
    )


def test_add_to_briefing_agent_receives_mode_prompt_and_discriminated_schema(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_TO_BRIEFING,
        workflow_key="share_action.add_to_briefing.v1",
        tool_policy={},
        input_json={"url": "https://example.com/story"},
    )
    db_session.commit()
    sandbox = _FakeSandbox()
    monkeypatch.setattr(share_action_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        share_action_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )

    share_action_agent.run_share_action_agent(
        task=task,
        sandbox_factory=lambda _task: cast(AgentVmSession, sandbox),
    )

    assert "# Share Action: Add to Briefing" in sandbox.files[share_action_agent.INPUT_ACTION_SKILL]
    schema = json.loads(sandbox.files[share_action_agent.INPUT_OUTPUT_SCHEMA])
    target_schema = schema["$defs"]["ShareActionBriefingTarget"]
    assert target_schema["discriminator"]["propertyName"] == "kind"
    assert set(target_schema["discriminator"]["mapping"]) == {"feed", "content"}
