from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app.models.db.common import _utcnow


class AudioEpisode(Base):
    """Persisted on-demand podcast-style audio artifact."""

    __tablename__ = "audio_episodes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    title = Column(String(255), nullable=False)
    source_content_id = Column(Integer, nullable=True, index=True)
    input_hash = Column(String(64), nullable=False)
    episode_group_id = Column(String(64), nullable=True)
    chapter_index = Column(Integer, nullable=True)
    source_item_ids = Column(JSONB, nullable=False, default=list)
    source_snapshot = Column(JSONB, nullable=False, default=dict)
    script = Column(JSONB, nullable=True)
    script_text = Column(Text, nullable=True)
    prompt_version = Column(Integer, nullable=False, default=1)
    model = Column(String(100), nullable=True)
    audio_storage_path = Column(String(2048), nullable=True)
    audio_content_type = Column(String(100), nullable=False, default="audio/mpeg")
    duration_seconds = Column(Integer, nullable=True)
    share_enabled = Column(Boolean, nullable=False, default=False, index=True)
    share_token_hash = Column(String(64), nullable=True, index=True)
    share_token_nonce = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(episode_group_id IS NULL) = (chapter_index IS NULL)",
            name="ck_audio_episodes_chapter_metadata_pair",
        ),
        CheckConstraint(
            "chapter_index IS NULL OR chapter_index >= 0",
            name="ck_audio_episodes_chapter_index_nonnegative",
        ),
        UniqueConstraint(
            "user_id",
            "kind",
            "input_hash",
            name="uq_audio_episodes_user_kind_hash",
        ),
        UniqueConstraint(
            "user_id",
            "kind",
            "episode_group_id",
            "chapter_index",
            name="uq_audio_episodes_user_kind_group_chapter",
        ),
        Index("idx_audio_episodes_user_kind_status", "user_id", "kind", "status"),
    )
