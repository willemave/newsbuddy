"""Process cache and durable acquisition state machine for E2B sandboxes."""

from __future__ import annotations

import threading
import weakref
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from e2b import SandboxNotFoundException, TimeoutException

from app.core.logging import get_logger
from app.services.agent_vm_capabilities import probe_agent_vm_template
from app.services.agent_vm_corpus import (
    AgentDataHydrationResult,
    AgentDataRevisionError,
    hydrate_e2b_agent_data,
)
from app.services.agent_vm_e2b_config import build_e2b_create_kwargs
from app.services.agent_vm_io import remaining_deadline_seconds
from app.services.agent_vm_runtime import AgentVmDeadlineExceeded, AgentVmError
from app.services.agent_vm_security import harden_canonical_agent_vm
from app.services.agent_vm_state import LockedAgentVmState, locked_agent_vm_state
from app.services.agent_vm_template import AGENT_VM_TEMPLATE_REVISION

E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS = 5.0
E2B_MAX_IDLE_CACHED_SESSIONS = 8
logger = get_logger(__name__)


@dataclass(frozen=True)
class E2BSandboxAcquisition:
    sandbox: Any
    created: bool
    capabilities: dict[str, Any]
    hydration: AgentDataHydrationResult | None
    created_snapshot_id: str | None = None


class E2BSandboxPool:
    """Own process handles while PostgreSQL owns persistent sandbox identity."""

    def __init__(self) -> None:
        self._sandboxes: dict[tuple[str, str], Any] = {}
        self._capabilities_by_revision: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._namespace_locks: weakref.WeakValueDictionary[tuple[str, str], threading.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._active_session_counts: dict[int, int] = {}
        self._pending_kills: dict[int, Any] = {}
        self._active_acquisitions = 0
        self._draining = False

    def cache_key(self, vm_namespace: str) -> tuple[str, str]:
        return (AGENT_VM_TEMPLATE_REVISION, vm_namespace)

    def acquire(
        self,
        *,
        sandbox_class: type[Any],
        vm_namespace: str,
        user_id: int,
        feature: str,
        settings: Any,
        deadline: float | None = None,
    ) -> E2BSandboxAcquisition:
        cache_key = self.cache_key(vm_namespace)
        namespace_lock = self._begin_acquisition(cache_key, deadline=deadline)
        lock_acquired = False
        try:
            remaining = remaining_deadline_seconds(deadline)
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
                return self._acquire_namespace(
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
            self._finish_acquisition()

    def _acquire_namespace(
        self,
        *,
        cache_key: tuple[str, str],
        sandbox_class: type[Any],
        vm_namespace: str,
        user_id: int,
        feature: str,
        settings: Any,
        deadline: float | None,
    ) -> E2BSandboxAcquisition:
        acquisition: E2BSandboxAcquisition | None = None
        try:
            with locked_agent_vm_state(user_id=user_id, vm_namespace=vm_namespace) as state:
                acquisition = self._acquire_locked(
                    cache_key=cache_key,
                    sandbox_class=sandbox_class,
                    vm_namespace=vm_namespace,
                    user_id=user_id,
                    feature=feature,
                    settings=settings,
                    deadline=deadline,
                    state=state,
                )
        except Exception:
            if acquisition is not None and acquisition.created:
                self._kill_sandbox(acquisition.sandbox, operation="failed_state_commit_cleanup")
                self._delete_created_snapshot_best_effort(
                    sandbox_class,
                    snapshot_id=acquisition.created_snapshot_id,
                    api_key=settings.llm_task_sandbox_e2b_api_key,
                )
            raise
        with self._lock:
            self._sandboxes[cache_key] = acquisition.sandbox
            self._capabilities_by_revision[AGENT_VM_TEMPLATE_REVISION] = acquisition.capabilities
            self._acquire_lease_locked(acquisition.sandbox)
        return acquisition

    def _acquire_locked(
        self,
        *,
        cache_key: tuple[str, str],
        sandbox_class: type[Any],
        vm_namespace: str,
        user_id: int,
        feature: str,
        settings: Any,
        deadline: float | None,
        state: LockedAgentVmState,
    ) -> E2BSandboxAcquisition:
        self._discard_stale_template_state(
            state=state,
            sandbox_class=sandbox_class,
            api_key=settings.llm_task_sandbox_e2b_api_key,
        )
        cached = self._matching_cached_sandbox(cache_key, state)
        if cached is not None:
            try:
                cached.connect(
                    timeout=settings.llm_task_sandbox_timeout_seconds,
                    request_timeout=remaining_deadline_seconds(deadline),
                )
            except Exception as exc:
                if not is_missing_e2b_sandbox_error(exc):
                    raise
                self._detach_cached(cache_key, cached)
                state.set_sandbox(None, None)
            else:
                return self._finalize_existing_candidate(
                    cache_key=cache_key,
                    sandbox=cached,
                    sandbox_class=sandbox_class,
                    state=state,
                    vm_namespace=vm_namespace,
                    user_id=user_id,
                    feature=feature,
                    settings=settings,
                    deadline=deadline,
                )

        sandbox: Any | None = None
        if state.sandbox_id:
            try:
                sandbox = sandbox_class.connect(
                    state.sandbox_id,
                    timeout=settings.llm_task_sandbox_timeout_seconds,
                    api_key=settings.llm_task_sandbox_e2b_api_key,
                    request_timeout=remaining_deadline_seconds(deadline),
                )
            except Exception as exc:
                if not is_missing_e2b_sandbox_error(exc):
                    raise
                state.set_sandbox(None, None)

        if sandbox is not None:
            return self._finalize_existing_candidate(
                cache_key=cache_key,
                sandbox=sandbox,
                sandbox_class=sandbox_class,
                state=state,
                vm_namespace=vm_namespace,
                user_id=user_id,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )

        return self._create_and_finalize(
            sandbox_class=sandbox_class,
            state=state,
            vm_namespace=vm_namespace,
            user_id=user_id,
            feature=feature,
            settings=settings,
            deadline=deadline,
        )

    def _finalize_existing_candidate(
        self,
        *,
        cache_key: tuple[str, str],
        sandbox: Any,
        sandbox_class: type[Any],
        state: LockedAgentVmState,
        vm_namespace: str,
        user_id: int,
        feature: str,
        settings: Any,
        deadline: float | None,
    ) -> E2BSandboxAcquisition:
        try:
            return self._finalize_candidate(
                sandbox=sandbox,
                state=state,
                user_id=user_id,
                settings=settings,
                deadline=deadline,
                created=False,
                canonical_create=False,
            )
        except AgentDataRevisionError:
            try:
                sandbox.kill(request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS)
            except Exception as exc:
                if not is_missing_e2b_sandbox_error(exc):
                    raise AgentVmError(
                        "Unable to destroy an E2B sandbox with a corrupt corpus revision"
                    ) from exc
            self._detach_cached(cache_key, sandbox)
            state.set_sandbox(None, None)
            return self._create_and_finalize(
                sandbox_class=sandbox_class,
                state=state,
                vm_namespace=vm_namespace,
                user_id=user_id,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )

    def _create_and_finalize(
        self,
        *,
        sandbox_class: type[Any],
        state: LockedAgentVmState,
        vm_namespace: str,
        user_id: int,
        feature: str,
        settings: Any,
        deadline: float | None,
    ) -> E2BSandboxAcquisition:
        snapshot_is_current = bool(
            state.snapshot_id and state.snapshot_template_revision == AGENT_VM_TEMPLATE_REVISION
        )
        template = state.snapshot_id if snapshot_is_current else None
        create_kwargs = build_e2b_create_kwargs(
            user_id=user_id,
            vm_namespace=vm_namespace,
            feature=feature,
            settings=settings,
            deadline=deadline,
            **({"template": template} if template else {}),
        )
        try:
            sandbox = sandbox_class.create(**create_kwargs)
        except Exception as exc:
            if not snapshot_is_current or not is_missing_e2b_snapshot_error(exc):
                raise
            state.set_snapshot(None, None)
            create_kwargs = build_e2b_create_kwargs(
                user_id=user_id,
                vm_namespace=vm_namespace,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )
            sandbox = sandbox_class.create(**create_kwargs)
            snapshot_is_current = False
        try:
            return self._finalize_candidate(
                sandbox=sandbox,
                state=state,
                user_id=user_id,
                settings=settings,
                deadline=deadline,
                created=True,
                canonical_create=not snapshot_is_current,
            )
        except AgentDataRevisionError:
            if not snapshot_is_current:
                self._kill_sandbox(sandbox, operation="corrupt_corpus_cleanup")
                raise
            self._kill_sandbox(sandbox, operation="corrupt_snapshot_clone_cleanup")
            self._delete_snapshot(
                sandbox_class,
                snapshot_id=state.snapshot_id,
                api_key=settings.llm_task_sandbox_e2b_api_key,
            )
            state.set_snapshot(None, None)
            create_kwargs = build_e2b_create_kwargs(
                user_id=user_id,
                vm_namespace=vm_namespace,
                feature=feature,
                settings=settings,
                deadline=deadline,
            )
            replacement = sandbox_class.create(**create_kwargs)
            try:
                return self._finalize_candidate(
                    sandbox=replacement,
                    state=state,
                    user_id=user_id,
                    settings=settings,
                    deadline=deadline,
                    created=True,
                    canonical_create=True,
                )
            except Exception:
                self._kill_sandbox(replacement, operation="failed_recovery_cleanup")
                raise
        except Exception:
            self._kill_sandbox(sandbox, operation="failed_preflight_cleanup")
            raise

    def _finalize_candidate(
        self,
        *,
        sandbox: Any,
        state: LockedAgentVmState,
        user_id: int,
        settings: Any,
        deadline: float | None,
        created: bool,
        canonical_create: bool,
    ) -> E2BSandboxAcquisition:
        if canonical_create:
            harden_canonical_agent_vm(
                sandbox,
                request_timeout_seconds=remaining_deadline_seconds(deadline),
            )
        capabilities = self._capabilities_by_revision.get(AGENT_VM_TEMPLATE_REVISION)
        if capabilities is None:
            capabilities = probe_agent_vm_template(
                sandbox,
                request_timeout_seconds=remaining_deadline_seconds(deadline),
            )

        hydration: AgentDataHydrationResult | None = None
        if user_id > 0 and state.db is not None:
            hydration = hydrate_e2b_agent_data(
                sandbox,
                state.db,
                user_id=user_id,
                deadline=deadline,
            )

        sandbox_id = sandbox_identifier(sandbox)
        if not sandbox_id:
            raise AgentVmError("E2B returned a sandbox without an identifier")

        created_snapshot_id: str | None = None
        if canonical_create and user_id > 0 and state.durable and not state.snapshot_id:
            created_snapshot_id = self._create_recovery_snapshot(
                sandbox=sandbox,
                state=state,
                settings=settings,
                deadline=deadline,
            )

        state.set_sandbox(sandbox_id, AGENT_VM_TEMPLATE_REVISION)
        return E2BSandboxAcquisition(
            sandbox=sandbox,
            created=created,
            capabilities=capabilities,
            hydration=hydration,
            created_snapshot_id=created_snapshot_id,
        )

    def _create_recovery_snapshot(
        self,
        *,
        sandbox: Any,
        state: LockedAgentVmState,
        settings: Any,
        deadline: float | None,
    ) -> str | None:
        try:
            snapshot = sandbox.create_snapshot(
                request_timeout=remaining_deadline_seconds(deadline),
            )
        except Exception as exc:  # snapshot recovery must not take down a healthy sandbox
            logger.warning(
                "Unable to create clean E2B recovery snapshot: %s",
                exc,
                extra={
                    "component": "llm_task_sandbox",
                    "operation": "create_recovery_snapshot",
                    "sandbox_id": sandbox_identifier(sandbox),
                },
            )
            with suppress(Exception):
                sandbox.connect(
                    timeout=settings.llm_task_sandbox_timeout_seconds,
                    request_timeout=remaining_deadline_seconds(deadline),
                )
            return None
        snapshot_id = str(getattr(snapshot, "snapshot_id", "") or "").strip()
        if not snapshot_id:
            logger.warning(
                "E2B returned a recovery snapshot without an identifier",
                extra={
                    "component": "llm_task_sandbox",
                    "operation": "create_recovery_snapshot",
                    "sandbox_id": sandbox_identifier(sandbox),
                },
            )
            sandbox.connect(
                timeout=settings.llm_task_sandbox_timeout_seconds,
                request_timeout=remaining_deadline_seconds(deadline),
            )
            return None
        try:
            sandbox.connect(
                timeout=settings.llm_task_sandbox_timeout_seconds,
                request_timeout=remaining_deadline_seconds(deadline),
            )
        except Exception:
            self._delete_created_snapshot_best_effort(
                type(sandbox),
                snapshot_id=snapshot_id,
                api_key=settings.llm_task_sandbox_e2b_api_key,
            )
            raise
        state.set_snapshot(
            snapshot_id,
            AGENT_VM_TEMPLATE_REVISION,
        )
        return snapshot_id

    def _discard_stale_template_state(
        self,
        *,
        state: LockedAgentVmState,
        sandbox_class: type[Any],
        api_key: str,
    ) -> None:
        if state.sandbox_id and state.template_revision != AGENT_VM_TEMPLATE_REVISION:
            self._kill_persisted_sandbox(
                sandbox_class,
                sandbox_id=state.sandbox_id,
                api_key=api_key,
            )
            state.set_sandbox(None, None)
        if state.snapshot_id and state.snapshot_template_revision != AGENT_VM_TEMPLATE_REVISION:
            self._delete_snapshot(
                sandbox_class,
                snapshot_id=state.snapshot_id,
                api_key=api_key,
            )
            state.set_snapshot(None, None)

    @staticmethod
    def _kill_persisted_sandbox(
        sandbox_class: type[Any],
        *,
        sandbox_id: str,
        api_key: str,
    ) -> None:
        try:
            sandbox_class.kill(
                sandbox_id,
                api_key=api_key,
                request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if not is_missing_e2b_sandbox_error(exc):
                raise AgentVmError(f"Unable to replace stale E2B sandbox {sandbox_id}") from exc

    @staticmethod
    def _delete_snapshot(
        sandbox_class: type[Any],
        *,
        snapshot_id: str | None,
        api_key: str,
    ) -> None:
        if not snapshot_id:
            return
        try:
            sandbox_class.delete_snapshot(
                snapshot_id,
                api_key=api_key,
                request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if not is_missing_e2b_snapshot_error(exc):
                raise AgentVmError(f"Unable to delete stale E2B snapshot {snapshot_id}") from exc

    @staticmethod
    def _delete_created_snapshot_best_effort(
        sandbox_class: type[Any],
        *,
        snapshot_id: str | None,
        api_key: str,
    ) -> None:
        if not snapshot_id:
            return
        try:
            sandbox_class.delete_snapshot(
                snapshot_id,
                api_key=api_key,
                request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the owning failure
            logger.info(
                "Unable to delete an uncommitted E2B recovery snapshot: %s",
                exc,
                extra={
                    "component": "llm_task_sandbox",
                    "operation": "failed_snapshot_commit_cleanup",
                    "snapshot_id": snapshot_id,
                },
            )

    def release(self, cache_key: tuple[str, str], sandbox: object) -> None:
        sandbox_to_kill: object | None = None
        detached: list[tuple[tuple[str, str], object]] = []
        sandbox_key = id(sandbox)
        with self._condition:
            active_count = self._active_session_counts.get(sandbox_key, 0)
            if active_count <= 0:
                return
            if active_count > 1:
                self._active_session_counts[sandbox_key] = active_count - 1
            else:
                self._active_session_counts.pop(sandbox_key, None)
                sandbox_to_kill = self._pending_kills.pop(sandbox_key, None)
                if sandbox_to_kill is None and self._sandboxes.get(cache_key) is sandbox:
                    self._sandboxes.pop(cache_key)
                    self._sandboxes[cache_key] = sandbox
                    detached = self._detach_excess_idle_locked()
                self._condition.notify_all()
        if sandbox_to_kill is not None:
            self._kill_sandbox(sandbox_to_kill, operation="evicted_lease_released")
        self._log_detached(detached)

    def discard(self, cache_key: tuple[str, str], sandbox: object) -> None:
        self.evict(cache_key, sandbox)
        self.release(cache_key, sandbox)

    def evict(self, cache_key: tuple[str, str], sandbox: object) -> None:
        namespace_lock = self._namespace_lock(cache_key)
        with namespace_lock:
            sandbox_to_kill: object | None = None
            with self._lock:
                if self._sandboxes.get(cache_key) is not sandbox:
                    return
                self._sandboxes.pop(cache_key, None)
                sandbox_key = id(sandbox)
                if self._active_session_counts.get(sandbox_key, 0) > 0:
                    self._pending_kills[sandbox_key] = sandbox
                else:
                    sandbox_to_kill = sandbox
            if sandbox_to_kill is not None:
                self._kill_sandbox(sandbox_to_kill, operation="evict_e2b_sandbox")
        logger.warning(
            "Evicted stale E2B sandbox from process cache",
            extra={
                "component": "llm_task_sandbox",
                "operation": "evict_e2b_sandbox",
                "vm_namespace": cache_key[1],
                "sandbox_id": sandbox_identifier(sandbox),
            },
        )

    def close(self) -> int:
        """Detach process handles without destroying durable E2B resources."""
        with self._condition:
            while self._draining:
                self._condition.wait()
            self._draining = True
            while self._active_acquisitions:
                self._condition.wait()
            count = len({id(value) for value in self._sandboxes.values()})
            self._sandboxes.clear()
            self._capabilities_by_revision.clear()
            self._namespace_locks.clear()
        with self._condition:
            self._draining = False
            self._condition.notify_all()
        return count

    def _matching_cached_sandbox(
        self,
        cache_key: tuple[str, str],
        state: LockedAgentVmState,
    ) -> Any | None:
        with self._lock:
            cached = self._sandboxes.get(cache_key)
        if cached is None:
            return None
        if state.durable and state.sandbox_id != sandbox_identifier(cached):
            return None
        return cached

    def _detach_cached(self, cache_key: tuple[str, str], sandbox: object) -> None:
        with self._lock:
            if self._sandboxes.get(cache_key) is sandbox:
                self._sandboxes.pop(cache_key, None)

    def _begin_acquisition(
        self,
        cache_key: tuple[str, str],
        *,
        deadline: float | None,
    ) -> threading.Lock:
        with self._condition:
            while self._draining:
                self._condition.wait(timeout=remaining_deadline_seconds(deadline))
            namespace_lock = self._namespace_lock(cache_key)
            self._active_acquisitions += 1
            return namespace_lock

    def _finish_acquisition(self) -> None:
        with self._condition:
            self._active_acquisitions -= 1
            detached = self._detach_excess_idle_locked()
            if self._active_acquisitions == 0:
                self._condition.notify_all()
        self._log_detached(detached)

    def _namespace_lock(self, cache_key: tuple[str, str]) -> threading.Lock:
        with self._lock:
            namespace_lock = self._namespace_locks.get(cache_key)
            if namespace_lock is None:
                namespace_lock = threading.Lock()
                self._namespace_locks[cache_key] = namespace_lock
            return namespace_lock

    def _acquire_lease_locked(self, sandbox: object) -> None:
        sandbox_key = id(sandbox)
        self._active_session_counts[sandbox_key] = (
            self._active_session_counts.get(sandbox_key, 0) + 1
        )

    def _detach_excess_idle_locked(self) -> list[tuple[tuple[str, str], object]]:
        if self._active_acquisitions:
            return []
        idle = [
            (cache_key, sandbox)
            for cache_key, sandbox in self._sandboxes.items()
            if self._active_session_counts.get(id(sandbox), 0) == 0
        ]
        excess_count = len(idle) - E2B_MAX_IDLE_CACHED_SESSIONS
        detached: list[tuple[tuple[str, str], object]] = []
        for cache_key, sandbox in idle[: max(0, excess_count)]:
            if self._sandboxes.get(cache_key) is sandbox:
                self._sandboxes.pop(cache_key, None)
                detached.append((cache_key, sandbox))
        return detached

    @staticmethod
    def _log_detached(detached: list[tuple[tuple[str, str], object]]) -> None:
        for cache_key, sandbox in detached:
            logger.info(
                "Detached idle E2B handle from process cache",
                extra={
                    "component": "llm_task_sandbox",
                    "operation": "idle_cache_limit",
                    "vm_namespace": cache_key[1],
                    "sandbox_id": sandbox_identifier(sandbox),
                },
            )

    @staticmethod
    def _kill_sandbox(sandbox: object, *, operation: str) -> None:
        kill = getattr(sandbox, "kill", None)
        if not callable(kill):
            return
        try:
            kill(request_timeout=E2B_CLEANUP_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - best-effort ephemeral cleanup
            logger.info(
                "Unable to kill stale E2B sandbox after eviction: %s",
                exc,
                extra={
                    "component": "llm_task_sandbox",
                    "operation": operation,
                    "sandbox_id": sandbox_identifier(sandbox),
                },
            )


def is_missing_e2b_sandbox_error(exc: Exception) -> bool:
    if isinstance(exc, SandboxNotFoundException):
        return True
    message = str(exc).lower()
    return "sandbox was not found" in message or "sandbox not found" in message


def is_missing_e2b_snapshot_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message and ("snapshot" in message or "template" in message)


def sandbox_identifier(sandbox: object) -> str | None:
    for attr in ("sandbox_id", "id", "sandboxId"):
        value = getattr(sandbox, attr, None)
        if value:
            return str(value)
    return None


E2B_SANDBOX_POOL = E2BSandboxPool()
