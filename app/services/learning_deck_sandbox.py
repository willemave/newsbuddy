"""Sandbox provider boundary for Learning Deck generation."""

from __future__ import annotations

import mimetypes
import shlex
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band


class LearningDeckSandboxError(RuntimeError):
    """Raised when the Learning Deck sandbox cannot be used."""


@dataclass(frozen=True)
class LearningDeckCommandResult:
    """Normalized command result from the sandbox."""

    stdout: str
    stderr: str
    exit_code: int


class LearningDeckSandboxSession(ABC):
    """Small provider-independent sandbox interface."""

    provider: str
    sandbox_id: str | None

    @abstractmethod
    def run_command(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> LearningDeckCommandResult:
        """Run one shell command in the sandbox."""

    @abstractmethod
    def write_file(self, path: str, text: str) -> None:
        """Write UTF-8 text inside the sandbox workdir."""

    @abstractmethod
    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read UTF-8 text from the sandbox workdir."""

    @abstractmethod
    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Read raw bytes from the sandbox workdir."""

    @abstractmethod
    def list_files(self, path: str) -> list[str]:
        """List files below a sandbox workdir path."""

    @abstractmethod
    def close(self) -> None:
        """Release sandbox resources."""


@dataclass
class LocalLearningDeckSandboxSession(LearningDeckSandboxSession):
    """Local sandbox used by tests and explicit dependency injection."""

    root_dir: Path
    provider: str = "local"
    sandbox_id: str | None = None

    @classmethod
    def create(cls) -> LocalLearningDeckSandboxSession:
        return cls(root_dir=Path(tempfile.mkdtemp(prefix="newsly-learning-deck-")))

    def run_command(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> LearningDeckCommandResult:
        result = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=self.root_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        settings = get_settings()
        max_output_chars = settings.learning_sandbox_max_output_chars
        return LearningDeckCommandResult(
            stdout=_truncate_output(result.stdout or "", max_output_chars),
            stderr=_truncate_output(result.stderr or "", max_output_chars),
            exit_code=int(result.returncode),
        )

    def write_file(self, path: str, text: str) -> None:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        data = self._resolve_path(path).read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            raise LearningDeckSandboxError(f"Sandbox file exceeds {max_bytes} bytes: {path}")
        return data

    def list_files(self, path: str) -> list[str]:
        root = self._resolve_path(path)
        if not root.exists():
            return []
        sandbox_root = self.root_dir.resolve()
        return [
            child.relative_to(sandbox_root).as_posix()
            for child in sorted(root.rglob("*"))
            if child.is_file()
        ]

    def close(self) -> None:
        return

    def _resolve_path(self, path: str) -> Path:
        sandbox_root = self.root_dir.resolve()
        candidate = (sandbox_root / path.strip().lstrip("/")).resolve()
        if candidate != sandbox_root and sandbox_root not in candidate.parents:
            raise LearningDeckSandboxError("Sandbox path must stay inside the workdir")
        return candidate


class E2BLearningDeckSandboxSession(LearningDeckSandboxSession):
    """E2B-backed sandbox session for Learning Deck generation."""

    provider = "e2b"

    def __init__(self, *, user_id: int, run_id: int) -> None:
        settings = get_settings()
        api_key = settings.learning_sandbox_e2b_api_key
        if not api_key:
            raise LearningDeckSandboxError("E2B API key is not configured")

        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:  # pragma: no cover
            raise LearningDeckSandboxError("e2b-code-interpreter is not installed") from exc

        create_kwargs: dict[str, Any] = {
            "timeout": settings.learning_sandbox_timeout_seconds,
            "allow_internet_access": settings.learning_sandbox_allow_internet_access,
            "api_key": api_key,
            "metadata": {
                "feature": "learning_decks",
                "user_id": str(user_id),
                "run_id": str(run_id),
            },
        }
        if settings.learning_sandbox_template:
            create_kwargs["template"] = settings.learning_sandbox_template

        self._sandbox = Sandbox.create(**create_kwargs)
        self._workdir = PurePosixPath(settings.learning_sandbox_workdir)
        self._max_output_chars = settings.learning_sandbox_max_output_chars
        self.sandbox_id = _sandbox_identifier(self._sandbox)
        self._run_raw_command(f"mkdir -p {shlex.quote(self._workdir.as_posix())}")

        record_vendor_usage_out_of_band(
            provider="e2b",
            model=settings.learning_sandbox_template or "default",
            feature="learning_deck_sandbox",
            operation="learning_deck_sandbox.e2b_create",
            source="queue",
            usage={"request_count": 1},
            user_id=user_id,
            metadata={
                "run_id": run_id,
                "sandbox_id": self.sandbox_id,
                "allow_internet_access": settings.learning_sandbox_allow_internet_access,
                "timeout_seconds": settings.learning_sandbox_timeout_seconds,
            },
        )

    def run_command(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> LearningDeckCommandResult:
        try:
            result = self._sandbox.commands.run(
                command,
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
            raise LearningDeckSandboxError(f"Sandbox file exceeds {max_bytes} bytes: {path}")
        return data

    def list_files(self, path: str) -> list[str]:
        target = self._resolve_workspace_path(path)
        relative_target = _relative_to_workdir(target, self._workdir)
        command = f"find {shlex.quote(relative_target)} -type f | sort"
        result = self.run_command(command)
        if result.exit_code != 0:
            return []
        return [line.strip().lstrip("./") for line in result.stdout.splitlines() if line.strip()]

    def close(self) -> None:
        try:
            self._sandbox.kill()
        except Exception:
            return

    def _resolve_workspace_path(self, path: str) -> str:
        candidate = PurePosixPath(path.strip() or ".")
        if candidate.is_absolute():
            candidate = PurePosixPath(str(candidate).lstrip("/"))
        if ".." in candidate.parts:
            raise LearningDeckSandboxError("Sandbox path must stay inside the workdir")
        return (self._workdir / candidate).as_posix()

    def _run_raw_command(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> LearningDeckCommandResult:
        result = self._sandbox.commands.run(command, timeout=timeout_seconds)
        return self._normalize_command_result(result)

    def _normalize_command_result(self, result: object) -> LearningDeckCommandResult:
        return LearningDeckCommandResult(
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

    def _normalize_command_exit_exception(self, exc: Exception) -> LearningDeckCommandResult:
        return LearningDeckCommandResult(
            stdout=_truncate_output(str(getattr(exc, "stdout", "") or ""), self._max_output_chars),
            stderr=_truncate_output(str(getattr(exc, "stderr", "") or ""), self._max_output_chars),
            exit_code=int(getattr(exc, "exit_code", 1) or 1),
        )


def create_learning_deck_sandbox_session(
    *,
    user_id: int,
    run_id: int,
) -> LearningDeckSandboxSession:
    """Create the configured Learning Deck sandbox session."""
    provider = get_settings().learning_sandbox_provider
    if provider == "disabled":
        raise LearningDeckSandboxError("Learning Deck sandbox provider is disabled")
    if provider == "e2b":
        return E2BLearningDeckSandboxSession(user_id=user_id, run_id=run_id)
    raise LearningDeckSandboxError(f"Unsupported Learning Deck sandbox provider: {provider}")


def guess_asset_content_type(relative_path: str) -> str:
    """Return a safe content type for an artifact asset."""
    guessed, _encoding = mimetypes.guess_type(relative_path)
    return guessed or "application/octet-stream"


def _truncate_output(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}\n[... truncated ...]"


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
