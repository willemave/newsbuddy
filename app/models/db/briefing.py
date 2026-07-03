from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import Base
from app.models.db.common import _utcnow


class BriefingLens(Base):
    """One user-visible briefing lens such as Podcasts, Articles, or a news category."""

    __tablename__ = "briefing_lenses"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    key = Column(String(64), nullable=False)
    tier = Column(String(16), nullable=False, index=True)
    title = Column(String(220), nullable=False)
    deck = Column(Text, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="active", index=True)
    centroid = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    retired_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_briefing_lenses_user_key"),
        Index("idx_briefing_lenses_user_status_position", "user_id", "status", "position"),
    )


class BriefingSegment(Base):
    """Immutable LLM-composed briefing block document over a frozen source set."""

    __tablename__ = "briefing_segments"

    id = Column(Integer, primary_key=True)
    lens_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    blocks = Column(JSONB, nullable=False, default=list)
    markdown_raw = Column(Text, nullable=False, default="")
    narration_text = Column(Text, nullable=False, default="")
    source_keys = Column(JSONB, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="active", index=True)
    model = Column(String(64), nullable=False)
    prompt_version = Column(String(16), nullable=False)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    generation_ms = Column(Integer, nullable=True)
    warnings = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_briefing_segments_user_status_created", "user_id", "status", "created_at"),
        Index("idx_briefing_segments_lens_status_created", "lens_id", "status", "created_at"),
    )


class BriefingPendingSource(Base):
    """Unread source waiting to be assigned and composed into a briefing segment."""

    __tablename__ = "briefing_pending_sources"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    lens_key = Column(String(64), nullable=True, index=True)
    source_kind = Column(String(16), nullable=False)
    source_id = Column(Integer, nullable=False)
    enqueued_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source_kind",
            "source_id",
            name="uq_briefing_pending_sources_user_source",
        ),
        Index("idx_briefing_pending_sources_user_lens", "user_id", "lens_key", "enqueued_at"),
    )


class BriefingState(Base):
    """Per-user briefing version and masthead metadata."""

    __tablename__ = "briefing_states"

    user_id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=0)
    masthead_title = Column(String(220), nullable=False)
    masthead_deck = Column(Text, nullable=False, default="")
    last_append_at = Column(DateTime, nullable=True)
    last_sweep_at = Column(DateTime, nullable=True)
