from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
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


class ProcessingTask(Base):
    """Simple task queue to replace Huey"""

    __tablename__ = "processing_tasks"

    id = Column(Integer, primary_key=True)
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
