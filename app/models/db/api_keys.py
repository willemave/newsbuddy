from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class UserApiKey(Base):
    """API key for machine-to-machine access on behalf of a user."""

    __tablename__ = "user_api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    key_prefix = Column(String(64), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False)
    created_by_admin_user_id = Column(Integer, nullable=True, index=True)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_user_api_keys_user_revoked", "user_id", "revoked_at"),
        Index("idx_user_api_keys_prefix_revoked", "key_prefix", "revoked_at"),
    )
