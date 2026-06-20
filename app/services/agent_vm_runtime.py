"""Shared VM session contracts for host-managed LLM tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class AgentVmError(RuntimeError):
    """Raised when a VM session cannot satisfy a requested operation."""


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


class AgentVmSession(ABC):
    """Small provider-independent VM workspace interface."""

    provider: str
    sandbox_id: str | None
    lease: AgentVmLease

    @abstractmethod
    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
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
    }
