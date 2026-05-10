from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
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


class UserIntegrationConnection(Base):
    """OAuth/API connection metadata for external providers per user."""

    __tablename__ = "user_integration_connections"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    provider = Column(String(50), nullable=False, index=True)
    provider_user_id = Column(String(255), nullable=True)
    provider_username = Column(String(255), nullable=True)
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    scopes = Column(JSON, default=list, nullable=True)
    connection_metadata = Column(JSON, default=dict, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_connection"),
        UniqueConstraint("provider", "provider_user_id", name="uq_provider_provider_user"),
        Index("idx_user_integration_provider_active", "provider", "is_active"),
    )


class UserIntegrationSyncState(Base):
    """Provider sync cursor/state for a single user integration connection."""

    __tablename__ = "user_integration_sync_state"

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, nullable=False, index=True)
    cursor = Column(String(1024), nullable=True)
    last_synced_item_id = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_status = Column(String(50), nullable=True)
    last_error = Column(Text, nullable=True)
    sync_metadata = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_user_integration_sync_connection"),
        Index("idx_user_integration_sync_last_synced", "last_synced_at"),
    )


class UserIntegrationSyncedItem(Base):
    """Per-connection ledger of externally synced items."""

    __tablename__ = "user_integration_synced_items"

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, nullable=False, index=True)
    channel = Column(String(50), nullable=False, index=True)
    external_item_id = Column(String(255), nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)
    item_url = Column(String(2048), nullable=True)
    first_synced_at = Column(DateTime, default=_utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "channel",
            "external_item_id",
            name="uq_user_integration_synced_item",
        ),
        Index(
            "idx_user_integration_synced_item_lookup",
            "connection_id",
            "channel",
            "last_seen_at",
        ),
    )
