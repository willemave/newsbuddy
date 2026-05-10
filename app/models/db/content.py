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
from app.models.contracts import (
    ContentStatus,
)
from app.models.db.common import _utcnow
from app.utils.summary_utils import extract_short_summary


class Content(Base):
    __tablename__ = "contents"

    # Primary fields
    id = Column(Integer, primary_key=True)
    content_type = Column(String(20), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    source_url = Column(String(2048), nullable=True)
    title = Column(String(500), nullable=True)
    source = Column(String(100), nullable=True, index=True)
    platform = Column(String(50), nullable=True, index=True)
    is_aggregate = Column(Boolean, default=False, nullable=False, index=True)

    # Status tracking
    status = Column(String(20), default=ContentStatus.NEW.value, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Classification
    classification = Column(String(20), nullable=True, index=True)

    # Checkout mechanism
    checked_out_by = Column(String(100), nullable=True, index=True)
    checked_out_at = Column(DateTime, nullable=True)

    # Type-specific data stored as JSON
    # For articles: author, content, publish_date, source, internal_links
    # For podcasts: audio_url, transcript, duration, episode_number
    content_metadata = Column(JSON, default=dict, nullable=False)
    search_text = Column(Text, nullable=True)

    # Common timestamps
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    processed_at = Column(DateTime, nullable=True)
    publication_date = Column(DateTime, nullable=True, index=True)

    # Indexes for performance
    __table_args__ = (
        Index("idx_content_type_status", "content_type", "status"),
        Index("idx_checkout", "checked_out_by", "checked_out_at"),
        Index("idx_created_at", "created_at"),
        Index("idx_content_aggregate", "content_type", "is_aggregate"),
        Index("idx_url_content_type", "url", "content_type", unique=True),
        # Performance index for visibility queries (classification + status + content_type)
        Index("idx_contents_classification_status", "classification", "status", "content_type"),
    )

    @property
    def short_summary(self) -> str | None:
        """Return a short summary for list views if available."""
        if not self.content_metadata:
            return None
        return extract_short_summary(self.content_metadata.get("summary"))


class ContentDiscussion(Base):
    """Discussion payload for a content item."""

    __tablename__ = "content_discussions"

    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, nullable=False)
    platform = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    discussion_data = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    fetched_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("content_id", name="uq_content_discussions_content"),
        Index("idx_content_discussions_platform", "platform"),
        Index("idx_content_discussions_status", "status"),
        Index("idx_content_discussions_fetched_at", "fetched_at"),
    )


class ContentBody(Base):
    """Canonical body pointer stored separately from `content_metadata`."""

    __tablename__ = "content_bodies"

    content_id = Column(Integer, primary_key=True)
    variant = Column(String(20), primary_key=True)
    storage_provider = Column(String(32), nullable=False)
    storage_bucket = Column(String(255), nullable=True)
    storage_key = Column(String(2048), nullable=False)
    content_format = Column(String(32), nullable=False)
    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    char_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_content_bodies_content_id", "content_id"),
        Index("idx_content_bodies_storage_key", "storage_key"),
    )


class ContentReadStatus(Base):
    """Track which content has been read by which user."""

    __tablename__ = "content_read_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    read_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("idx_content_read_user_content", "user_id", "content_id", unique=True),)


class ContentKnowledgeSave(Base):
    """Track which content has been saved to knowledge by which user."""

    __tablename__ = "content_knowledge_saves"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    saved_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_content_knowledge_saves_user_content", "user_id", "content_id", unique=True),
    )


class ContentUnlikes(Base):
    """Track which content has been unliked by which user."""

    __tablename__ = "content_unlikes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    unliked_at = Column(DateTime, default=_utcnow, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_content_unlikes_user_content", "user_id", "content_id", unique=True),
    )


class ContentStatusEntry(Base):
    """Per-user status for content feed membership."""

    __tablename__ = "content_status"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True, default="inbox")
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="idx_content_status_user_content"),
        # Performance index for inbox lookups (user_id + status + content_id)
        Index("idx_content_status_user_status_content", "user_id", "status", "content_id"),
    )
