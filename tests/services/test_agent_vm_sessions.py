from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from uuid import uuid4

from e2b.exceptions import SandboxNotFoundException

from app.core.settings import get_settings
from app.services import agent_vm_sessions
from app.services.agent_vm_sessions import create_agent_vm_session


def test_local_agent_vm_session_reports_process_namespace_reuse(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "local")
    namespace = f"test:{uuid4()}"

    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    second = create_agent_vm_session(
        user_id=1,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/two",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert first.lease.vm_namespace == namespace
    assert first.lease.reuse_scope == "process_namespace"
    assert first.lease.reused is False
    assert second.lease.vm_namespace == namespace
    assert second.lease.reuse_scope == "process_namespace"
    assert second.lease.reused is True


class _FakeE2BCommands:
    def __init__(self, sandbox: _FakeE2BSandbox) -> None:
        self.sandbox = sandbox
        self.commands: list[str] = []

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> SimpleNamespace:
        del cwd, timeout
        self.commands.append(command)
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        if command == "newsly-sandbox-probe --json":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        name: "test"
                        for name in (
                            "bash",
                            "python",
                            "node",
                            "git",
                            "curl",
                            "jq",
                            "chromium",
                            "playwright",
                        )
                    }
                ),
                stderr="",
                exit_code=0,
            )
        return SimpleNamespace(stdout="", stderr="", exit_code=0)


class _FakeE2BFiles:
    def __init__(self, sandbox: _FakeE2BSandbox) -> None:
        self.sandbox = sandbox
        self.files: dict[str, str] = {}

    def write(self, path: str, text: str) -> None:
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        self.files[path] = text

    def read(self, path: str) -> str:
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        return self.files[path]


class _FakeE2BSandbox:
    created: list[_FakeE2BSandbox] = []

    def __init__(self, *, missing: bool = False) -> None:
        self.id = f"fake-e2b-{len(self.created) + 1}"
        self.missing = missing
        self.killed = False
        self.timeout_refreshes: list[int] = []
        self.commands = _FakeE2BCommands(self)
        self.files = _FakeE2BFiles(self)

    @classmethod
    def create(cls, **_kwargs) -> _FakeE2BSandbox:
        sandbox = cls()
        cls.created.append(sandbox)
        return sandbox

    def kill(self) -> None:
        self.killed = True

    def set_timeout(self, timeout_seconds: int) -> None:
        if self.missing:
            raise SandboxNotFoundException(f"Sandbox {self.id} not found")
        self.timeout_refreshes.append(timeout_seconds)


def test_missing_sandbox_error_does_not_match_template_or_dependency_failures() -> None:
    assert agent_vm_sessions._is_missing_e2b_sandbox_error(
        SandboxNotFoundException("Sandbox sandbox-123 not found")
    )
    assert not agent_vm_sessions._is_missing_e2b_sandbox_error(
        RuntimeError("Sandbox template not found")
    )
    assert not agent_vm_sessions._is_missing_e2b_sandbox_error(
        RuntimeError("Sandbox dependency not found")
    )


def _install_fake_e2b(monkeypatch) -> None:
    _FakeE2BSandbox.created = []
    monkeypatch.setitem(
        sys.modules,
        "e2b_code_interpreter",
        SimpleNamespace(Sandbox=_FakeE2BSandbox),
    )
    monkeypatch.setattr(
        agent_vm_sessions,
        "record_vendor_usage_out_of_band",
        lambda **_kwargs: None,
    )


def _configure_e2b(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "e2b")
    monkeypatch.setattr(settings, "llm_task_sandbox_e2b_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_task_sandbox_template", None)
    monkeypatch.setattr(settings, "llm_task_sandbox_timeout_seconds", 60)
    monkeypatch.setattr(settings, "llm_task_sandbox_allow_internet_access", True)
    monkeypatch.setattr(settings, "llm_task_sandbox_max_output_chars", 20_000)


def test_e2b_agent_vm_session_recreates_stale_cached_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    stale = _FakeE2BSandbox(missing=True)
    cache_key = agent_vm_sessions._e2b_cache_key(namespace, None)
    agent_vm_sessions._PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE[cache_key] = stale

    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert stale.killed is True
    assert stale.timeout_refreshes == []
    assert len(_FakeE2BSandbox.created) == 1
    assert session.sandbox_id == _FakeE2BSandbox.created[0].id
    assert session.lease.reused is False
    assert (
        agent_vm_sessions._PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE[cache_key]
        is (_FakeE2BSandbox.created[0])
    )


def test_e2b_agent_vm_session_probes_configured_template_once(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_template", "newsly-agent-v1")
    namespace = f"test:{uuid4()}"

    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    second = create_agent_vm_session(
        user_id=1,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/two",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    probe_commands = [
        command
        for command in _FakeE2BSandbox.created[0].commands.commands
        if command == "newsly-sandbox-probe --json"
    ]
    assert probe_commands == ["newsly-sandbox-probe --json"]
    assert first.lease.template_revision == "newsly-agent-v1"
    assert first.lease.capabilities is not None
    assert first.lease.capabilities["chromium"] == "test"
    assert second.lease.capabilities == first.lease.capabilities


def test_e2b_agent_vm_session_reuses_live_cached_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"

    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    second = create_agent_vm_session(
        user_id=1,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/two",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert len(_FakeE2BSandbox.created) == 1
    assert first.sandbox_id == second.sandbox_id
    assert first.lease.reused is False
    assert second.lease.reused is True
    assert _FakeE2BSandbox.created[0].timeout_refreshes == [60]
