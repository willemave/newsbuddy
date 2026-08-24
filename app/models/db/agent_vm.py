"""Persistence for system VM ownership and per-user agent-data manifests."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app.models.db.common import _utcnow


class AgentVmSystemState(Base):
    """Singleton durable ownership for the feed-research system sandbox."""

    __tablename__ = "agent_vm_system_state"

    id = Column(Integer, primary_key=True, default=1)
    sandbox_id = Column(String(255), nullable=True)
    template_revision = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class AgentDataFile(Base):
    """Current projected file state for one typed user document."""

    __tablename__ = "agent_data_files"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    document_kind = Column(String(32), nullable=False)
    document_key = Column(String(255), nullable=False)
    path = Column(String(1024), nullable=False)
    stale_paths = Column(JSONB, nullable=False, default=list)
    checksum_sha256 = Column(String(64), nullable=False)
    index_record = Column(JSONB, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    revision = Column(BigInteger, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "path", name="uq_agent_data_files_user_path"),
        UniqueConstraint(
            "user_id",
            "document_kind",
            "document_key",
            name="uq_agent_data_files_user_document",
        ),
        Index("idx_agent_data_files_user_revision", "user_id", "revision"),
    )
