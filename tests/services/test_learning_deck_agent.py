from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.models.contracts import LlmTaskKind, LlmTaskMode
from app.models.db import VendorUsageRecord
from app.services import learning_deck_agent
from app.services.learning_deck_agent import LearningDeckAgentExecutionError
from app.services.llm_tasks import create_llm_task


class _FakeAgentResult:
    output = "Deck generated."

    def usage(self) -> object:
        return SimpleNamespace(input_tokens=1000, output_tokens=500, total_tokens=1500)


class _FakeAgent:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tool(self, func):
        return func

    def run_sync(self, *_args: Any, **_kwargs: Any) -> _FakeAgentResult:
        return _FakeAgentResult()


class _FakeSandbox:
    provider = "local"
    sandbox_id = "sandbox-usage"

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.closed = False

    def run_command(
        self,
        _command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        del timeout_seconds
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        return self.run_command(command, timeout_seconds=timeout_seconds)

    def write_file(self, path: str, text: str) -> None:
        self.files[path] = text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == learning_deck_agent.OUTPUT_INDEX_HTML:
            return (
                "<html><style>.reveal .slides section { color: #eee; background: #111; "
                "padding: 2rem; }</style><body><div class='reveal'><div class='slides'>"
                "<section>Deck</section></div></div></body></html>"
            )
        if path == learning_deck_agent.OUTPUT_SOURCE_NOTES:
            return "# Source Notes\n\n## Sources\n\n- Primary source."
        if path == learning_deck_agent.OUTPUT_SOURCE_METADATA:
            return "{}"
        return self.files[path]

    def read_file_bytes(self, _path: str, *, max_bytes: int | None = None) -> bytes:
        del max_bytes
        return b""

    def list_files(self, _path: str = ".") -> list[str]:
        return []

    def close(self) -> None:
        self.closed = True


class _MissingOutputSandbox(_FakeSandbox):
    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        if path == learning_deck_agent.OUTPUT_SOURCE_METADATA:
            return "{}"
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _RepairingAgent(_FakeAgent):
    calls = 0

    def run_sync(self, *_args: Any, **kwargs: Any) -> _FakeAgentResult:
        type(self).calls += 1
        if type(self).calls == 2:
            sandbox = kwargs["deps"].sandbox
            sandbox.write_file(
                learning_deck_agent.OUTPUT_INDEX_HTML,
                "<html><style>.reveal .slides section { color: #eee; background: #111; "
                "padding: 2rem; }</style><div class='reveal'><div class='slides'>"
                "<section>Repaired</section></div></div></html>",
            )
            sandbox.write_file(
                learning_deck_agent.OUTPUT_SOURCE_NOTES,
                "# Sources\n\n- Primary source.",
            )
        return _FakeAgentResult()


def test_learning_deck_agent_persists_vendor_usage_row(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _FakeSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={
            "source_kind": "content",
            "source_identity": "content:77",
            "source_content_id": 77,
            "source_title": "Deck Source",
            "body_text": "Source body for a generated learning deck.",
        },
        interests_prompt="Focus on systems",
        user_id=test_user.id,
        run_id=123,
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert result.model_provider == "openai"
    assert sandbox.closed is True
    row = (
        db_session.query(VendorUsageRecord)
        .filter(VendorUsageRecord.feature == "learning_deck_generation")
        .one()
    )
    assert row.operation == "learning_deck.generate"
    assert row.source == "queue"
    assert row.user_id == test_user.id
    assert row.content_id == 77
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.total_tokens == 1500
    assert row.metadata_json == {
        "run_id": 123,
        "source_kind": "content",
        "source_identity": "content:77",
        "source_content_id": 77,
    }


def test_learning_deck_agent_uses_generic_vm_session_when_llm_task_exists(
    db_session,
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    llm_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
    )
    db_session.commit()
    sandbox = _FakeSandbox()
    calls: list[dict[str, object]] = []

    def fake_create_agent_vm_session(**kwargs):
        calls.append(kwargs)
        return sandbox

    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")
    monkeypatch.setattr(
        learning_deck_agent,
        "create_agent_vm_session",
        fake_create_agent_vm_session,
    )

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={
            "source_kind": "content",
            "source_identity": "content:88",
            "source_content_id": 88,
            "source_title": "Deck Source",
            "body_text": "Source body for a generated learning deck.",
        },
        interests_prompt=None,
        user_id=test_user.id,
        run_id=456,
        llm_task=llm_task,
    )

    assert result.sandbox_provider == "local"
    assert calls == [
        {
            "user_id": test_user.id,
            "llm_task_id": llm_task.id,
            "vm_namespace": llm_task.vm_namespace,
            "workspace_path": llm_task.workspace_path,
            "shared_workspace_path": llm_task.shared_workspace_path,
            "feature": "learning_deck",
        }
    ]


def test_learning_deck_agent_log_event_accepts_deps_object() -> None:
    events: list[dict[str, Any]] = []
    deps = learning_deck_agent.LearningDeckAgentDeps(
        sandbox=cast(Any, _FakeSandbox()),
        user_id=1,
        run_id=2,
        agent_log_events=events,
    )

    learning_deck_agent._append_agent_log_event(
        deps,
        "read_file",
        {"path": "output/index.html"},
    )

    assert events == [
        {
            "created_at": events[0]["created_at"],
            "event_type": "read_file",
            "payload": {"path": "output/index.html"},
        }
    ]


def test_learning_deck_agent_repairs_missing_required_artifacts_once(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _MissingOutputSandbox()
    _RepairingAgent.calls = 0
    monkeypatch.setattr(learning_deck_agent, "Agent", _RepairingAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    result = learning_deck_agent.run_learning_deck_agent(
        source_snapshot={"source_kind": "content", "source_title": "Source"},
        interests_prompt=None,
        user_id=test_user.id,
        run_id=91,
        sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
    )

    assert _RepairingAgent.calls == 2
    assert "Repaired" in result.index_html
    assert any(
        event["event_type"] == "artifact_validation_failed" for event in result.agent_log_events
    )


def test_learning_deck_agent_reports_typed_failure_when_repair_does_not_create_outputs(
    test_user,
    vendor_usage_db,
    monkeypatch,
) -> None:
    del vendor_usage_db
    sandbox = _MissingOutputSandbox()
    monkeypatch.setattr(learning_deck_agent, "Agent", _FakeAgent)
    monkeypatch.setattr(
        learning_deck_agent,
        "build_pydantic_model",
        lambda _model_spec: (object(), {}),
    )
    monkeypatch.setattr(learning_deck_agent, "resolve_model_provider", lambda _model_spec: "openai")

    with pytest.raises(
        LearningDeckAgentExecutionError,
        match="artifact_contract_failed",
    ) as exc_info:
        learning_deck_agent.run_learning_deck_agent(
            source_snapshot={"source_kind": "content", "source_title": "Source"},
            interests_prompt=None,
            user_id=test_user.id,
            run_id=92,
            sandbox_factory=lambda _user_id, _run_id: cast(Any, sandbox),
        )

    assert exc_info.value.sandbox_id == sandbox.sandbox_id
    assert any(
        event["event_type"] == "artifact_repair_failed" for event in exc_info.value.agent_log_events
    )
