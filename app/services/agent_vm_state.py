"""Transactional ownership of persistent E2B sandbox identities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.core.settings import get_settings
from app.models.db.agent_vm import AgentVmSystemState
from app.models.db.users import User
from app.services.agent_vm_runtime import SYSTEM_USER_ID, AgentVmError

_AGENT_VM_ADVISORY_LOCK_BASE = 6_142_000_000


@dataclass
class LockedAgentVmState:
    """Mutable view over one row held under a transaction lock."""

    row: User | AgentVmSystemState | None
    durable: bool
    db: Session | None

    @property
    def sandbox_id(self) -> str | None:
        if isinstance(self.row, User):
            return _clean(self.row.agent_vm_sandbox_id)
        if isinstance(self.row, AgentVmSystemState):
            return _clean(self.row.sandbox_id)
        return None

    @property
    def template_revision(self) -> str | None:
        if isinstance(self.row, User):
            return _clean(self.row.agent_vm_template_revision)
        if isinstance(self.row, AgentVmSystemState):
            return _clean(self.row.template_revision)
        return None

    @property
    def snapshot_id(self) -> str | None:
        if isinstance(self.row, User):
            return _clean(self.row.agent_vm_snapshot_id)
        return None

    @property
    def snapshot_template_revision(self) -> str | None:
        if isinstance(self.row, User):
            return _clean(self.row.agent_vm_snapshot_template_revision)
        return None

    def set_sandbox(self, sandbox_id: str | None, template_revision: str | None) -> None:
        if isinstance(self.row, User):
            self.row.agent_vm_sandbox_id = sandbox_id
            self.row.agent_vm_template_revision = template_revision
        elif isinstance(self.row, AgentVmSystemState):
            self.row.sandbox_id = sandbox_id
            self.row.template_revision = template_revision
            self.row.updated_at = datetime.now(UTC)

    def set_snapshot(
        self,
        snapshot_id: str | None,
        template_revision: str | None,
    ) -> None:
        if isinstance(self.row, User):
            self.row.agent_vm_snapshot_id = snapshot_id
            self.row.agent_vm_snapshot_template_revision = template_revision


@contextmanager
def locked_agent_vm_state(
    *,
    user_id: int,
    vm_namespace: str,
) -> Iterator[LockedAgentVmState]:
    """Lock, expose, and commit one durable sandbox owner.

    Synthetic test namespaces deliberately remain process-local. Production user
    and system namespaces are serialized in PostgreSQL before connect-or-create,
    preventing two workers from creating competing sandboxes.
    """

    if not _uses_durable_state(user_id=user_id, vm_namespace=vm_namespace):
        yield LockedAgentVmState(row=None, durable=False, db=None)
        return

    session_factory = get_session_factory()
    with session_factory() as db:
        _take_namespace_lock(db, user_id=user_id)
        row: User | AgentVmSystemState | None
        if user_id == SYSTEM_USER_ID:
            row = (
                db.query(AgentVmSystemState)
                .filter(AgentVmSystemState.id == 1)
                .with_for_update()
                .first()
            )
            if row is None:
                row = AgentVmSystemState(id=1)
                db.add(row)
                db.flush()
        else:
            row = db.query(User).filter(User.id == user_id).with_for_update().first()
            if row is None:
                if get_settings().environment == "production":
                    raise AgentVmError(f"Cannot acquire VM state for missing user {user_id}")
                yield LockedAgentVmState(row=None, durable=False, db=None)
                return

        state = LockedAgentVmState(row=row, durable=True, db=db)
        yield state
        db.commit()


def _take_namespace_lock(db: Session, *, user_id: int) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _AGENT_VM_ADVISORY_LOCK_BASE + int(user_id)},
    )


def _uses_durable_state(*, user_id: int, vm_namespace: str) -> bool:
    if vm_namespace.startswith("test:"):
        return False
    return user_id == SYSTEM_USER_ID or vm_namespace == f"user:{user_id}"


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
