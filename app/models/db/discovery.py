from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class FeedDiscoveryRun(Base):
    """Track a feed discovery run for a user."""

    __tablename__ = "feed_discovery_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="pending")
    direction_summary = Column(Text, nullable=True)
    seed_content_ids = Column(JSON, default=list, nullable=False)
    token_input = Column(Integer, nullable=True)
    token_output = Column(Integer, nullable=True)
    token_total = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)
    duration_ms_total = Column(Float, nullable=True)
    duration_ms_direction = Column(Float, nullable=True)
    duration_ms_lane = Column(Float, nullable=True)
    duration_ms_candidate_extract = Column(Float, nullable=True)
    duration_ms_candidate_validate = Column(Float, nullable=True)
    duration_ms_persist = Column(Float, nullable=True)
    timing_json = Column("timing", JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (Index("idx_feed_discovery_runs_user_created", "user_id", "created_at"),)


class FeedDiscoverySuggestion(Base):
    """Suggested feed/podcast/YouTube subscription from discovery."""

    __tablename__ = "feed_discovery_suggestions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    suggestion_type = Column(String(50), nullable=False, index=True)
    site_url = Column(String(2048), nullable=True)
    feed_url = Column(String(2048), nullable=False)
    item_url = Column(String(2048), nullable=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    channel_id = Column(String(255), nullable=True)
    playlist_id = Column(String(255), nullable=True)
    rationale = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, index=True, default="new")
    config = Column(JSON, default=dict, nullable=False)
    metadata_json = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "feed_url", name="uq_feed_discovery_user_feed"),
        Index("idx_feed_discovery_suggestions_user_status", "user_id", "status"),
    )
