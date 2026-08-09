"""Server-side refresh-token replay protection."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.core.db import Base


class ConsumedRefreshToken(Base):
    """A refresh token that has already been exchanged once."""

    __tablename__ = "consumed_refresh_tokens"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("idx_consumed_refresh_tokens_expiry", "expires_at"),)
