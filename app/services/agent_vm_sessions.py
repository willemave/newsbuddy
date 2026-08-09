"""Concrete VM session providers for generic LLM tasks."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any

import httpx
from e2b import CommandExitException, SandboxNotFoundException, TimeoutException

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.agent_vm_capabilities import (
    probe_configured_e2b_sandbox,
    probe_default_e2b_sandbox,
)
from app.services.agent_vm_runtime import (
    AgentCommandResult,
    AgentVmDeadlineExceeded,
    AgentVmError,
    AgentVmFileSizeLimitExceeded,
    AgentVmLease,
    AgentVmSession,
)
from app.services.vendor_costs import record_vendor_usage_out_of_band

_PROCESS_LOCAL_ROOTS_BY_NAMESPACE: dict[str, Path] = {}
_PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE: dict[tuple[str, str], Any] = {}
_PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE: dict[tuple[str, str], dict[str, Any]] = {}
_PROCESS_LOCAL_AGENT_VM_LOCK = threading.RLock()
_PROCESS_LOCAL_AGENT_VM_CONDITION = threading.Condition(_PROCESS_LOCAL_AGENT_VM_LOCK)
_PROCESS_LOCAL_E2B_LOCKS_BY_NAMESPACE: weakref.WeakValueDictionary[
    tuple[str, str], threading.Lock
] = weakref.WeakValueDictionary()
_PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS: dict[int, int] = {}
_PROCESS_LOCAL_E2B_PENDING_KILLS: dict[int, Any] = {}
_PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS = 0
_PROCESS_LOCAL_E2B_DRAINING = False
E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS = 5.0
E2B_MAX_IDLE_CACHED_SESSIONS = 8
AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0
AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS = 300.0
AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS = 30.0
AGENT_VM_MAX_FILE_BYTES = 20_000_000
AGENT_VM_MAX_LISTED_FILES = 500
logger = get_logger(__name__)


@dataclass
class LocalAgentVmSession(AgentVmSession):
    """Local filesystem-backed VM session used by tests and explicit local dev."""

    vm_namespace: str
    namespace_root: Path
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
    ) -> AgentCommandResult:
        effective_timeout = _bounded_operation_timeout(
            deadline=self.deadline,
            requested_timeout=timeout_seconds,
            default_timeout=AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            result = subprocess.run(
                ["/bin/bash", "-lc", _with_workspace_tool_env(command)],
                cwd=self.workspace_root,
                env=_local_agent_process_env(),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentVmDeadlineExceeded("Agent VM command deadline was exceeded") from exc
        max_output_chars = get_settings().llm_task_sandbox_max_output_chars
        return AgentCommandResult(
            stdout=_truncate_output(result.stdout or "", max_output_chars),
            stderr=_truncate_output(result.stderr or "", max_output_chars),
            exit_code=int(result.returncode),
        )

    def write_file(self, path: str, text: str) -> None:
        _remaining_deadline_seconds(self.deadline)
        _validate_file_write_size(path, text)
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        _remaining_deadline_seconds(self.deadline)
        effective_max_bytes = _bounded_file_read_limit(max_bytes)
        with self._resolve_workspace_path(path).open("rb") as handle:
            data = handle.read(effective_max_bytes + 1)
        if len(data) > effective_max_bytes:
            raise AgentVmFileSizeLimitExceeded(f"VM file exceeds limit: {path}")
        return data

    def list_files(self, path: str = ".") -> list[str]:
        _remaining_deadline_seconds(self.deadline)
        root = self._resolve_workspace_path(path)
        if not root.exists():
            return []
        workspace_root = self.workspace_root.resolve()
        files: list[str] = []
        for child in root.rglob("*"):
            _remaining_deadline_seconds(self.deadline)
            if not child.is_file():
                continue
            files.append(child.relative_to(workspace_root).as_posix())
            if len(files) > AGENT_VM_MAX_LISTED_FILES:
                raise AgentVmError(
                    f"VM file listing exceeds {AGENT_VM_MAX_LISTED_FILES} files: {path}"
                )
        return sorted(files)

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
        deadline: float | None = None,
    ) -> None:
        settings = get_settings()
        api_key = settings.llm_task_sandbox_e2b_api_key
        if not api_key:
            raise AgentVmError("E2B API key is not configured for LLM tasks")
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:  # pragma: no cover
            raise AgentVmError("e2b-code-interpreter is not installed") from exc

        self._close_lock = threading.Lock()
        self._closed = False
        self._cache_key = _e2b_cache_key(vm_namespace, settings.llm_task_sandbox_template)
        self.vm_namespace = vm_namespace
        self._workdir = PurePosixPath(workspace_path)
        self._shared_workdir = PurePosixPath(shared_workspace_path)
        self._deadline = deadline
        self._max_output_chars = settings.llm_task_sandbox_max_output_chars
        bootstrap_command = (
            "mkdir -p "
            f"{shlex.quote(self._workdir.as_posix())} "
            f"{shlex.quote(self._shared_workdir.as_posix())}"
        )
        for attempt in range(2):
            sandbox, created, capabilities = _get_or_create_e2b_sandbox(
                sandbox_class=Sandbox,
                vm_namespace=vm_namespace,
                user_id=user_id,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )
            self._sandbox = sandbox
            try:
                self._run_raw_command(
                    bootstrap_command,
                    timeout_seconds=_remaining_deadline_seconds(deadline),
                )
            except AgentVmDeadlineExceeded:
                _release_e2b_sandbox_lease(self._cache_key, sandbox)
                raise
            except Exception as exc:
                _discard_e2b_sandbox_lease(self._cache_key, sandbox)
                should_retry = attempt == 0 and not created and _is_missing_e2b_sandbox_error(exc)
                if should_retry:
                    continue
                raise
            break

        self.sandbox_id = _sandbox_identifier(self._sandbox)
        self.lease = AgentVmLease(
            provider=self.provider,
            vm_namespace=vm_namespace,
            sandbox_id=self.sandbox_id,
            reuse_scope="process_namespace",
            reused=not created,
            template_revision=settings.llm_task_sandbox_template,
            capabilities=capabilities,
        )
        if created:
            try:
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
            except Exception:
                _discard_e2b_sandbox_lease(self._cache_key, sandbox)
                raise

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        effective_timeout = _bounded_operation_timeout(
            deadline=self._deadline,
            requested_timeout=timeout_seconds,
            default_timeout=AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            result = self._sandbox.commands.run(
                _with_workspace_tool_env(command),
                cwd=self._workdir.as_posix(),
                timeout=effective_timeout,
                request_timeout=effective_timeout,
            )
        except CommandExitException as exc:
            return self._normalize_command_exit_exception(exc)
        except TimeoutException as exc:
            raise AgentVmDeadlineExceeded("Agent VM command deadline was exceeded") from exc
        except Exception as exc:
            self._evict_if_missing(exc)
            raise
        return self._normalize_command_result(result)

    def write_file(self, path: str, text: str) -> None:
        _validate_file_write_size(path, text)
        operation_timeout = self._file_operation_timeout()
        destination = self._resolve_workspace_path(path)
        parent = PurePosixPath(destination).parent
        self._run_raw_command(
            f"mkdir -p {shlex.quote(parent.as_posix())}",
            timeout_seconds=operation_timeout,
        )
        try:
            self._sandbox.files.write(
                destination,
                text,
                request_timeout=self._file_operation_timeout(),
            )
        except (TimeoutException, httpx.TimeoutException) as exc:
            raise AgentVmDeadlineExceeded("Agent VM file-write deadline was exceeded") from exc
        except Exception as exc:
            self._evict_if_missing(exc)
            raise

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        return self.read_file_bytes(path, max_bytes=max_bytes).decode("utf-8")

    def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        effective_max_bytes = _bounded_file_read_limit(max_bytes)
        operation_timeout = self._file_operation_timeout()
        operation_deadline = monotonic() + operation_timeout
        try:
            stream = self._sandbox.files.read(
                self._resolve_workspace_path(path),
                format="stream",
                request_timeout=operation_timeout,
                stream_idle_timeout=operation_timeout,
            )
            chunks: list[bytes] = []
            total_bytes = 0
            with stream:
                for chunk in stream:
                    if monotonic() >= operation_deadline:
                        raise AgentVmDeadlineExceeded("Agent VM file-read deadline was exceeded")
                    total_bytes += len(chunk)
                    if total_bytes > effective_max_bytes:
                        raise AgentVmFileSizeLimitExceeded(f"VM file exceeds limit: {path}")
                    chunks.append(bytes(chunk))
            data = b"".join(chunks)
        except (TimeoutException, httpx.TimeoutException) as exc:
            raise AgentVmDeadlineExceeded("Agent VM file-read deadline was exceeded") from exc
        except Exception as exc:
            self._evict_if_missing(exc)
            raise
        return data

    def list_files(self, path: str = ".") -> list[str]:
        target = self._resolve_workspace_path(path)
        relative_target = PurePosixPath(target).relative_to(self._workdir).as_posix()
        result = self.execute_bash(
            f"find {shlex.quote(relative_target)} -type f | sort | "
            f"head -n {AGENT_VM_MAX_LISTED_FILES + 1}",
            timeout_seconds=self._file_operation_timeout(),
        )
        if result.exit_code != 0:
            return []
        if "[... truncated ...]" in result.stdout:
            raise AgentVmError("VM file listing exceeded the bounded command output")
        files = [
            line.strip().removeprefix("./") for line in result.stdout.splitlines() if line.strip()
        ]
        if len(files) > AGENT_VM_MAX_LISTED_FILES:
            raise AgentVmError(f"VM file listing exceeds {AGENT_VM_MAX_LISTED_FILES} files: {path}")
        return files

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        _release_e2b_sandbox_lease(self._cache_key, self._sandbox)

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
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        effective_timeout = _bounded_operation_timeout(
            deadline=self._deadline,
            requested_timeout=timeout_seconds,
            default_timeout=AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            result = self._sandbox.commands.run(
                command,
                timeout=effective_timeout,
                request_timeout=effective_timeout,
            )
        except TimeoutException as exc:
            raise AgentVmDeadlineExceeded("Agent VM command deadline was exceeded") from exc
        except Exception as exc:
            self._evict_if_missing(exc)
            raise
        return self._normalize_command_result(result)

    def _file_operation_timeout(self, requested_timeout: float | None = None) -> float:
        return _bounded_operation_timeout(
            deadline=self._deadline,
            requested_timeout=requested_timeout,
            default_timeout=AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS,
        )

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

    def _evict_if_missing(self, exc: Exception) -> None:
        if _is_missing_e2b_sandbox_error(exc):
            _evict_e2b_sandbox(self._cache_key, self._sandbox)


def create_agent_vm_session(
    *,
    user_id: int,
    llm_task_id: int,
    vm_namespace: str,
    workspace_path: str,
    shared_workspace_path: str,
    feature: str,
    deadline: float | None = None,
) -> AgentVmSession:
    """Create or attach to the configured generic LLM task VM session."""
    _remaining_deadline_seconds(deadline)
    settings = get_settings()
    provider = settings.llm_task_sandbox_provider
    if provider == "disabled":
        raise AgentVmError("LLM task sandbox provider is disabled")
    if provider == "local":
        return _create_local_agent_vm_session(
            vm_namespace=vm_namespace,
            workspace_path=workspace_path,
            shared_workspace_path=shared_workspace_path,
            deadline=deadline,
        )
    if provider == "e2b":
        return E2BAgentVmSession(
            user_id=user_id,
            llm_task_id=llm_task_id,
            vm_namespace=vm_namespace,
            workspace_path=workspace_path,
            shared_workspace_path=shared_workspace_path,
            feature=feature,
            deadline=deadline,
        )
    raise AgentVmError(f"Unsupported LLM task sandbox provider: {provider}")


def evict_agent_vm_session(session: AgentVmSession) -> None:
    """Evict the exact cached E2B sandbox backing one unhealthy session."""
    if not isinstance(session, E2BAgentVmSession):
        return
    _evict_e2b_sandbox(session._cache_key, session._sandbox)


def _create_local_agent_vm_session(
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


def _get_or_create_e2b_sandbox(
    *,
    sandbox_class: type[Any],
    vm_namespace: str,
    user_id: int,
    feature: str,
    settings: Any,
    deadline: float | None = None,
) -> tuple[Any, bool, dict[str, Any]]:
    cache_key = _e2b_cache_key(vm_namespace, settings.llm_task_sandbox_template)
    namespace_lock = _begin_e2b_acquisition(cache_key, deadline=deadline)
    lock_acquired = False
    try:
        remaining = _remaining_deadline_seconds(deadline)
        lock_acquired = (
            namespace_lock.acquire()
            if remaining is None
            else namespace_lock.acquire(timeout=remaining)
        )
        if not lock_acquired:
            raise AgentVmDeadlineExceeded(
                "Agent VM deadline expired while waiting for the namespace"
            )
        try:
            return _get_or_create_e2b_sandbox_for_namespace(
                cache_key=cache_key,
                sandbox_class=sandbox_class,
                vm_namespace=vm_namespace,
                user_id=user_id,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )
        except TimeoutException as exc:
            if deadline is not None:
                raise AgentVmDeadlineExceeded("Agent VM request deadline was exceeded") from exc
            raise
    finally:
        if lock_acquired:
            namespace_lock.release()
        _finish_e2b_acquisition()


def _get_or_create_e2b_sandbox_for_namespace(
    *,
    cache_key: tuple[str, str],
    sandbox_class: type[Any],
    vm_namespace: str,
    user_id: int,
    feature: str,
    settings: Any,
    deadline: float | None = None,
) -> tuple[Any, bool, dict[str, Any]]:
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        cached = _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.get(cache_key)
        cached_capabilities = _PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE.get(cache_key, {})
    if cached is not None:
        try:
            _refresh_e2b_sandbox_timeout(
                cached,
                timeout_seconds=settings.llm_task_sandbox_timeout_seconds,
                request_timeout_seconds=_remaining_deadline_seconds(deadline),
            )
        except TimeoutException as exc:
            if deadline is not None:
                raise AgentVmDeadlineExceeded("Agent VM request deadline was exceeded") from exc
            _evict_e2b_sandbox_for_namespace(cache_key, cached)
            raise
        except Exception as exc:
            _evict_e2b_sandbox_for_namespace(
                cache_key,
                cached,
            )
            if not _is_missing_e2b_sandbox_error(exc):
                raise
        else:
            _acquire_e2b_sandbox_lease(cached)
            return cached, False, cached_capabilities

    create_kwargs: dict[str, Any] = {
        "timeout": settings.llm_task_sandbox_timeout_seconds,
        "allow_internet_access": settings.llm_task_sandbox_allow_internet_access,
        "api_key": settings.llm_task_sandbox_e2b_api_key,
        "metadata": {
            "feature": feature,
            "user_id": str(user_id),
            "vm_namespace": vm_namespace,
        },
    }
    if settings.llm_task_sandbox_template:
        create_kwargs["template"] = settings.llm_task_sandbox_template
    request_timeout = _remaining_deadline_seconds(deadline)
    if request_timeout is not None:
        create_kwargs["request_timeout"] = request_timeout
    sandbox = sandbox_class.create(**create_kwargs)
    try:
        capability_probe = (
            probe_configured_e2b_sandbox
            if settings.llm_task_sandbox_template
            else probe_default_e2b_sandbox
        )
        capabilities = capability_probe(
            sandbox,
            request_timeout_seconds=_remaining_deadline_seconds(deadline),
        )
    except Exception:
        _kill_e2b_sandbox(
            sandbox,
            operation="failed_preflight_cleanup",
        )
        raise
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE[cache_key] = sandbox
        _PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE[cache_key] = capabilities
        _acquire_e2b_sandbox_lease_locked(sandbox)
    return sandbox, True, capabilities


def _begin_e2b_acquisition(
    cache_key: tuple[str, str],
    *,
    deadline: float | None = None,
) -> threading.Lock:
    global _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS

    with _PROCESS_LOCAL_AGENT_VM_CONDITION:
        while _PROCESS_LOCAL_E2B_DRAINING:
            remaining = _remaining_deadline_seconds(deadline)
            _PROCESS_LOCAL_AGENT_VM_CONDITION.wait(timeout=remaining)
        namespace_lock = _e2b_namespace_lock(cache_key)
        _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS += 1
        return namespace_lock


def _finish_e2b_acquisition() -> None:
    global _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS

    idle_sandboxes_to_kill: list[tuple[tuple[str, str], object]] = []
    with _PROCESS_LOCAL_AGENT_VM_CONDITION:
        _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS -= 1
        if _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS == 0:
            idle_sandboxes_to_kill = _detach_excess_idle_e2b_sandboxes_locked()
            _PROCESS_LOCAL_AGENT_VM_CONDITION.notify_all()
    _kill_detached_idle_e2b_sandboxes(idle_sandboxes_to_kill)


def _refresh_e2b_sandbox_timeout(
    sandbox: object,
    *,
    timeout_seconds: int,
    request_timeout_seconds: float | None = None,
) -> None:
    set_timeout = getattr(sandbox, "set_timeout", None)
    if callable(set_timeout):
        set_timeout(timeout_seconds, request_timeout=request_timeout_seconds)


def _acquire_e2b_sandbox_lease(sandbox: object) -> None:
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        _acquire_e2b_sandbox_lease_locked(sandbox)


def _acquire_e2b_sandbox_lease_locked(sandbox: object) -> None:
    sandbox_key = id(sandbox)
    _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS[sandbox_key] = (
        _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.get(sandbox_key, 0) + 1
    )


def _release_e2b_sandbox_lease(
    cache_key: tuple[str, str],
    sandbox: object,
) -> None:
    sandbox_to_kill: object | None = None
    idle_sandboxes_to_kill: list[tuple[tuple[str, str], object]] = []
    sandbox_key = id(sandbox)
    with _PROCESS_LOCAL_AGENT_VM_CONDITION:
        active_count = _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.get(sandbox_key, 0)
        if active_count <= 0:
            return
        if active_count > 1:
            _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS[sandbox_key] = active_count - 1
        else:
            _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.pop(sandbox_key, None)
            sandbox_to_kill = _PROCESS_LOCAL_E2B_PENDING_KILLS.pop(sandbox_key, None)
            if (
                sandbox_to_kill is None
                and _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.get(cache_key) is sandbox
            ):
                # Reinsert this newly idle sandbox before trimming the oldest entries.
                _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.pop(cache_key)
                _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE[cache_key] = sandbox
                idle_sandboxes_to_kill = _detach_excess_idle_e2b_sandboxes_locked()
            _PROCESS_LOCAL_AGENT_VM_CONDITION.notify_all()
    if sandbox_to_kill is not None:
        _kill_e2b_sandbox(
            sandbox_to_kill,
            operation="evicted_lease_released",
        )
    _kill_detached_idle_e2b_sandboxes(idle_sandboxes_to_kill)


def _kill_detached_idle_e2b_sandboxes(
    detached: list[tuple[tuple[str, str], object]],
) -> None:
    for idle_cache_key, idle_sandbox in detached:
        _kill_e2b_sandbox(idle_sandbox, operation="idle_cache_limit")
        logger.info(
            "Evicted idle E2B sandbox from process cache",
            extra={
                "component": "llm_task_sandbox",
                "operation": "idle_cache_limit",
                "vm_namespace": idle_cache_key[1],
                "sandbox_id": _sandbox_identifier(idle_sandbox),
            },
        )


def _detach_excess_idle_e2b_sandboxes_locked() -> list[tuple[tuple[str, str], object]]:
    """Detach least-recently-idle sandboxes after all acquisitions settle."""
    if _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS:
        return []
    idle_entries = [
        (idle_cache_key, idle_sandbox)
        for idle_cache_key, idle_sandbox in _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.items()
        if _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.get(id(idle_sandbox), 0) == 0
    ]
    excess_count = len(idle_entries) - E2B_MAX_IDLE_CACHED_SESSIONS
    if excess_count <= 0:
        return []

    detached: list[tuple[tuple[str, str], object]] = []
    for idle_cache_key, idle_sandbox in idle_entries[:excess_count]:
        if _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.get(idle_cache_key) is not idle_sandbox:
            continue
        _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.pop(idle_cache_key, None)
        _PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE.pop(idle_cache_key, None)
        detached.append((idle_cache_key, idle_sandbox))
    return detached


def _discard_e2b_sandbox_lease(
    cache_key: tuple[str, str],
    sandbox: object,
) -> None:
    _evict_e2b_sandbox(cache_key, sandbox)
    _release_e2b_sandbox_lease(cache_key, sandbox)


def _evict_e2b_sandbox(
    cache_key: tuple[str, str],
    sandbox: object,
) -> None:
    namespace_lock = _e2b_namespace_lock(cache_key)
    with namespace_lock:
        _evict_e2b_sandbox_for_namespace(cache_key, sandbox)


def _evict_e2b_sandbox_for_namespace(
    cache_key: tuple[str, str],
    sandbox: object,
) -> None:
    sandbox_to_kill: object | None = None
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        cached = _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.get(cache_key)
        if cached is not sandbox:
            return
        _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.pop(cache_key, None)
        _PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE.pop(cache_key, None)
        sandbox_key = id(sandbox)
        if _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.get(sandbox_key, 0) > 0:
            _PROCESS_LOCAL_E2B_PENDING_KILLS[sandbox_key] = sandbox
        else:
            sandbox_to_kill = sandbox
    if sandbox_to_kill is not None:
        _kill_e2b_sandbox(
            sandbox_to_kill,
            operation="evict_e2b_sandbox",
        )
    logger.warning(
        "Evicted stale E2B sandbox from process cache",
        extra={
            "component": "llm_task_sandbox",
            "operation": "evict_e2b_sandbox",
            "vm_namespace": cache_key[1],
            "sandbox_id": _sandbox_identifier(sandbox),
        },
    )


def _e2b_namespace_lock(cache_key: tuple[str, str]) -> threading.Lock:
    with _PROCESS_LOCAL_AGENT_VM_LOCK:
        namespace_lock = _PROCESS_LOCAL_E2B_LOCKS_BY_NAMESPACE.get(cache_key)
        if namespace_lock is None:
            namespace_lock = threading.Lock()
            _PROCESS_LOCAL_E2B_LOCKS_BY_NAMESPACE[cache_key] = namespace_lock
        return namespace_lock


def _kill_e2b_sandbox(
    sandbox: object,
    *,
    operation: str,
) -> None:
    kill = getattr(sandbox, "kill", None)
    if callable(kill):
        try:
            kill(request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Unable to kill stale E2B sandbox after eviction: %s",
                exc,
                extra={
                    "component": "llm_task_sandbox",
                    "operation": operation,
                    "sandbox_id": _sandbox_identifier(sandbox),
                },
            )


def close_process_agent_vm_sessions() -> None:
    """Detach process caches and release sandboxes once active leases close."""
    global _PROCESS_LOCAL_E2B_DRAINING

    with _PROCESS_LOCAL_AGENT_VM_CONDITION:
        while _PROCESS_LOCAL_E2B_DRAINING:
            _PROCESS_LOCAL_AGENT_VM_CONDITION.wait()
        _PROCESS_LOCAL_E2B_DRAINING = True
        while _PROCESS_LOCAL_E2B_ACTIVE_ACQUISITIONS:
            _PROCESS_LOCAL_AGENT_VM_CONDITION.wait()
        cached_sandboxes = list(
            {
                id(sandbox): sandbox
                for sandbox in _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.values()
            }.values()
        )
        sandboxes: list[object] = []
        for sandbox in cached_sandboxes:
            sandbox_key = id(sandbox)
            if _PROCESS_LOCAL_E2B_ACTIVE_SESSION_COUNTS.get(sandbox_key, 0) > 0:
                _PROCESS_LOCAL_E2B_PENDING_KILLS[sandbox_key] = sandbox
            else:
                sandboxes.append(sandbox)
        local_roots = list(dict.fromkeys(_PROCESS_LOCAL_ROOTS_BY_NAMESPACE.values()))
        _PROCESS_LOCAL_E2B_SANDBOXES_BY_NAMESPACE.clear()
        _PROCESS_LOCAL_E2B_CAPABILITIES_BY_NAMESPACE.clear()
        _PROCESS_LOCAL_E2B_LOCKS_BY_NAMESPACE.clear()
        _PROCESS_LOCAL_ROOTS_BY_NAMESPACE.clear()

    try:
        for sandbox in sandboxes:
            _kill_e2b_sandbox(sandbox, operation="process_shutdown")
        for root in local_roots:
            shutil.rmtree(root, ignore_errors=True)

        if cached_sandboxes or local_roots:
            logger.info(
                "Released process-scoped agent VM resources",
                extra={
                    "component": "llm_task_sandbox",
                    "operation": "process_shutdown",
                    "context_data": {
                        "sandbox_count": len(cached_sandboxes),
                        "deferred_sandbox_count": len(cached_sandboxes) - len(sandboxes),
                        "local_workspace_count": len(local_roots),
                    },
                },
            )
    finally:
        with _PROCESS_LOCAL_AGENT_VM_CONDITION:
            _PROCESS_LOCAL_E2B_DRAINING = False
            _PROCESS_LOCAL_AGENT_VM_CONDITION.notify_all()


def _e2b_cache_key(vm_namespace: str, template: str | None) -> tuple[str, str]:
    return (template or "default", vm_namespace)


def _is_missing_e2b_sandbox_error(exc: Exception) -> bool:
    if isinstance(exc, SandboxNotFoundException):
        return True
    message = str(exc).lower()
    return "sandbox was not found" in message or "sandbox not found" in message


def _sandbox_identifier(sandbox: object) -> str | None:
    for attr in ("sandbox_id", "id", "sandboxId"):
        value = getattr(sandbox, attr, None)
        if value:
            return str(value)
    return None


def _remaining_deadline_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AgentVmDeadlineExceeded("Agent VM deadline was exceeded")
    return remaining


def _bounded_operation_timeout(
    *,
    deadline: float | None,
    requested_timeout: float | None,
    default_timeout: float,
    maximum_timeout: float,
) -> float:
    timeout = default_timeout if requested_timeout is None else float(requested_timeout)
    if timeout <= 0:
        raise AgentVmError("Agent VM operation timeout must be positive")
    timeout = min(timeout, maximum_timeout)
    remaining = _remaining_deadline_seconds(deadline)
    return min(timeout, remaining) if remaining is not None else timeout


def _bounded_file_read_limit(max_bytes: int | None) -> int:
    if max_bytes is not None and max_bytes <= 0:
        raise AgentVmError("Agent VM file-read limit must be positive")
    return min(max_bytes or AGENT_VM_MAX_FILE_BYTES, AGENT_VM_MAX_FILE_BYTES)


def _validate_file_write_size(path: str, text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > AGENT_VM_MAX_FILE_BYTES:
        raise AgentVmFileSizeLimitExceeded(f"VM file exceeds limit: {path}")
