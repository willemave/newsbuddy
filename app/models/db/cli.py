from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class CliLinkSession(Base):
    """Short-lived QR approval session for linking one CLI install."""

    __tablename__ = "cli_link_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    approve_token_hash = Column(String(128), nullable=False)
    poll_token_hash = Column(String(128), nullable=False)
    requested_device_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    approved_by_user_id = Column(Integer, nullable=True, index=True)
    user_api_key_id = Column(Integer, nullable=True, index=True)
    issued_api_key_plaintext = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_cli_link_sessions_status_expires", "status", "expires_at"),)
