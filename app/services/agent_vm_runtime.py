"""Shared VM session contracts for host-managed LLM tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

SYSTEM_USER_ID = 0


def resolve_sandbox_user_id(value: object) -> int:
    """Return a positive persisted user ID or the system sandbox namespace."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return SYSTEM_USER_ID


class AgentVmError(RuntimeError):
    """Raised when a VM session cannot satisfy a requested operation."""


class AgentVmDeadlineExceeded(AgentVmError):
    """Raised when a deadline expires before a VM operation can finish."""


class AgentVmFileSizeLimitExceeded(AgentVmError):
    """Raised when a VM file operation exceeds its explicit byte limit."""


class AgentVmPathError(AgentVmError):
    """Raised when an agent-facing path violates the task workspace contract."""


_WORKSPACE_PATH_ERROR = (
    "VM path is outside the task workspace. Address files with workspace-relative paths."
)


def resolve_workspace_relative_path(
    path: str,
    *,
    workspace_root: PurePosixPath,
) -> PurePosixPath:
    """Resolve an agent path to one canonical workspace-relative POSIX path."""
    if not workspace_root.is_absolute() or ".." in workspace_root.parts:
        raise AgentVmError(f"Invalid VM workspace root: {workspace_root}")

    candidate = PurePosixPath(path.strip() or ".")
    if ".." in candidate.parts:
        raise AgentVmPathError(_WORKSPACE_PATH_ERROR)
    if not candidate.is_absolute():
        return candidate
    try:
        return candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise AgentVmPathError(_WORKSPACE_PATH_ERROR) from exc


@dataclass(frozen=True)
class AgentCommandResult:
    """Normalized shell command result returned by a VM session."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class AgentVmLease:
    """Observable lifecycle metadata for a VM-backed task workspace."""

    provider: str
    vm_namespace: str
    sandbox_id: str | None
    reuse_scope: str
    reused: bool
    template_revision: str | None = None
    capabilities: dict[str, Any] | None = None


class AgentVmSession(ABC):
    """Small provider-independent VM workspace interface."""

    provider: str
    sandbox_id: str | None
    lease: AgentVmLease
    workspace_posix_root: PurePosixPath

    @abstractmethod
    def resolve_relative_path(self, path: str) -> str:
        """Return the canonical workspace-relative form of an agent path."""

    @abstractmethod
    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        """Run one shell command in the VM workspace."""

    @abstractmethod
    def write_file(self, path: str, text: str) -> None:
        """Write UTF-8 text inside the VM workspace."""

    @abstractmethod
    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read UTF-8 text from the VM workspace."""

    @abstractmethod
    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Read raw bytes from the VM workspace."""

    @abstractmethod
    def list_files(self, path: str = ".") -> list[str]:
        """List files below a VM workspace path."""

    @abstractmethod
    def close(self) -> None:
        """Release VM resources."""


def agent_vm_session_log_payload(session: object) -> dict[str, Any]:
    """Return stable log metadata for real sessions and lightweight test fakes."""
    lease = getattr(session, "lease", None)
    return {
        "provider": getattr(session, "provider", None),
        "sandbox_id": getattr(session, "sandbox_id", None),
        "vm_namespace": getattr(lease, "vm_namespace", None),
        "reuse_scope": getattr(lease, "reuse_scope", None),
        "reused": getattr(lease, "reused", None),
        "template_revision": getattr(lease, "template_revision", None),
        "capabilities": getattr(lease, "capabilities", None),
    }
