from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class UserFeedback(Base):
    """User-submitted product feedback from authenticated clients."""

    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    message = Column(Text, nullable=False)
    source = Column(String(64), nullable=False, default="ios_settings")
    app_version = Column(String(64), nullable=True)
    build_number = Column(String(64), nullable=True)
    platform = Column(String(64), nullable=True)
    os_version = Column(String(128), nullable=True)
    device_model = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
