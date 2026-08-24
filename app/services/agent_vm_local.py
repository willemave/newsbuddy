"""Local filesystem-backed agent VM provider for tests and explicit development."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.settings import get_settings
from app.services.agent_vm_io import (
    AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
    AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
    AGENT_VM_MAX_LISTED_FILES,
    bounded_file_read_limit,
    bounded_operation_timeout,
    remaining_deadline_seconds,
    truncate_output,
    validate_file_write_size,
)
from app.services.agent_vm_runtime import (
    AgentCommandResult,
    AgentVmDeadlineExceeded,
    AgentVmError,
    AgentVmFileSizeLimitExceeded,
    AgentVmLease,
    AgentVmPathError,
    AgentVmSession,
    resolve_workspace_relative_path,
)

_PROCESS_LOCAL_ROOTS_BY_NAMESPACE: dict[str, Path] = {}
_PROCESS_LOCAL_AGENT_VM_LOCK = threading.RLock()
_LOCAL_DATA_PATH_RE = re.compile(r"(?<![A-Za-z0-9_:.])/data(?=/|\b)")


@dataclass
class LocalAgentVmSession(AgentVmSession):
    """Local filesystem-backed VM session used by tests and explicit local dev."""

    vm_namespace: str
    namespace_root: Path
    workspace_posix_root: PurePosixPath
    workspace_root: Path
    shared_root: Path
    lease: AgentVmLease
    deadline: float | None = None
    provider: str = "local"
    sandbox_id: str | None = None

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        max_output_chars: int | None = None,
    ) -> AgentCommandResult:
        effective_timeout = bounded_operation_timeout(
            deadline=self.deadline,
            requested_timeout=timeout_seconds,
            default_timeout=AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            local_command = _LOCAL_DATA_PATH_RE.sub(
                str((self.namespace_root / "data").resolve()),
                command,
            )
            result = subprocess.run(
                ["/bin/bash", "-lc", local_command],
                cwd=self.workspace_root,
                env=_local_agent_process_env(),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentVmDeadlineExceeded("Agent VM command deadline was exceeded") from exc
        output_limit = max_output_chars or get_settings().llm_task_sandbox_max_output_chars
        if on_stdout is not None and result.stdout:
            on_stdout(result.stdout)
        return AgentCommandResult(
            stdout=truncate_output(result.stdout or "", output_limit),
            stderr=truncate_output(result.stderr or "", output_limit),
            exit_code=int(result.returncode),
        )

    def write_file(self, path: str, text: str) -> None:
        remaining_deadline_seconds(self.deadline)
        validate_file_write_size(path, text)
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        remaining_deadline_seconds(self.deadline)
        effective_max_bytes = bounded_file_read_limit(max_bytes)
        with self._resolve_read_path(path).open("rb") as handle:
            data = handle.read(effective_max_bytes + 1)
        if len(data) > effective_max_bytes:
            raise AgentVmFileSizeLimitExceeded(f"VM file exceeds limit: {path}")
        return data

    def list_files(self, path: str = ".") -> list[str]:
        remaining_deadline_seconds(self.deadline)
        root = self._resolve_read_path(path)
        if not root.exists():
            return []
        workspace_root = self.workspace_root.resolve()
        data_root = (self.namespace_root / "data").resolve()
        files: list[str] = []
        for child in root.rglob("*"):
            remaining_deadline_seconds(self.deadline)
            if not child.is_file():
                continue
            if child == workspace_root or workspace_root in child.parents:
                display_path = child.relative_to(workspace_root).as_posix()
            elif child == data_root or data_root in child.parents:
                display_path = "/data/" + child.relative_to(data_root).as_posix()
            else:  # pragma: no cover - both resolvers constrain the tree
                raise AgentVmPathError("VM read path escaped the data root")
            files.append(display_path)
            if len(files) > AGENT_VM_MAX_LISTED_FILES:
                raise AgentVmError(
                    f"VM file listing exceeds {AGENT_VM_MAX_LISTED_FILES} files: {path}"
                )
        return sorted(files)

    def close(self) -> None:
        return

    def resolve_relative_path(self, path: str) -> str:
        return resolve_workspace_relative_path(
            path,
            workspace_root=self.workspace_posix_root,
        ).as_posix()

    def _resolve_workspace_path(self, path: str) -> Path:
        workspace_root = self.workspace_root.resolve()
        relative_path = PurePosixPath(self.resolve_relative_path(path))
        candidate = workspace_root.joinpath(*relative_path.parts).resolve()
        if candidate != workspace_root and workspace_root not in candidate.parents:
            raise AgentVmPathError("VM path must stay inside the task workspace")
        return candidate

    def _resolve_read_path(self, path: str) -> Path:
        candidate = PurePosixPath(path.strip() or ".")
        if candidate.is_absolute():
            if ".." in candidate.parts or not (
                candidate == PurePosixPath("/data") or PurePosixPath("/data") in candidate.parents
            ):
                raise AgentVmPathError("VM reads must stay inside the workspace or /data")
            data_root = (self.namespace_root / "data").resolve()
            resolved = data_root.joinpath(*candidate.parts[2:]).resolve()
            if resolved != data_root and data_root not in resolved.parents:
                raise AgentVmPathError("VM read path escaped /data")
            return resolved
        return self._resolve_workspace_path(path)


def create_local_agent_vm_session(
    *,
    vm_namespace: str,
    workspace_path: str,
    shared_workspace_path: str,
    deadline: float | None = None,
) -> LocalAgentVmSession:
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
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
        workspace_posix_root=PurePosixPath(workspace_path),
        workspace_root=workspace_root,
        shared_root=shared_root,
        lease=AgentVmLease(
            provider="local",
            vm_namespace=vm_namespace,
            sandbox_id=None,
            reuse_scope="process_namespace",
            reused=reused,
        ),
        deadline=deadline,
    )


def close_process_local_agent_vm_sessions() -> int:
    """Remove process-scoped local VM roots and return the number released."""
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        local_roots = list(dict.fromkeys(_PROCESS_LOCAL_ROOTS_BY_NAMESPACE.values()))
        _PROCESS_LOCAL_ROOTS_BY_NAMESPACE.clear()
    for root in local_roots:
        shutil.rmtree(root, ignore_errors=True)
    return len(local_roots)


def _map_vm_path(namespace_root: Path, vm_path: str) -> Path:
    normalized = PurePosixPath(vm_path.strip() or ".")
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise AgentVmError(f"Invalid VM workspace path: {vm_path}")
    return namespace_root.joinpath(*normalized.parts[1:]).resolve()


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
