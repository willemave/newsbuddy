"""Concrete VM session providers for generic LLM tasks."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.settings import get_settings
from app.services.agent_vm_runtime import (
    AgentCommandResult,
    AgentVmError,
    AgentVmLease,
    AgentVmSession,
)
from app.services.vendor_costs import record_vendor_usage_out_of_band

_PROCESS_LOCAL_ROOTS_BY_NAMESPACE: dict[str, Path] = {}
_PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE: dict[str, Any] = {}


@dataclass
class LocalAgentVmSession(AgentVmSession):
    """Local filesystem-backed VM session used by tests and explicit local dev."""

    vm_namespace: str
    namespace_root: Path
    workspace_root: Path
    shared_root: Path
    lease: AgentVmLease
    provider: str = "local"
    sandbox_id: str | None = None

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> AgentCommandResult:
        result = subprocess.run(
            ["/bin/bash", "-lc", _with_workspace_tool_env(command)],
            cwd=self.workspace_root,
            env=_local_agent_process_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        max_output_chars = get_settings().llm_task_sandbox_max_output_chars
        return AgentCommandResult(
            stdout=_truncate_output(result.stdout or "", max_output_chars),
            stderr=_truncate_output(result.stderr or "", max_output_chars),
            exit_code=int(result.returncode),
        )

    def write_file(self, path: str, text: str) -> None:
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        data = self._resolve_workspace_path(path).read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            raise AgentVmError(f"VM file exceeds {max_bytes} bytes: {path}")
        return data

    def list_files(self, path: str = ".") -> list[str]:
        root = self._resolve_workspace_path(path)
        if not root.exists():
            return []
        workspace_root = self.workspace_root.resolve()
        return [
            child.relative_to(workspace_root).as_posix()
            for child in sorted(root.rglob("*"))
            if child.is_file()
        ]

    def close(self) -> None:
        return

    def _resolve_workspace_path(self, path: str) -> Path:
        workspace_root = self.workspace_root.resolve()
        candidate = (workspace_root / path.strip().lstrip("/")).resolve()
        if candidate != workspace_root and workspace_root not in candidate.parents:
            raise AgentVmError("VM path must stay inside the task workspace")
        return candidate


class E2BAgentVmSession(AgentVmSession):
    """E2B-backed reusable VM session for generic LLM task workspaces."""

    provider = "e2b"

    def __init__(
        self,
        *,
        user_id: int,
        llm_task_id: int,
        vm_namespace: str,
        workspace_path: str,
        shared_workspace_path: str,
        feature: str,
    ) -> None:
        settings = get_settings()
        api_key = settings.llm_task_sandbox_e2b_api_key
        if not api_key:
            raise AgentVmError("E2B API key is not configured for LLM tasks")
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:  # pragma: no cover
            raise AgentVmError("e2b-code-interpreter is not installed") from exc

        sandbox: Any = _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.get(vm_namespace)
        created = False
        if sandbox is None:
            create_kwargs: dict[str, Any] = {
                "timeout": settings.llm_task_sandbox_timeout_seconds,
                "allow_internet_access": settings.llm_task_sandbox_allow_internet_access,
                "api_key": api_key,
                "metadata": {
                    "feature": feature,
                    "user_id": str(user_id),
                    "vm_namespace": vm_namespace,
                },
            }
            if settings.llm_task_sandbox_template:
                create_kwargs["template"] = settings.llm_task_sandbox_template
            sandbox = Sandbox.create(**create_kwargs)
            _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE[vm_namespace] = sandbox
            created = True

        self.vm_namespace = vm_namespace
        self._sandbox = sandbox
        self._workdir = PurePosixPath(workspace_path)
        self._shared_workdir = PurePosixPath(shared_workspace_path)
        self._max_output_chars = settings.llm_task_sandbox_max_output_chars
        self.sandbox_id = _sandbox_identifier(self._sandbox)
        self.lease = AgentVmLease(
            provider=self.provider,
            vm_namespace=vm_namespace,
            sandbox_id=self.sandbox_id,
            reuse_scope="process_namespace",
            reused=not created,
        )
        self._run_raw_command(
            "mkdir -p "
            f"{shlex.quote(self._workdir.as_posix())} "
            f"{shlex.quote(self._shared_workdir.as_posix())}"
        )
        if created:
            record_vendor_usage_out_of_band(
                provider="e2b",
                model=settings.llm_task_sandbox_template or "default",
                feature="llm_task_sandbox",
                operation="llm_task_sandbox.e2b_create",
                source="queue",
                usage={"request_count": 1},
                user_id=user_id,
                metadata={
                    "llm_task_id": llm_task_id,
                    "vm_namespace": vm_namespace,
                    "sandbox_id": self.sandbox_id,
                    "feature": feature,
                    "allow_internet_access": settings.llm_task_sandbox_allow_internet_access,
                    "timeout_seconds": settings.llm_task_sandbox_timeout_seconds,
                },
            )

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> AgentCommandResult:
        try:
            result = self._sandbox.commands.run(
                _with_workspace_tool_env(command),
                cwd=self._workdir.as_posix(),
                timeout=timeout_seconds,
            )
        except _e2b_command_exit_exception() as exc:
            return self._normalize_command_exit_exception(exc)
        return self._normalize_command_result(result)

    def write_file(self, path: str, text: str) -> None:
        destination = self._resolve_workspace_path(path)
        parent = PurePosixPath(destination).parent
        self._run_raw_command(f"mkdir -p {shlex.quote(parent.as_posix())}")
        self._sandbox.files.write(destination, text)

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        payload = self._sandbox.files.read(self._resolve_workspace_path(path))
        data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        if max_bytes is not None and len(data) > max_bytes:
            raise AgentVmError(f"VM file exceeds {max_bytes} bytes: {path}")
        return data

    def list_files(self, path: str = ".") -> list[str]:
        target = self._resolve_workspace_path(path)
        relative_target = _relative_to_workdir(target, self._workdir)
        result = self.execute_bash(f"find {shlex.quote(relative_target)} -type f | sort")
        if result.exit_code != 0:
            return []
        return [line.strip().lstrip("./") for line in result.stdout.splitlines() if line.strip()]

    def close(self) -> None:
        # Generic LLM task sandboxes are namespace-scoped in this worker process.
        return

    def _resolve_workspace_path(self, path: str) -> str:
        candidate = PurePosixPath(path.strip() or ".")
        if candidate.is_absolute():
            candidate = PurePosixPath(str(candidate).lstrip("/"))
        if ".." in candidate.parts:
            raise AgentVmError("VM path must stay inside the task workspace")
        return (self._workdir / candidate).as_posix()

    def _run_raw_command(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> AgentCommandResult:
        result = self._sandbox.commands.run(command, timeout=timeout_seconds)
        return self._normalize_command_result(result)

    def _normalize_command_result(self, result: object) -> AgentCommandResult:
        return AgentCommandResult(
            stdout=_truncate_output(
                str(getattr(result, "stdout", "") or ""),
                self._max_output_chars,
            ),
            stderr=_truncate_output(
                str(getattr(result, "stderr", "") or ""),
                self._max_output_chars,
            ),
            exit_code=int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0),
        )

    def _normalize_command_exit_exception(self, exc: Exception) -> AgentCommandResult:
        return AgentCommandResult(
            stdout=_truncate_output(str(getattr(exc, "stdout", "") or ""), self._max_output_chars),
            stderr=_truncate_output(str(getattr(exc, "stderr", "") or ""), self._max_output_chars),
            exit_code=int(getattr(exc, "exit_code", 1) or 1),
        )


def create_agent_vm_session(
    *,
    user_id: int,
    llm_task_id: int,
    vm_namespace: str,
    workspace_path: str,
    shared_workspace_path: str,
    feature: str,
) -> AgentVmSession:
    """Create or attach to the configured generic LLM task VM session."""
    settings = get_settings()
    provider = settings.llm_task_sandbox_provider
    if provider == "disabled":
        raise AgentVmError("LLM task sandbox provider is disabled")
    if provider == "local":
        return _create_local_agent_vm_session(
            vm_namespace=vm_namespace,
            workspace_path=workspace_path,
            shared_workspace_path=shared_workspace_path,
        )
    if provider == "e2b":
        return E2BAgentVmSession(
            user_id=user_id,
            llm_task_id=llm_task_id,
            vm_namespace=vm_namespace,
            workspace_path=workspace_path,
            shared_workspace_path=shared_workspace_path,
            feature=feature,
        )
    raise AgentVmError(f"Unsupported LLM task sandbox provider: {provider}")


def _create_local_agent_vm_session(
    *,
    vm_namespace: str,
    workspace_path: str,
    shared_workspace_path: str,
) -> LocalAgentVmSession:
    namespace_root = _PROCESS_LOCAL_ROOTS_BY_NAMESPACE.get(vm_namespace)
    reused = namespace_root is not None
    if namespace_root is None:
        namespace_root = Path(tempfile.mkdtemp(prefix="newsly-llm-task-"))
        _PROCESS_LOCAL_ROOTS_BY_NAMESPACE[vm_namespace] = namespace_root
    workspace_root = _map_vm_path(namespace_root, workspace_path)
    shared_root = _map_vm_path(namespace_root, shared_workspace_path)
    workspace_root.mkdir(parents=True, exist_ok=True)
    shared_root.mkdir(parents=True, exist_ok=True)
    return LocalAgentVmSession(
        vm_namespace=vm_namespace,
        namespace_root=namespace_root,
        workspace_root=workspace_root,
        shared_root=shared_root,
        lease=AgentVmLease(
            provider="local",
            vm_namespace=vm_namespace,
            sandbox_id=None,
            reuse_scope="process_namespace",
            reused=reused,
        ),
    )


def _map_vm_path(namespace_root: Path, vm_path: str) -> Path:
    normalized = PurePosixPath(vm_path.strip() or ".")
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise AgentVmError(f"Invalid VM workspace path: {vm_path}")
    return namespace_root.joinpath(*normalized.parts[1:]).resolve()


def _truncate_output(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n[... truncated ...]"


def _local_agent_process_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "SHELL", "TMP", "TMPDIR", "TEMP"}
    }
    env.setdefault(
        "PATH",
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    )
    return env


def _with_workspace_tool_env(command: str) -> str:
    return (
        'export PATH="$PWD/.newsly/bin:$PATH"\n'
        'if [ -f "$PWD/.newsly/env" ]; then\n'
        "  set -a\n"
        '  . "$PWD/.newsly/env"\n'
        "  set +a\n"
        "fi\n"
        f"{command}"
    )


def _sandbox_identifier(sandbox: object) -> str | None:
    for attr in ("sandbox_id", "id", "sandboxId"):
        value = getattr(sandbox, attr, None)
        if value:
            return str(value)
    return None


def _e2b_command_exit_exception() -> type[Exception]:
    try:
        from e2b.sandbox.commands.command_handle import CommandExitException
    except ImportError:  # pragma: no cover
        return RuntimeError
    return CommandExitException


def _relative_to_workdir(path: str, workdir: PurePosixPath) -> str:
    full_path = PurePosixPath(path)
    try:
        return full_path.relative_to(workdir).as_posix()
    except ValueError:
        return full_path.name
