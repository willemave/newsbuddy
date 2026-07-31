from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.models.contracts import LlmTaskKind, LlmTaskMode
from app.services import share_action_agent
from app.services.agent_vm_sessions import AgentVmSession
from app.services.llm_tasks import create_llm_task


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


class _FakeSandbox:
    provider = "local"
    sandbox_id = "sandbox-share-action"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

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
        pass


def test_share_action_agent_uses_flash_v4_vm_task_model(
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

    assert result.model_name == OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    assert result.model_provider == "openrouter"
    assert captured_model_specs == [OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC]
