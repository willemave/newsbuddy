"""Contract tests for lazy access to the persistent per-user agent VM."""

from __future__ import annotations

from pathlib import PurePosixPath

from app.services import lazy_agent_vm
from app.services.agent_vm_runtime import AgentCommandResult, AgentVmLease, AgentVmSession
from app.services.lazy_agent_vm import LazyAgentVmRuntime


class _FakeSession(AgentVmSession):
    provider = "e2b"
    sandbox_id = "sandbox-1"
    workspace_posix_root = PurePosixPath("/data/workspace/chat/11")
    lease = AgentVmLease(
        provider="e2b",
        vm_namespace="user:7",
        sandbox_id="sandbox-1",
        reuse_scope="persistent_user",
        reused=True,
    )

    def __init__(self) -> None:
        self.close_calls = 0

    def resolve_relative_path(self, path: str) -> str:
        return path

    def execute_bash(self, command: str, **_kwargs) -> AgentCommandResult:
        return AgentCommandResult(stdout=command, stderr="", exit_code=0)

    def write_file(self, path: str, text: str) -> None:
        del path, text

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        del max_bytes
        return path

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        return self.read_file(path, max_bytes=max_bytes).encode()

    def list_files(self, path: str = ".") -> list[str]:
        return [path]

    def close(self) -> None:
        self.close_calls += 1


def test_runtime_construction_performs_no_sandbox_operation(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        lazy_agent_vm,
        "create_agent_vm_session",
        lambda **kwargs: calls.append(kwargs),
    )

    runtime = LazyAgentVmRuntime(
        user_id=7,
        session_id=11,
        llm_task_id=22,
        feature="chat",
    )

    assert runtime.acquired is False
    assert calls == []
    runtime.close()
    assert calls == []


def test_first_tool_acquires_once_and_close_only_releases_the_lease(monkeypatch) -> None:
    session = _FakeSession()
    calls: list[dict[str, object]] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return session

    monkeypatch.setattr(lazy_agent_vm, "create_agent_vm_session", _create)
    runtime = LazyAgentVmRuntime(
        user_id=7,
        session_id=11,
        llm_task_id=22,
        feature="chat",
    )

    assert runtime.get_session() is session
    assert runtime.get_session() is session
    assert calls == [
        {
            "user_id": 7,
            "llm_task_id": 22,
            "vm_namespace": "user:7",
            "workspace_path": "/data/workspace/chat/11",
            "shared_workspace_path": "/data/workspace/shared",
            "feature": "chat",
            "deadline": None,
        }
    ]

    runtime.close()

    assert runtime.acquired is False
    assert session.close_calls == 1
