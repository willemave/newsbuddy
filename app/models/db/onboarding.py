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
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class OnboardingDiscoveryRun(Base):
    """Track an onboarding discovery run for a user."""

    __tablename__ = "onboarding_discovery_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="pending")
    topic_summary = Column(Text, nullable=True)
    inferred_topics = Column(JSON, default=list, nullable=False)
    lane_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (Index("idx_onboarding_discovery_runs_user_created", "user_id", "created_at"),)


class OnboardingDiscoveryLane(Base):
    """Track a single onboarding discovery lane."""

    __tablename__ = "onboarding_discovery_lanes"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    lane_name = Column(String(160), nullable=False)
    goal = Column(Text, nullable=True)
    target = Column(String(30), nullable=True)
    status = Column(String(20), nullable=False, index=True, default="queued")
    query_count = Column(Integer, nullable=False, default=0)
    completed_queries = Column(Integer, nullable=False, default=0)
    queries = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("idx_onboarding_discovery_lanes_run", "run_id"),)


class OnboardingDiscoverySuggestion(Base):
    """Suggested subscription discovered during onboarding."""

    __tablename__ = "onboarding_discovery_suggestions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    suggestion_type = Column(String(50), nullable=False, index=True)
    site_url = Column(String(2048), nullable=True)
    feed_url = Column(String(2048), nullable=True)
    subreddit = Column(String(255), nullable=True)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, index=True, default="new")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_onboarding_discovery_suggestions_run", "run_id"),
        Index("idx_onboarding_discovery_suggestions_user_status", "user_id", "status"),
    )
