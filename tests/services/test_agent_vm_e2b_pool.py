from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.db import User
from app.services import agent_vm_e2b_pool
from app.services.agent_vm_corpus import AgentDataHydrationResult, AgentDataRevisionError
from app.services.agent_vm_e2b_pool import E2BSandboxPool
from app.services.agent_vm_runtime import AgentVmError
from app.services.agent_vm_state import LockedAgentVmState
from app.services.agent_vm_template import AGENT_VM_TEMPLATE_NAME, AGENT_VM_TEMPLATE_REVISION


class _Sandbox:
    instances: dict[str, _Sandbox] = {}
    create_templates: list[str] = []
    deleted_snapshots: list[str] = []
    fail_templates: set[str] = set()
    next_id = 1

    def __init__(self) -> None:
        self.sandbox_id = f"sandbox-{type(self).next_id}"
        type(self).next_id += 1
        type(self).instances[self.sandbox_id] = self
        self.missing = False
        self.killed = False
        self.snapshot_count = 0

    @classmethod
    def reset(cls) -> None:
        cls.instances = {}
        cls.create_templates = []
        cls.deleted_snapshots = []
        cls.fail_templates = set()
        cls.next_id = 1

    @classmethod
    def create(cls, *, template: str, **_kwargs: object) -> _Sandbox:
        cls.create_templates.append(template)
        if template in cls.fail_templates:
            raise RuntimeError(f"Snapshot template {template} not found")
        return cls()

    def connect(self, *_args: object, **_kwargs: object) -> _Sandbox:
        sandbox = self if isinstance(self, _Sandbox) else _Sandbox.instances.get(str(self))
        if sandbox is None or sandbox.missing:
            raise RuntimeError("Sandbox not found")
        return sandbox

    def kill(self, **_kwargs: object) -> bool:
        sandbox = self if isinstance(self, _Sandbox) else _Sandbox.instances.get(str(self))
        if sandbox is None or sandbox.missing:
            return False
        sandbox.missing = True
        sandbox.killed = True
        return True

    def create_snapshot(self, **_kwargs: object) -> SimpleNamespace:
        self.snapshot_count += 1
        return SimpleNamespace(snapshot_id=f"snapshot-for-{self.sandbox_id}")

    @staticmethod
    def delete_snapshot(snapshot_id: str, **_kwargs: object) -> bool:
        _Sandbox.deleted_snapshots.append(snapshot_id)
        return True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        llm_task_sandbox_timeout_seconds=300,
        llm_task_sandbox_allow_internet_access=True,
        llm_task_sandbox_e2b_api_key="test-key",
        public_base_url=None,
    )


def _state(*, data_revision: int = 7) -> tuple[User, LockedAgentVmState]:
    user = User(
        id=42,
        is_active=True,
        agent_data_revision=data_revision,
        agent_vm_sandbox_id=None,
        agent_vm_template_revision=None,
        agent_vm_snapshot_id=None,
        agent_vm_snapshot_template_revision=None,
    )
    return user, LockedAgentVmState(row=user, durable=True, db=object())  # type: ignore[arg-type]


def _install_boundaries(
    monkeypatch,
    *,
    state: LockedAgentVmState,
    hydrations: Sequence[AgentDataHydrationResult | Exception] | None = None,
) -> None:
    @contextmanager
    def locked_state(**_kwargs: object):
        yield state

    results = iter(
        hydrations
        or [
            AgentDataHydrationResult(
                remote_revision=0,
                applied_revision=7,
                full=True,
                changed_file_count=3,
                deleted_path_count=0,
                elapsed_ms=12.0,
            )
        ]
    )

    def hydrate(*_args: object, **_kwargs: object) -> AgentDataHydrationResult:
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(agent_vm_e2b_pool, "locked_agent_vm_state", locked_state)
    monkeypatch.setattr(agent_vm_e2b_pool, "hydrate_e2b_agent_data", hydrate)
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "harden_canonical_agent_vm",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "probe_agent_vm_template",
        lambda *_args, **_kwargs: {"bash": True, "playwright": True},
    )


def _acquire(pool: E2BSandboxPool):
    return pool.acquire(
        sandbox_class=_Sandbox,
        vm_namespace="user:42",
        user_id=42,
        feature="chat",
        settings=_settings(),
    )


def test_first_acquisition_snapshots_clean_hydrated_corpus(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state()
    _install_boundaries(monkeypatch, state=state)
    pool = E2BSandboxPool()

    acquisition = _acquire(pool)

    assert acquisition.created is True
    assert _Sandbox.create_templates == [AGENT_VM_TEMPLATE_NAME]
    assert user.agent_vm_sandbox_id == acquisition.sandbox.sandbox_id
    assert user.agent_vm_template_revision == AGENT_VM_TEMPLATE_REVISION
    assert user.agent_vm_snapshot_id == f"snapshot-for-{acquisition.sandbox.sandbox_id}"
    assert user.agent_vm_snapshot_template_revision == AGENT_VM_TEMPLATE_REVISION
    assert acquisition.sandbox.snapshot_count == 1
    pool.release(pool.cache_key("user:42"), acquisition.sandbox)


def test_missing_sandbox_clones_snapshot_then_applies_delta(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state(data_revision=9)
    hydrations = [
        AgentDataHydrationResult(0, 7, True, 3, 0, 10.0),
        AgentDataHydrationResult(7, 9, False, 2, 1, 4.0),
    ]
    _install_boundaries(monkeypatch, state=state, hydrations=hydrations)
    pool = E2BSandboxPool()
    first = _acquire(pool)
    pool.release(pool.cache_key("user:42"), first.sandbox)
    first.sandbox.missing = True

    recovered = _acquire(pool)

    assert recovered.created is True
    assert recovered.hydration is not None
    assert recovered.hydration.full is False
    assert recovered.hydration.changed_file_count == 2
    assert _Sandbox.create_templates == [
        AGENT_VM_TEMPLATE_NAME,
        user.agent_vm_snapshot_id,
    ]
    assert user.agent_vm_sandbox_id == recovered.sandbox.sandbox_id
    assert recovered.sandbox.snapshot_count == 0
    pool.release(pool.cache_key("user:42"), recovered.sandbox)


def test_missing_snapshot_falls_back_to_canonical_and_replaces_checkpoint(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state()
    user.agent_vm_snapshot_id = "snapshot-gone"
    user.agent_vm_snapshot_template_revision = AGENT_VM_TEMPLATE_REVISION
    _Sandbox.fail_templates.add("snapshot-gone")
    _install_boundaries(monkeypatch, state=state)
    pool = E2BSandboxPool()

    acquisition = _acquire(pool)

    assert _Sandbox.create_templates == ["snapshot-gone", AGENT_VM_TEMPLATE_NAME]
    assert user.agent_vm_snapshot_id == f"snapshot-for-{acquisition.sandbox.sandbox_id}"
    pool.release(pool.cache_key("user:42"), acquisition.sandbox)


def test_future_remote_revision_destroys_corrupt_sandbox_and_recovers(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state(data_revision=9)
    existing = _Sandbox()
    user.agent_vm_sandbox_id = existing.sandbox_id
    user.agent_vm_template_revision = AGENT_VM_TEMPLATE_REVISION
    user.agent_vm_snapshot_id = "snapshot-baseline"
    user.agent_vm_snapshot_template_revision = AGENT_VM_TEMPLATE_REVISION
    _install_boundaries(
        monkeypatch,
        state=state,
        hydrations=[
            AgentDataRevisionError("remote revision 10 is ahead of host revision 9"),
            AgentDataHydrationResult(7, 9, False, 2, 0, 3.0),
        ],
    )
    pool = E2BSandboxPool()

    acquisition = _acquire(pool)

    assert existing.killed is True
    assert _Sandbox.create_templates == ["snapshot-baseline"]
    assert user.agent_vm_sandbox_id == acquisition.sandbox.sandbox_id
    pool.release(pool.cache_key("user:42"), acquisition.sandbox)


def test_template_rotation_preserves_ids_when_kill_is_not_confirmed(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state()
    user.agent_vm_sandbox_id = "old-sandbox"
    user.agent_vm_template_revision = "old-template"
    user.agent_vm_snapshot_id = "old-snapshot"
    user.agent_vm_snapshot_template_revision = "old-template"
    _install_boundaries(monkeypatch, state=state)

    def fail_kill(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(_Sandbox, "kill", fail_kill)

    with pytest.raises(AgentVmError, match="Unable to replace stale E2B sandbox"):
        _acquire(E2BSandboxPool())

    assert user.agent_vm_sandbox_id == "old-sandbox"
    assert user.agent_vm_snapshot_id == "old-snapshot"


def test_template_rotation_accepts_confirmed_missing_snapshot(monkeypatch) -> None:
    _Sandbox.reset()
    user, state = _state()
    user.agent_vm_snapshot_id = "old-snapshot"
    user.agent_vm_snapshot_template_revision = "old-template"
    _install_boundaries(monkeypatch, state=state)

    def missing_snapshot(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("Snapshot not found")

    monkeypatch.setattr(_Sandbox, "delete_snapshot", missing_snapshot)

    pool = E2BSandboxPool()
    acquisition = _acquire(pool)

    assert user.agent_vm_snapshot_id == f"snapshot-for-{acquisition.sandbox.sandbox_id}"
    pool.release(pool.cache_key("user:42"), acquisition.sandbox)


def test_failed_state_commit_never_publishes_process_cache(monkeypatch) -> None:
    _Sandbox.reset()
    _user, state = _state()

    @contextmanager
    def failed_commit(**_kwargs: Any):
        yield state
        raise RuntimeError("commit failed")

    monkeypatch.setattr(agent_vm_e2b_pool, "locked_agent_vm_state", failed_commit)
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "hydrate_e2b_agent_data",
        lambda *_args, **_kwargs: AgentDataHydrationResult(0, 7, True, 1, 0, 1.0),
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "probe_agent_vm_template",
        lambda *_args, **_kwargs: {"bash": True},
    )
    monkeypatch.setattr(
        agent_vm_e2b_pool,
        "harden_canonical_agent_vm",
        lambda *_args, **_kwargs: None,
    )
    pool = E2BSandboxPool()

    with pytest.raises(RuntimeError, match="commit failed"):
        _acquire(pool)

    assert pool._sandboxes == {}
    assert pool._active_session_counts == {}
    assert all(sandbox.killed for sandbox in _Sandbox.instances.values())
    assert _Sandbox.deleted_snapshots == ["snapshot-for-sandbox-1"]
