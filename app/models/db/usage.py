from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class VendorUsageRecord(Base):
    """Persist per-call vendor usage and estimated cost."""

    __tablename__ = "vendor_usage_records"

    id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, index=True)
    model = Column(String(255), nullable=False, index=True)
    feature = Column(String(100), nullable=False, index=True)
    operation = Column(String(100), nullable=False, index=True)
    source = Column(String(50), nullable=True, index=True)
    request_id = Column(String(100), nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    session_id = Column(Integer, nullable=True, index=True)
    message_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    input_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    request_count = Column(Integer, nullable=True)
    resource_count = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    currency = Column(String(8), nullable=False, default="USD")
    pricing_version = Column(String(50), nullable=True)
    metadata_json = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("idx_vendor_usage_provider_model_created", "provider", "model", "created_at"),
        Index("idx_vendor_usage_content_created", "content_id", "created_at"),
        Index("idx_vendor_usage_session_created", "session_id", "created_at"),
        Index("idx_vendor_usage_task_created", "task_id", "created_at"),
    )
