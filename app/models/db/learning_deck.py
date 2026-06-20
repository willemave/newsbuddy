from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app.models.contracts import LearningDeckRunStatus
from app.models.db.common import _utcnow

ACTIVE_LEARNING_DECK_RUN_STATUS_VALUES = (
    LearningDeckRunStatus.QUEUED.value,
    LearningDeckRunStatus.PREPARING.value,
    LearningDeckRunStatus.GENERATING.value,
    LearningDeckRunStatus.VALIDATING.value,
    LearningDeckRunStatus.PUBLISHING.value,
)
ACTIVE_LEARNING_DECK_RUN_STATUS_SQL = (
    "status IN ('queued', 'preparing', 'generating', 'validating', 'publishing')"
)


class LearningDeck(Base):
    """Stable user-facing Learning Deck record for one source."""

    __tablename__ = "learning_decks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    source_kind = Column(String(32), nullable=False, index=True)
    source_identity = Column(String(512), nullable=False)
    source_url = Column(String(2048), nullable=True)
    source_content_id = Column(Integer, nullable=True, index=True)
    source_title = Column(String(500), nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)
    title = Column(String(500), nullable=False)
    latest_successful_run_id = Column(Integer, nullable=True, index=True)
    latest_run_id = Column(Integer, nullable=True, index=True)
    artifact_storage_prefix = Column(String(2048), nullable=True)
    deck_object_key = Column(String(2048), nullable=True)
    source_notes_object_key = Column(String(2048), nullable=True)
    source_notes_html_object_key = Column(String(2048), nullable=True)
    artifact_object_keys = Column(JSONB, nullable=False, default=list)
    share_enabled = Column(Boolean, nullable=False, default=False, index=True)
    share_token_hash = Column(String(64), nullable=True, index=True)
    share_token_nonce = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index(
            "uq_learning_decks_user_source_active",
            "user_id",
            "source_identity",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_learning_decks_user_updated", "user_id", "updated_at"),
    )


class LearningDeckRun(Base):
    """One generation attempt for a Learning Deck."""

    __tablename__ = "learning_deck_runs"

    id = Column(Integer, primary_key=True)
    deck_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    llm_task_id = Column(
        Integer,
        ForeignKey("llm_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default="queued", index=True)
    interests_prompt = Column(Text, nullable=True)
    source_snapshot = Column(JSONB, nullable=False, default=dict)
    timeline = Column(JSONB, nullable=False, default=list)
    artifact_storage_prefix = Column(String(2048), nullable=True)
    deck_object_key = Column(String(2048), nullable=True)
    source_notes_object_key = Column(String(2048), nullable=True)
    source_notes_html_object_key = Column(String(2048), nullable=True)
    artifact_object_keys = Column(JSONB, nullable=False, default=list)
    model_provider = Column(String(50), nullable=True)
    model_name = Column(String(100), nullable=True)
    sandbox_provider = Column(String(50), nullable=True)
    sandbox_id = Column(String(255), nullable=True)
    agent_log_object_key = Column(String(2048), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_learning_deck_runs_deck_created", "deck_id", "created_at"),
        Index("idx_learning_deck_runs_user_status", "user_id", "status"),
        Index(
            "uq_learning_deck_runs_user_active",
            "user_id",
            unique=True,
            postgresql_where=text(ACTIVE_LEARNING_DECK_RUN_STATUS_SQL),
        ),
    )
