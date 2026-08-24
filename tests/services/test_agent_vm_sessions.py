from __future__ import annotations

import gc
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from e2b.exceptions import SandboxNotFoundException, TimeoutException

from app.core.settings import get_settings
from app.services import (
    agent_vm_capabilities,
    agent_vm_e2b_pool,
    agent_vm_io,
    agent_vm_local,
    agent_vm_sessions,
)
from app.services.agent_vm_e2b_pool import E2BSandboxAcquisition
from app.services.agent_vm_runtime import SYSTEM_USER_ID
from app.services.agent_vm_sessions import create_agent_vm_session
from app.services.agent_vm_state import LockedAgentVmState
from app.services.agent_vm_template import (
    AGENT_VM_TEMPLATE_NAME,
    AGENT_VM_TEMPLATE_REVISION,
)
from app.services.feed_research_runtime import FeedResearchRuntimeError, feed_research_runtime


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


def test_local_agent_vm_session_normalizes_workspace_absolute_paths_and_blocks_escape(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "local")
    workspace_path = "/tmp/newsly/tasks/55"
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=55,
        vm_namespace=f"test:{uuid4()}",
        workspace_path=workspace_path,
        shared_workspace_path="/tmp/newsly/users/1/shared",
        feature="test",
    )

    session.write_file(f"{workspace_path}/output/source-notes.md", "notes")

    assert session.read_file("output/source-notes.md") == "notes"
    assert session.resolve_relative_path(f"{workspace_path}/output/source-notes.md") == (
        "output/source-notes.md"
    )
    with pytest.raises(agent_vm_sessions.AgentVmPathError, match="workspace-relative paths"):
        session.write_file("/etc/passwd", "blocked")

    outside = tmp_path / "outside"
    outside.mkdir()
    assert isinstance(session, agent_vm_local.LocalAgentVmSession)
    (session.workspace_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(agent_vm_sessions.AgentVmPathError, match="inside the task workspace"):
        session.write_file("escape/payload.txt", "blocked")

    session.close()


class _FakeE2BCommands:
    def __init__(self, sandbox: _FakeE2BSandbox) -> None:
        self.sandbox = sandbox
        self.commands: list[str] = []
        self.timeouts: list[float | None] = []
        self.request_timeouts: list[float | None] = []
        self.fallback_stdout = ""

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        user: str | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
    ) -> SimpleNamespace:
        del cwd, user
        self.commands.append(command)
        self.timeouts.append(timeout)
        self.request_timeouts.append(request_timeout)
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        if command.startswith("mkdir -p") and self.sandbox.bootstrap_error is not None:
            raise self.sandbox.bootstrap_error
        if command == agent_vm_capabilities.AGENT_VM_CAPABILITY_PROBE:
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "bash": "/bin/bash",
                        "python": "/usr/bin/python",
                        "node": "/usr/bin/node",
                        "git": "/usr/bin/git",
                        "curl": "/usr/bin/curl",
                        "jq": "/usr/bin/jq",
                        "rg": "/usr/bin/rg",
                        "chromium": True,
                        "playwright": True,
                    }
                ),
                stderr="",
                exit_code=0,
            )
        if self.sandbox.command_error is not None and (
            self.sandbox.command_error_contains is None
            or self.sandbox.command_error_contains in command
        ):
            raise self.sandbox.command_error
        return SimpleNamespace(stdout=self.fallback_stdout, stderr="", exit_code=0)


class _FakeFileStream:
    def __init__(self, data: bytes) -> None:
        self._chunks = [data]
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True


class _FakeE2BFiles:
    def __init__(self, sandbox: _FakeE2BSandbox) -> None:
        self.sandbox = sandbox
        self.files: dict[str, str] = {}
        self.write_timeouts: list[float | None] = []
        self.read_timeouts: list[float | None] = []
        self.stream_idle_timeouts: list[float | None] = []
        self.write_error: Exception | None = None
        self.read_error: Exception | None = None
        self.last_stream: _FakeFileStream | None = None

    def write(
        self,
        path: str,
        text: str,
        *,
        request_timeout: float | None = None,
    ) -> None:
        self.write_timeouts.append(request_timeout)
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        if self.write_error is not None:
            raise self.write_error
        self.files[path] = text

    def read(
        self,
        path: str,
        *,
        format: str = "text",
        request_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
    ) -> str | bytearray | _FakeFileStream:
        self.read_timeouts.append(request_timeout)
        self.stream_idle_timeouts.append(stream_idle_timeout)
        if self.sandbox.missing:
            raise RuntimeError("The sandbox was not found")
        if self.read_error is not None:
            raise self.read_error
        value = self.files[path]
        if format == "stream":
            self.last_stream = _FakeFileStream(value.encode("utf-8"))
            return self.last_stream
        return bytearray(value.encode("utf-8")) if format == "bytes" else value


class _FakeE2BSandbox:
    created: list[_FakeE2BSandbox] = []
    create_delay_seconds = 0.0
    create_barrier: threading.Barrier | None = None
    create_started: threading.Event | None = None
    create_release: threading.Event | None = None
    bootstrap_error: Exception | None = None
    timeout_error: Exception | None = None
    command_error: Exception | None = None
    command_error_contains: str | None = None
    create_kwargs: list[dict[str, object]] = []
    instances_by_id: dict[str, _FakeE2BSandbox] = {}
    created_snapshot_ids: list[str] = []
    deleted_snapshot_ids: list[str] = []

    def __init__(self, *, missing: bool = False) -> None:
        self.id = f"fake-e2b-{uuid4()}"
        self.missing = missing
        self.killed = False
        self.kill_count = 0
        self.timeout_refreshes: list[int] = []
        self.network_updates: list[dict[str, object]] = []
        self.commands = _FakeE2BCommands(self)
        self.files = _FakeE2BFiles(self)
        type(self).instances_by_id[self.id] = self

    @classmethod
    def create(cls, **_kwargs) -> _FakeE2BSandbox:
        cls.create_kwargs.append(_kwargs)
        if cls.create_started is not None:
            cls.create_started.set()
        if cls.create_barrier is not None:
            cls.create_barrier.wait(timeout=2)
        if cls.create_release is not None and not cls.create_release.wait(timeout=2):
            raise TimeoutError("fake E2B create was not released")
        if cls.create_delay_seconds:
            time.sleep(cls.create_delay_seconds)
        sandbox = cls()
        sandbox.bootstrap_error = cls.bootstrap_error
        sandbox.timeout_error = cls.timeout_error
        cls.created.append(sandbox)
        return sandbox

    def kill(
        self,
        *,
        api_key: str | None = None,
        request_timeout: float | None = None,
    ) -> bool:
        del api_key, request_timeout
        sandbox = (
            self
            if isinstance(self, _FakeE2BSandbox)
            else _FakeE2BSandbox.instances_by_id.get(str(self))
        )
        if sandbox is None or sandbox.missing:
            return False
        sandbox.killed = True
        sandbox.missing = True
        sandbox.kill_count += 1
        return True

    def create_snapshot(self, **_kwargs: object) -> SimpleNamespace:
        snapshot_id = f"snapshot-{uuid4()}"
        type(self).created_snapshot_ids.append(snapshot_id)
        return SimpleNamespace(snapshot_id=snapshot_id)

    @staticmethod
    def delete_snapshot(snapshot_id: str, **_kwargs: object) -> bool:
        _FakeE2BSandbox.deleted_snapshot_ids.append(snapshot_id)
        return True

    def connect(
        self,
        timeout: int | None = None,
        *,
        api_key: str | None = None,
        request_timeout: float | None = None,
    ) -> _FakeE2BSandbox:
        """Model E2B's instance/class method variant with one test double."""
        del api_key, request_timeout
        if isinstance(self, _FakeE2BSandbox):
            sandbox = self
        else:
            sandbox = _FakeE2BSandbox.instances_by_id.get(str(self))
            if sandbox is None:
                raise SandboxNotFoundException(f"Sandbox {self} not found")
        if sandbox.timeout_error is not None:
            raise sandbox.timeout_error
        if sandbox.missing:
            raise SandboxNotFoundException(f"Sandbox {sandbox.id} not found")
        if timeout is not None:
            sandbox.timeout_refreshes.append(timeout)
        return sandbox

    def update_network(
        self,
        network: dict[str, object],
        *,
        request_timeout: float | None = None,
    ) -> None:
        del request_timeout
        self.network_updates.append(network)


def test_missing_sandbox_error_does_not_match_template_or_dependency_failures() -> None:
    assert agent_vm_e2b_pool.is_missing_e2b_sandbox_error(
        SandboxNotFoundException("Sandbox sandbox-123 not found")
    )
    assert not agent_vm_e2b_pool.is_missing_e2b_sandbox_error(
        RuntimeError("Sandbox template not found")
    )
    assert not agent_vm_e2b_pool.is_missing_e2b_sandbox_error(
        RuntimeError("Sandbox dependency not found")
    )


def test_agent_vm_capability_probe_rejects_non_object_manifest() -> None:
    sandbox = SimpleNamespace(
        commands=SimpleNamespace(
            run=lambda *_args, **_kwargs: SimpleNamespace(
                stdout="[]",
                stderr="",
                exit_code=0,
            )
        )
    )

    with pytest.raises(
        agent_vm_sessions.AgentVmError,
        match="invalid capability manifest",
    ):
        agent_vm_capabilities.probe_agent_vm_template(sandbox)


def test_system_vm_enables_only_candidate_egress_then_resets_to_denied(
    monkeypatch,
) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)

    session = create_agent_vm_session(
        user_id=0,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/data/workspace/feed/one",
        shared_workspace_path="/data/workspace/shared",
        feature="feed_research",
    )

    assert isinstance(session, agent_vm_sessions.E2BAgentVmSession)
    sandbox = _FakeE2BSandbox.created[0]
    assert _FakeE2BSandbox.create_kwargs[0]["allow_internet_access"] is False
    create_network = cast(dict[str, object], _FakeE2BSandbox.create_kwargs[0]["network"])
    create_denials = cast(list[str], create_network["deny_out"])
    assert "0.0.0.0/8" not in create_denials
    assert "224.0.0.0/4" not in create_denials
    assert "127.0.0.0/8" not in create_denials
    assert "::1/128" not in create_denials

    session.set_allowed_outbound_hosts(["feeds.example.com", "feeds.example.com"])
    session.reset_network_policy()

    assert sandbox.network_updates[0]["allow_internet_access"] is True
    assert sandbox.network_updates[0]["allow_out"] == ["feeds.example.com"]
    first_denials = cast(list[str], sandbox.network_updates[0]["deny_out"])
    assert "0.0.0.0/0" in first_denials
    assert "127.0.0.0/8" not in first_denials
    assert "::1/128" not in first_denials
    assert sandbox.network_updates[1]["allow_internet_access"] is False
    assert sandbox.network_updates[1]["allow_out"] == []
    session.close()


def test_sandbox_creation_has_no_private_beta_volume_mount(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)

    session = create_agent_vm_session(
        user_id=0,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/data/workspace/feed/one",
        shared_workspace_path="/data/workspace/shared",
        feature="feed_research",
    )

    assert "volume_mounts" not in _FakeE2BSandbox.create_kwargs[0]
    assert _FakeE2BSandbox.create_kwargs[0]["lifecycle"] == {
        "on_timeout": {"action": "pause", "keep_memory": True}
    }
    session.close()


def _install_fake_e2b(monkeypatch) -> None:
    agent_vm_sessions.close_process_agent_vm_sessions()
    _FakeE2BSandbox.created = []
    _FakeE2BSandbox.create_delay_seconds = 0.0
    _FakeE2BSandbox.create_barrier = None
    _FakeE2BSandbox.create_started = None
    _FakeE2BSandbox.create_release = None
    _FakeE2BSandbox.bootstrap_error = None
    _FakeE2BSandbox.timeout_error = None
    _FakeE2BSandbox.command_error = None
    _FakeE2BSandbox.command_error_contains = None
    _FakeE2BSandbox.create_kwargs = []
    _FakeE2BSandbox.instances_by_id = {}
    _FakeE2BSandbox.created_snapshot_ids = []
    _FakeE2BSandbox.deleted_snapshot_ids = []
    monkeypatch.setitem(
        sys.modules,
        "e2b_code_interpreter",
        SimpleNamespace(Sandbox=_FakeE2BSandbox),
    )

    @contextmanager
    def process_local_state(**_kwargs: object):
        yield LockedAgentVmState(row=None, durable=False, db=None)

    monkeypatch.setattr(agent_vm_e2b_pool, "locked_agent_vm_state", process_local_state)
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "harden_canonical_agent_vm",
        lambda *_args, **_kwargs: None,
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
    monkeypatch.setattr(settings, "llm_task_sandbox_timeout_seconds", 60)
    monkeypatch.setattr(settings, "llm_task_sandbox_allow_internet_access", True)
    monkeypatch.setattr(settings, "llm_task_sandbox_max_output_chars", 20_000)


def test_e2b_agent_vm_session_recreates_stale_cached_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    stale = _FakeE2BSandbox(missing=True)
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] = stale

    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert stale.killed is False
    assert stale.timeout_refreshes == []
    assert len(_FakeE2BSandbox.created) == 1
    assert session.sandbox_id == _FakeE2BSandbox.created[0].id
    assert session.lease.reused is False
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is (_FakeE2BSandbox.created[0])
    session.close()


def test_cached_workspace_bootstrap_missing_retries_once_with_fresh_sandbox(
    monkeypatch,
) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    stale = _FakeE2BSandbox()
    stale.bootstrap_error = SandboxNotFoundException(f"Sandbox {stale.id} not found")
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] = stale

    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert stale.kill_count == 1
    assert len(_FakeE2BSandbox.created) == 1
    assert session.sandbox_id == _FakeE2BSandbox.created[0].id
    assert session.lease.reused is False
    session.close()


def test_fresh_workspace_bootstrap_missing_does_not_retry(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.bootstrap_error = SandboxNotFoundException("Sandbox fresh not found")

    with pytest.raises(SandboxNotFoundException):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=1,
            vm_namespace=f"test:{uuid4()}",
            workspace_path="/workspace/newsly/users/1/tasks/one",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    assert len(_FakeE2BSandbox.created) == 1
    assert _FakeE2BSandbox.created[0].kill_count == 1


def test_workspace_retry_uses_final_acquisition_reuse_metadata(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    stale = _FakeE2BSandbox()
    stale.bootstrap_error = SandboxNotFoundException(f"Sandbox {stale.id} not found")
    replacement = _FakeE2BSandbox()
    acquisitions = iter(
        [
            E2BSandboxAcquisition(stale, False, {"bash": True}, None),
            E2BSandboxAcquisition(replacement, False, {"bash": True}, None),
        ]
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool.E2B_SANDBOX_POOL,
        "acquire",
        lambda **_kwargs: next(acquisitions),
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool.E2B_SANDBOX_POOL,
        "discard",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool.E2B_SANDBOX_POOL,
        "release",
        lambda *_args: None,
    )

    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert session.sandbox_id == replacement.id
    assert session.lease.reused is True
    session.close()


def test_e2b_agent_vm_session_uses_and_probes_canonical_template_once(monkeypatch) -> None:
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

    probe_commands = [
        command
        for command in _FakeE2BSandbox.created[0].commands.commands
        if command == agent_vm_capabilities.AGENT_VM_CAPABILITY_PROBE
    ]
    assert probe_commands == [agent_vm_capabilities.AGENT_VM_CAPABILITY_PROBE]
    assert _FakeE2BSandbox.create_kwargs[0]["template"] == AGENT_VM_TEMPLATE_NAME
    assert first.lease.template_revision == AGENT_VM_TEMPLATE_REVISION
    assert first.lease.capabilities is not None
    assert first.lease.capabilities["chromium"] is True
    assert second.lease.capabilities == first.lease.capabilities
    first.close()
    second.close()


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
    first.close()
    second.close()


def test_same_namespace_concurrent_acquisition_creates_one_e2b_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.create_delay_seconds = 0.05
    namespace = f"test:{uuid4()}"

    def _create(task_id: int):
        return create_agent_vm_session(
            user_id=1,
            llm_task_id=task_id,
            vm_namespace=namespace,
            workspace_path=f"/workspace/newsly/users/1/tasks/{task_id}",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        sessions = list(executor.map(_create, (1, 2)))

    assert len(_FakeE2BSandbox.created) == 1
    assert {session.sandbox_id for session in sessions} == {_FakeE2BSandbox.created[0].id}
    assert sorted(session.lease.reused for session in sessions) == [False, True]
    for session in sessions:
        session.close()


def test_e2b_session_deadline_bounds_create_bootstrap_and_file_read(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    deadline = time.monotonic() + 1

    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=deadline,
    )
    sandbox = _FakeE2BSandbox.created[0]
    create_timeout = _FakeE2BSandbox.create_kwargs[0]["request_timeout"]

    assert isinstance(create_timeout, float)
    assert 0 < create_timeout <= 1
    assert sandbox.commands.request_timeouts[0] is not None
    assert 0 < sandbox.commands.request_timeouts[0] <= 1

    session.write_file("payload.bin", "payload")
    assert session.read_file_bytes("payload.bin") == b"payload"
    assert len(sandbox.files.read_timeouts) == 1
    read_timeout = sandbox.files.read_timeouts[0]
    assert read_timeout is not None
    assert 0 < read_timeout <= 1
    assert sandbox.files.stream_idle_timeouts == sandbox.files.read_timeouts
    assert sandbox.files.last_stream is not None
    assert sandbox.files.last_stream.closed is True
    session.close()


def test_e2b_session_normalizes_workspace_absolute_paths_and_blocks_escape(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    workspace_path = "/tmp/newsly/tasks/55"
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=55,
        vm_namespace=f"test:{uuid4()}",
        workspace_path=workspace_path,
        shared_workspace_path="/tmp/newsly/users/1/shared",
        feature="test",
    )
    sandbox = _FakeE2BSandbox.created[0]
    command_count_before_write = len(sandbox.commands.commands)

    session.write_file(f"{workspace_path}/output/source-notes.md", "notes")

    assert session.read_file("output/source-notes.md") == "notes"
    assert len(sandbox.commands.commands) == command_count_before_write
    assert sandbox.files.files == {f"{workspace_path}/output/source-notes.md": "notes"}
    assert session.resolve_relative_path(f"{workspace_path}/output/source-notes.md") == (
        "output/source-notes.md"
    )
    with pytest.raises(agent_vm_sessions.AgentVmPathError, match="workspace-relative paths"):
        session.write_file("/etc/passwd", "blocked")
    assert f"{workspace_path}/etc/passwd" not in sandbox.files.files

    session.close()


def test_e2b_session_bounds_default_command_and_file_operations(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 600,
    )
    sandbox = _FakeE2BSandbox.created[0]

    session.execute_bash("printf bounded")
    session.write_file("payload.txt", "payload")
    assert session.read_file("payload.txt") == "payload"
    assert session.list_files() == []

    command_timeouts = dict(zip(sandbox.commands.commands, sandbox.commands.timeouts, strict=True))
    model_command = next(
        command for command in command_timeouts if command.endswith("printf bounded")
    )
    list_command = next(command for command in command_timeouts if command.startswith("find "))
    assert (
        command_timeouts[model_command]
        == agent_vm_sessions.AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS
    )
    assert (
        command_timeouts[list_command] == agent_vm_sessions.AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS
    )
    assert f"head -n {agent_vm_sessions.AGENT_VM_MAX_LISTED_FILES + 1}" in list_command
    assert sandbox.files.write_timeouts == [
        agent_vm_sessions.AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS
    ]
    assert sandbox.files.read_timeouts == [
        agent_vm_sessions.AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS
    ]
    session.close()


def test_e2b_session_closes_bounded_read_stream_when_file_is_too_large(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 60,
    )
    sandbox = _FakeE2BSandbox.created[0]
    session.write_file("payload.txt", "oversized")

    with pytest.raises(
        agent_vm_sessions.AgentVmFileSizeLimitExceeded,
        match="exceeds limit",
    ):
        session.read_file_bytes("payload.txt", max_bytes=4)

    assert sandbox.files.last_stream is not None
    assert sandbox.files.last_stream.closed is True
    session.close()


def test_e2b_session_rejects_oversized_host_file_write(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    monkeypatch.setattr(agent_vm_io, "AGENT_VM_MAX_FILE_BYTES", 4)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 60,
    )
    sandbox = _FakeE2BSandbox.created[0]

    with pytest.raises(
        agent_vm_sessions.AgentVmFileSizeLimitExceeded,
        match="exceeds limit",
    ):
        session.write_file("payload.txt", "12345")

    assert sandbox.files.write_timeouts == []
    session.close()


def test_e2b_session_rejects_operations_after_overall_deadline(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(agent_vm_sessions, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agent_vm_io, "monotonic", lambda: clock[0])
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=110.0,
    )
    sandbox = _FakeE2BSandbox.created[0]
    commands_before_expiry = len(sandbox.commands.commands)
    clock[0] = 111.0

    with pytest.raises(agent_vm_sessions.AgentVmDeadlineExceeded):
        session.execute_bash("should-not-run")

    assert len(sandbox.commands.commands) == commands_before_expiry
    session.close()


def test_e2b_session_maps_file_timeout_without_evicting_live_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 60,
    )
    sandbox = _FakeE2BSandbox.created[0]
    session.write_file("payload.txt", "payload")
    sandbox.files.read_error = TimeoutException("request timed out")

    with pytest.raises(agent_vm_sessions.AgentVmDeadlineExceeded, match="file-read"):
        session.read_file_bytes("payload.txt")

    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is sandbox
    assert sandbox.killed is False
    session.close()


def test_e2b_session_rejects_oversized_file_listing(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 60,
    )
    sandbox = _FakeE2BSandbox.created[0]
    sandbox.commands.fallback_stdout = "\n".join(
        f"./output/{index}.txt" for index in range(agent_vm_sessions.AGENT_VM_MAX_LISTED_FILES + 1)
    )

    with pytest.raises(agent_vm_sessions.AgentVmError, match="file listing exceeds"):
        session.list_files()

    session.close()


def test_e2b_session_file_listing_preserves_hidden_relative_paths(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"test:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 60,
    )
    sandbox = _FakeE2BSandbox.created[0]
    sandbox.commands.fallback_stdout = "./.newsly/env\n./output/index.html"

    assert session.list_files() == [".newsly/env", "output/index.html"]

    session.close()


def test_e2b_session_deadline_bounds_namespace_lock_wait(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    namespace_lock = agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_lock(cache_key)
    namespace_lock.acquire()
    try:
        with pytest.raises(
            agent_vm_sessions.AgentVmDeadlineExceeded,
            match="namespace",
        ):
            create_agent_vm_session(
                user_id=1,
                llm_task_id=1,
                vm_namespace=namespace,
                workspace_path="/workspace/newsly/users/1/tasks/one",
                shared_workspace_path="/workspace/newsly/users/1/shared",
                feature="test",
                deadline=time.monotonic() + 0.02,
            )
    finally:
        namespace_lock.release()

    assert _FakeE2BSandbox.created == []
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._active_acquisitions == 0


def test_e2b_namespace_lock_cache_releases_quiescent_keys() -> None:
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(f"test:{uuid4()}")
    namespace_lock = agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_lock(cache_key)

    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_locks[cache_key] is namespace_lock

    del namespace_lock
    gc.collect()

    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_locks


def test_e2b_command_request_timeout_maps_to_deadline_without_eviction(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
        deadline=time.monotonic() + 1,
    )
    sandbox = _FakeE2BSandbox.created[0]
    sandbox.command_error = TimeoutException("request timed out")
    sandbox.command_error_contains = "slow-command"

    with pytest.raises(agent_vm_sessions.AgentVmDeadlineExceeded):
        session.execute_bash("slow-command", timeout_seconds=0.01)

    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is sandbox
    assert sandbox.killed is False
    session.close()


def test_cached_e2b_refresh_deadline_does_not_evict_live_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    sandbox = _FakeE2BSandbox.created[0]
    sandbox.timeout_error = TimeoutException("request timed out")

    with pytest.raises(agent_vm_sessions.AgentVmDeadlineExceeded):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=2,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/two",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
            deadline=time.monotonic() + 0.1,
        )

    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is sandbox
    assert sandbox.killed is False
    first.close()


def test_cached_e2b_bootstrap_deadline_releases_lease_without_eviction(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    sandbox = _FakeE2BSandbox.created[0]
    first.close()
    sandbox.bootstrap_error = TimeoutException("request timed out")

    with pytest.raises(agent_vm_sessions.AgentVmDeadlineExceeded):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=2,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/two",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
            deadline=time.monotonic() + 0.1,
        )

    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is sandbox
    assert id(sandbox) not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._active_session_counts
    assert sandbox.killed is False


def test_different_namespaces_initialize_e2b_sandboxes_concurrently(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.create_barrier = threading.Barrier(2)

    def _create(task_id: int):
        return create_agent_vm_session(
            user_id=task_id,
            llm_task_id=task_id,
            vm_namespace=f"test:{uuid4()}",
            workspace_path=f"/workspace/newsly/users/{task_id}/tasks/{task_id}",
            shared_workspace_path=f"/workspace/newsly/users/{task_id}/shared",
            feature="test",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        sessions = list(executor.map(_create, (1, 2)))

    assert len(_FakeE2BSandbox.created) == 2
    assert len({session.sandbox_id for session in sessions}) == 2
    assert all(session.lease.reused is False for session in sessions)
    for session in sessions:
        session.close()


def test_idle_e2b_cache_detaches_least_recently_idle_session(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    monkeypatch.setattr(agent_vm_e2b_pool, "E2B_MAX_IDLE_CACHED_SESSIONS", 2)
    namespaces = [f"idle-limit:{uuid4()}" for _index in range(3)]
    sessions = [
        create_agent_vm_session(
            user_id=index,
            llm_task_id=index,
            vm_namespace=namespace,
            workspace_path=f"/workspace/newsly/users/{index}/tasks/{index}",
            shared_workspace_path=f"/workspace/newsly/users/{index}/shared",
            feature="test",
        )
        for index, namespace in enumerate(namespaces, start=1)
    ]
    cache_keys = [
        agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace) for namespace in namespaces
    ]
    sandboxes = list(_FakeE2BSandbox.created)

    for session in sessions:
        session.close()

    assert sandboxes[0].killed is False
    assert cache_keys[0] not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes
    assert sandboxes[1].killed is False
    assert sandboxes[2].killed is False
    assert set(agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes).issuperset(cache_keys[1:])


def test_idle_e2b_cache_never_evicts_active_session(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    monkeypatch.setattr(agent_vm_e2b_pool, "E2B_MAX_IDLE_CACHED_SESSIONS", 1)
    active_namespace = f"active-limit:{uuid4()}"
    active = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=active_namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    active_sandbox = _FakeE2BSandbox.created[0]
    active_cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(active_namespace)

    idle_sessions = [
        create_agent_vm_session(
            user_id=index,
            llm_task_id=index,
            vm_namespace=f"active-limit:{uuid4()}",
            workspace_path=f"/workspace/newsly/users/{index}/tasks/{index}",
            shared_workspace_path=f"/workspace/newsly/users/{index}/shared",
            feature="test",
        )
        for index in (2, 3)
    ]
    for session in idle_sessions:
        session.close()

    assert active_sandbox.killed is False
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[active_cache_key] is active_sandbox
    assert _FakeE2BSandbox.created[1].killed is False
    assert _FakeE2BSandbox.created[2].killed is False

    active.close()


def test_idle_e2b_cache_trims_when_final_acquisition_finishes(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    monkeypatch.setattr(agent_vm_e2b_pool, "E2B_MAX_IDLE_CACHED_SESSIONS", 1)
    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=f"idle-finish:{uuid4()}",
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    second = create_agent_vm_session(
        user_id=2,
        llm_task_id=2,
        vm_namespace=f"idle-finish:{uuid4()}",
        workspace_path="/workspace/newsly/users/2/tasks/two",
        shared_workspace_path="/workspace/newsly/users/2/shared",
        feature="test",
    )
    first_sandbox, second_sandbox = _FakeE2BSandbox.created
    _FakeE2BSandbox.create_started = threading.Event()
    _FakeE2BSandbox.create_release = threading.Event()

    def _create_third():
        return create_agent_vm_session(
            user_id=3,
            llm_task_id=3,
            vm_namespace=f"idle-finish:{uuid4()}",
            workspace_path="/workspace/newsly/users/3/tasks/three",
            shared_workspace_path="/workspace/newsly/users/3/shared",
            feature="test",
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_create_third)
        assert _FakeE2BSandbox.create_started.wait(timeout=1)
        first.close()
        second.close()
        assert first_sandbox.killed is False
        assert second_sandbox.killed is False
        _FakeE2BSandbox.create_release.set()
        third = future.result(timeout=1)

    assert first_sandbox.kill_count == 0
    assert second_sandbox.killed is False
    third_sandbox = _FakeE2BSandbox.created[2]
    assert third_sandbox.killed is False

    third.close()
    assert second_sandbox.kill_count == 0
    assert third_sandbox.killed is False


def test_process_cleanup_waits_for_inflight_e2b_initialization(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.create_started = threading.Event()
    _FakeE2BSandbox.create_release = threading.Event()
    namespace = f"test:{uuid4()}"

    def _create():
        return create_agent_vm_session(
            user_id=1,
            llm_task_id=1,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/one",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(_create)
        assert _FakeE2BSandbox.create_started.wait(timeout=1)
        cleanup_future = executor.submit(agent_vm_sessions.close_process_agent_vm_sessions)
        deadline = time.monotonic() + 1
        while not agent_vm_e2b_pool.E2B_SANDBOX_POOL._draining:
            assert time.monotonic() < deadline
            time.sleep(0.001)
        try:
            assert cleanup_future.done() is False
        finally:
            _FakeE2BSandbox.create_release.set()
        session = create_future.result(timeout=1)
        cleanup_future.result(timeout=1)

    assert session.sandbox_id == _FakeE2BSandbox.created[0].id
    assert _FakeE2BSandbox.created[0].killed is False
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes == {}
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_locks == {}
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._active_acquisitions == 0
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._draining is False
    session.close()
    assert _FakeE2BSandbox.created[0].killed is False


def test_post_create_workspace_failure_kills_and_evicts_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.bootstrap_error = RuntimeError("workspace bootstrap failed")
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)

    with pytest.raises(RuntimeError, match="workspace bootstrap failed"):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=1,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/one",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    assert len(_FakeE2BSandbox.created) == 1
    assert _FakeE2BSandbox.created[0].killed is True
    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes


def test_post_create_telemetry_failure_releases_lease_and_sandbox(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    monkeypatch.setattr(
        agent_vm_sessions,
        "record_vendor_usage_out_of_band",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("telemetry failed")),
    )

    with pytest.raises(RuntimeError, match="telemetry failed"):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=1,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/one",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    sandbox = _FakeE2BSandbox.created[0]
    assert sandbox.killed is True
    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes
    assert id(sandbox) not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._active_session_counts
    assert id(sandbox) not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._pending_kills


def test_cached_sandbox_connect_failure_preserves_persistent_instance(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    poisoned = _FakeE2BSandbox()
    poisoned.timeout_error = RuntimeError("timeout refresh failed")
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] = poisoned

    with pytest.raises(RuntimeError, match="timeout refresh failed"):
        create_agent_vm_session(
            user_id=1,
            llm_task_id=1,
            vm_namespace=namespace,
            workspace_path="/workspace/newsly/users/1/tasks/one",
            shared_workspace_path="/workspace/newsly/users/1/shared",
            feature="test",
        )

    assert poisoned.killed is False
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is poisoned


def test_malformed_feed_batch_evicts_cached_session_before_reuse(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    user_id = uuid4().int % 1_000_000_000 + 100
    namespace = f"user:{SYSTEM_USER_ID}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    sessions = []

    def _session_factory(**kwargs):
        session = create_agent_vm_session(**kwargs)
        sessions.append(session)
        return session

    with (
        pytest.raises(FeedResearchRuntimeError, match="incomplete result set"),
        feed_research_runtime(
            user_id=user_id,
            execution_id=1,
            session_factory=_session_factory,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    stale_session = sessions[0]
    stale_sandbox = _FakeE2BSandbox.created[0]
    assert stale_sandbox.killed is True
    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes

    replacement = create_agent_vm_session(
        user_id=SYSTEM_USER_ID,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/tmp/newsly/tasks/2",
        shared_workspace_path=f"/tmp/newsly/users/{SYSTEM_USER_ID}/shared",
        feature="feed_research",
    )

    assert len(_FakeE2BSandbox.created) == 2
    assert replacement.lease.reused is False
    replacement_sandbox = _FakeE2BSandbox.created[1]
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is (replacement_sandbox)

    agent_vm_sessions.evict_agent_vm_session(stale_session)
    assert replacement_sandbox.killed is False
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is (replacement_sandbox)
    agent_vm_sessions.evict_agent_vm_session(replacement)
    replacement.close()


def test_evict_detaches_immediately_but_waits_for_all_active_session_leases(
    monkeypatch,
) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)
    session_kwargs: dict[str, Any] = {
        "user_id": 1,
        "vm_namespace": namespace,
        "shared_workspace_path": "/workspace/newsly/users/1/shared",
        "feature": "test",
    }
    first = create_agent_vm_session(
        **session_kwargs,
        llm_task_id=1,
        workspace_path="/workspace/newsly/users/1/tasks/one",
    )
    second = create_agent_vm_session(
        **session_kwargs,
        llm_task_id=2,
        workspace_path="/workspace/newsly/users/1/tasks/two",
    )
    poisoned = _FakeE2BSandbox.created[0]

    agent_vm_sessions.evict_agent_vm_session(first)

    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes
    assert poisoned.killed is False

    replacement = create_agent_vm_session(
        **session_kwargs,
        llm_task_id=3,
        workspace_path="/workspace/newsly/users/1/tasks/three",
    )
    assert replacement.sandbox_id != first.sandbox_id
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] is (_FakeE2BSandbox.created[1])
    assert poisoned.killed is False

    first.close()
    assert poisoned.killed is False
    second.close()
    assert poisoned.killed is True
    replacement.close()


def test_concurrent_replacement_does_not_wait_for_poisoned_session_to_close(
    monkeypatch,
) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    namespace = f"test:{uuid4()}"
    session_kwargs: dict[str, Any] = {
        "user_id": 1,
        "vm_namespace": namespace,
        "shared_workspace_path": "/workspace/newsly/users/1/shared",
        "feature": "test",
    }
    first = create_agent_vm_session(
        **session_kwargs,
        llm_task_id=1,
        workspace_path="/workspace/newsly/users/1/tasks/one",
    )
    still_active = create_agent_vm_session(
        **session_kwargs,
        llm_task_id=2,
        workspace_path="/workspace/newsly/users/1/tasks/two",
    )
    poisoned = _FakeE2BSandbox.created[0]

    def _replace():
        agent_vm_sessions.evict_agent_vm_session(first)
        first.close()
        return create_agent_vm_session(
            **session_kwargs,
            llm_task_id=3,
            workspace_path="/workspace/newsly/users/1/tasks/three",
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        replacement = executor.submit(_replace).result(timeout=1)

    assert replacement.sandbox_id != still_active.sandbox_id
    assert poisoned.killed is False
    still_active.close()
    assert poisoned.killed is True
    replacement.close()


def test_feed_command_channel_failure_evicts_cached_session(monkeypatch) -> None:
    _install_fake_e2b(monkeypatch)
    _configure_e2b(monkeypatch)
    _FakeE2BSandbox.command_error = RuntimeError("command channel disconnected")
    _FakeE2BSandbox.command_error_contains = "curl"
    user_id = uuid4().int % 1_000_000_000 + 100
    namespace = f"user:{user_id}"
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(namespace)

    with (
        pytest.raises(FeedResearchRuntimeError, match="became unavailable"),
        feed_research_runtime(
            user_id=user_id,
            execution_id=1,
            use_llm=False,
        ) as runtime,
    ):
        assert runtime.detector.validate_feed_url("https://example.com/feed.xml") is None

    assert _FakeE2BSandbox.created[0].killed is True
    assert cache_key not in agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes


def test_process_cleanup_detaches_cached_sandboxes_and_removes_local_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = _FakeE2BSandbox()
    cache_key = agent_vm_e2b_pool.E2B_SANDBOX_POOL.cache_key(f"test:{uuid4()}")
    local_root = tmp_path / "local-agent-root"
    local_root.mkdir()
    (local_root / "artifact.txt").write_text("temporary", encoding="utf-8")
    agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes[cache_key] = sandbox
    agent_vm_local._PROCESS_LOCAL_ROOTS_BY_NAMESPACE["local:test"] = local_root

    agent_vm_sessions.close_process_agent_vm_sessions()

    assert sandbox.killed is False
    assert not local_root.exists()
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._sandboxes == {}
    assert agent_vm_e2b_pool.E2B_SANDBOX_POOL._namespace_locks == {}
    assert agent_vm_local._PROCESS_LOCAL_ROOTS_BY_NAMESPACE == {}


def test_e2b_acquisition_after_process_cleanup_creates_fresh_sandbox(monkeypatch) -> None:
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
    agent_vm_sessions.close_process_agent_vm_sessions()
    second = create_agent_vm_session(
        user_id=1,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/two",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert first.sandbox_id != second.sandbox_id
    assert first.lease.reused is False
    assert second.lease.reused is False
    assert _FakeE2BSandbox.created[0].killed is False
    assert _FakeE2BSandbox.created[1].killed is False

    first.close()
    assert _FakeE2BSandbox.created[0].killed is False

    agent_vm_sessions.close_process_agent_vm_sessions()
    assert _FakeE2BSandbox.created[1].killed is False
    second.close()
    assert _FakeE2BSandbox.created[1].killed is False
