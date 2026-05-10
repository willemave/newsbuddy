from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
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


class UserScraperConfig(Base):
    """Per-user scraper configuration for dynamic sources."""

    __tablename__ = "user_scraper_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    scraper_type = Column(String(50), nullable=False, index=True)
    display_name = Column(String(255), nullable=True)
    feed_url = Column(String(2048), nullable=True)
    config = Column(JSON, default=dict, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "scraper_type", "feed_url", name="uq_user_scraper_feed"),
        Index("idx_user_scraper_user_type", "user_id", "scraper_type"),
    )
