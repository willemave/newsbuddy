"""Concrete VM session providers for generic LLM tasks."""

from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from pathlib import PurePosixPath
from time import monotonic, perf_counter
from typing import Any

import httpx
from e2b import CommandExitException, TimeoutException

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.agent_vm_e2b_config import default_network_denials
from app.services.agent_vm_e2b_pool import (
    E2B_SANDBOX_POOL,
    is_missing_e2b_sandbox_error,
    sandbox_identifier,
)
from app.services.agent_vm_io import (
    AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
    AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS,
    AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
    AGENT_VM_MAX_LISTED_FILES,
    bounded_file_read_limit,
    bounded_operation_timeout,
    command_event_text,
    remaining_deadline_seconds,
    truncate_output,
    validate_file_write_size,
)
from app.services.agent_vm_local import (
    close_process_local_agent_vm_sessions,
    create_local_agent_vm_session,
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
from app.services.agent_vm_template import (
    AGENT_VM_TEMPLATE_NAME,
    AGENT_VM_TEMPLATE_REVISION,
)
from app.services.vendor_costs import record_vendor_usage_out_of_band

logger = get_logger(__name__)


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
        self._cache_key = E2B_SANDBOX_POOL.cache_key(vm_namespace)
        self.vm_namespace = vm_namespace
        self._workdir = PurePosixPath(workspace_path)
        self.workspace_posix_root = self._workdir
        self._shared_workdir = PurePosixPath(shared_workspace_path)
        self._deadline = deadline
        self._max_output_chars = settings.llm_task_sandbox_max_output_chars
        self.sandbox_acquisition_ms = 0.0
        self.hydration_ms = 0.0
        bootstrap_command = (
            "mkdir -p "
            f"{shlex.quote(self._workdir.as_posix())} "
            f"{shlex.quote(self._shared_workdir.as_posix())}"
        )
        for attempt in range(2):
            acquisition_started_at = perf_counter()
            acquisition = E2B_SANDBOX_POOL.acquire(
                sandbox_class=Sandbox,
                vm_namespace=vm_namespace,
                user_id=user_id,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )
            elapsed_ms = (perf_counter() - acquisition_started_at) * 1000
            hydration_ms = acquisition.hydration.elapsed_ms if acquisition.hydration else 0.0
            self.sandbox_acquisition_ms += max(0.0, elapsed_ms - hydration_ms)
            self.hydration_ms += hydration_ms
            sandbox = acquisition.sandbox
            created = acquisition.created
            capabilities = acquisition.capabilities
            self._sandbox = sandbox
            try:
                self._run_raw_command(
                    bootstrap_command,
                    timeout_seconds=remaining_deadline_seconds(deadline),
                )
            except AgentVmDeadlineExceeded:
                E2B_SANDBOX_POOL.release(self._cache_key, sandbox)
                raise
            except Exception as exc:
                E2B_SANDBOX_POOL.discard(self._cache_key, sandbox)
                should_retry = attempt == 0 and not created and is_missing_e2b_sandbox_error(exc)
                if should_retry:
                    continue
                raise
            break

        self.sandbox_id = sandbox_identifier(self._sandbox)
        self.lease = AgentVmLease(
            provider=self.provider,
            vm_namespace=vm_namespace,
            sandbox_id=self.sandbox_id,
            reuse_scope="persistent_namespace",
            reused=not created,
            template_revision=AGENT_VM_TEMPLATE_REVISION,
            capabilities=capabilities,
        )
        if created:
            try:
                record_vendor_usage_out_of_band(
                    provider="e2b",
                    model=AGENT_VM_TEMPLATE_NAME,
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
                E2B_SANDBOX_POOL.discard(self._cache_key, sandbox)
                raise

    def execute_bash(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        on_stdout: Callable[[str], None] | None = None,
        max_output_chars: int | None = None,
    ) -> AgentCommandResult:
        effective_timeout = bounded_operation_timeout(
            deadline=self._deadline,
            requested_timeout=timeout_seconds,
            default_timeout=AGENT_VM_DEFAULT_COMMAND_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        try:
            run_kwargs: dict[str, Any] = {
                "cwd": self._workdir.as_posix(),
                "timeout": effective_timeout,
                "request_timeout": effective_timeout,
            }
            if on_stdout is not None:
                run_kwargs["on_stdout"] = lambda event: on_stdout(command_event_text(event))
            result = self._sandbox.commands.run(command, **run_kwargs)
        except CommandExitException as exc:
            return self._normalize_command_exit_exception(
                exc,
                max_output_chars=max_output_chars,
            )
        except TimeoutException as exc:
            raise AgentVmDeadlineExceeded("Agent VM command deadline was exceeded") from exc
        except Exception as exc:
            self._evict_if_missing(exc)
            raise
        return self._normalize_command_result(result, max_output_chars=max_output_chars)

    def write_file(self, path: str, text: str) -> None:
        validate_file_write_size(path, text)
        destination = self._resolve_workspace_path(path)
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
        effective_max_bytes = bounded_file_read_limit(max_bytes)
        operation_timeout = self._file_operation_timeout()
        operation_deadline = monotonic() + operation_timeout
        try:
            stream = self._sandbox.files.read(
                self._resolve_read_path(path),
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
        target = self._resolve_read_path(path)
        result = self.execute_bash(
            f"find {shlex.quote(target)} -type f | sort | head -n {AGENT_VM_MAX_LISTED_FILES + 1}",
            timeout_seconds=self._file_operation_timeout(),
        )
        if result.exit_code != 0:
            return []
        if "[... truncated ...]" in result.stdout:
            raise AgentVmError("VM file listing exceeded the bounded command output")
        files: list[str] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            candidate = PurePosixPath(line)
            try:
                files.append(candidate.relative_to(self._workdir).as_posix())
            except ValueError:
                files.append(candidate.as_posix())
        if len(files) > AGENT_VM_MAX_LISTED_FILES:
            raise AgentVmError(f"VM file listing exceeds {AGENT_VM_MAX_LISTED_FILES} files: {path}")
        return files

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        E2B_SANDBOX_POOL.release(self._cache_key, self._sandbox)

    def set_allowed_outbound_hosts(self, hosts: list[str]) -> None:
        """Restrict this sandbox to the exact public candidate hosts."""
        selectors = sorted({host.strip().lower() for host in hosts if host.strip()})
        self._sandbox.update_network(
            {
                "allow_internet_access": True,
                "allow_out": selectors,
                "deny_out": [
                    "0.0.0.0/0",
                    *default_network_denials(get_settings().public_base_url),
                ],
            },
            request_timeout=self._file_operation_timeout(),
        )

    def reset_network_policy(self) -> None:
        """Return a shared sandbox to deny-by-default egress."""
        self._sandbox.update_network(
            {
                "allow_internet_access": False,
                "allow_out": [],
                "deny_out": default_network_denials(get_settings().public_base_url),
            },
            request_timeout=self._file_operation_timeout(),
        )

    def resolve_relative_path(self, path: str) -> str:
        return resolve_workspace_relative_path(
            path,
            workspace_root=self.workspace_posix_root,
        ).as_posix()

    def _resolve_workspace_path(self, path: str) -> str:
        relative_path = PurePosixPath(self.resolve_relative_path(path))
        return (self.workspace_posix_root / relative_path).as_posix()

    def _resolve_read_path(self, path: str) -> str:
        candidate = PurePosixPath(path.strip() or ".")
        if candidate.is_absolute():
            if ".." in candidate.parts or not (
                candidate == PurePosixPath("/data") or PurePosixPath("/data") in candidate.parents
            ):
                raise AgentVmPathError("VM reads must stay inside the workspace or /data")
            return candidate.as_posix()
        return self._resolve_workspace_path(path)

    def _run_raw_command(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentCommandResult:
        effective_timeout = bounded_operation_timeout(
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
        return bounded_operation_timeout(
            deadline=self._deadline,
            requested_timeout=requested_timeout,
            default_timeout=AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS,
            maximum_timeout=AGENT_VM_FILE_OPERATION_TIMEOUT_SECONDS,
        )

    def _normalize_command_result(
        self,
        result: object,
        *,
        max_output_chars: int | None = None,
    ) -> AgentCommandResult:
        output_limit = max_output_chars or self._max_output_chars
        return AgentCommandResult(
            stdout=truncate_output(
                str(getattr(result, "stdout", "") or ""),
                output_limit,
            ),
            stderr=truncate_output(
                str(getattr(result, "stderr", "") or ""),
                output_limit,
            ),
            exit_code=int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0),
        )

    def _normalize_command_exit_exception(
        self,
        exc: Exception,
        *,
        max_output_chars: int | None = None,
    ) -> AgentCommandResult:
        output_limit = max_output_chars or self._max_output_chars
        return AgentCommandResult(
            stdout=truncate_output(str(getattr(exc, "stdout", "") or ""), output_limit),
            stderr=truncate_output(str(getattr(exc, "stderr", "") or ""), output_limit),
            exit_code=int(getattr(exc, "exit_code", 1) or 1),
        )

    def _evict_if_missing(self, exc: Exception) -> None:
        if is_missing_e2b_sandbox_error(exc):
            E2B_SANDBOX_POOL.evict(self._cache_key, self._sandbox)


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
    remaining_deadline_seconds(deadline)
    settings = get_settings()
    provider = settings.llm_task_sandbox_provider
    if provider == "disabled":
        raise AgentVmError("LLM task sandbox provider is disabled")
    if provider == "local":
        return create_local_agent_vm_session(
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
    E2B_SANDBOX_POOL.evict(session._cache_key, session._sandbox)


def close_process_agent_vm_sessions() -> None:
    """Detach process handles without destroying persistent E2B sandboxes."""
    sandbox_count = E2B_SANDBOX_POOL.close()
    local_workspace_count = close_process_local_agent_vm_sessions()
    if sandbox_count or local_workspace_count:
        logger.info(
            "Released process-scoped agent VM handles",
            extra={
                "component": "llm_task_sandbox",
                "operation": "process_shutdown",
                "context_data": {
                    "sandbox_count": sandbox_count,
                    "persistent_sandbox_count": sandbox_count,
                    "local_workspace_count": local_workspace_count,
                },
            },
        )
