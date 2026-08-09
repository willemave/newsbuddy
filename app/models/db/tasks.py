from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)

PROCESSING_TASK_LEASE_FIELDS = (
    "locked_at",
    "locked_by",
    "lease_token",
    "lease_expires_at",
)


class ProcessingTask(Base):
    """Simple task queue to replace Huey"""

    __tablename__ = "processing_tasks"

    id = Column(Integer, primary_key=True)
    # Set only for work whose lifecycle is exclusively owned by one user.
    # Shared content work remains ownerless and grants polling access through
    # ``processing_task_user_access`` instead.
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    task_type = Column(String(50), nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    payload = Column(JSON, default=dict)
    status = Column(String(20), default="pending", index=True)
    queue_name = Column(String(32), nullable=False, index=True, default="content")

    created_at = Column(DateTime, default=_utcnow)
    available_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(String(100), nullable=True, index=True)
    lease_token = Column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)

    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False, server_default="0")
    dedupe_key = Column(String(512), nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "lease_token IS NULL OR "
            "(status IS NOT NULL AND status = 'processing' "
            "AND locked_at IS NOT NULL AND locked_by IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_processing_tasks_lease_token_has_owner",
        ),
        Index("idx_task_status_created", "status", "created_at"),
        Index("idx_task_queue_status_created", "queue_name", "status", "created_at"),
        Index(
            "idx_task_status_available",
            "status",
            "retry_count",
            "available_at",
            "created_at",
            "id",
        ),
        Index(
            "idx_task_queue_status_available",
            "queue_name",
            "status",
            "retry_count",
            "available_at",
            "created_at",
            "id",
        ),
        Index(
            "uq_processing_tasks_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"),
        ),
    )


class ProcessingTaskUserAccess(Base):
    """Users allowed to observe one async task through the jobs API."""

    __tablename__ = "processing_task_user_access"

    task_id = Column(
        Integer,
        ForeignKey("processing_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_processing_task_user_access_user_task", "user_id", "task_id"),)


def processing_task_lease_clear_values() -> dict[str, object]:
    """Return the canonical values for releasing processing-task ownership."""
    return {field: None for field in PROCESSING_TASK_LEASE_FIELDS}


def clear_processing_task_lease(task: ProcessingTask) -> None:
    """Clear every ownership field on an ORM processing-task row."""
    for field in PROCESSING_TASK_LEASE_FIELDS:
        setattr(task, field, None)
