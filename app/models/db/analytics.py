from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class AnalyticsInteraction(Base):
    """Track append-only user interactions for content analytics."""

    __tablename__ = "analytics_interactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    interaction_type = Column(String(32), nullable=False, index=True)
    interaction_id = Column(String(36), nullable=False)
    surface = Column(String(64), nullable=True)
    context_data = Column(JSON, default=dict, nullable=False)
    occurred_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "interaction_id",
            name="uq_analytics_interactions_user_interaction",
        ),
        Index(
            "idx_analytics_interactions_user_type_occurred",
            "user_id",
            "interaction_type",
            "occurred_at",
        ),
        Index(
            "idx_analytics_interactions_user_content_occurred",
            "user_id",
            "content_id",
            "occurred_at",
        ),
    )
