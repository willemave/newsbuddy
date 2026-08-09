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
)

from app.core.db import Base
from app.core.logging import get_logger
from app.models.contracts import (
    MessageProcessingStatus,
)
from app.models.db.common import _utcnow

logger = get_logger(__name__)


class ChatSession(Base):
    """Chat session for deep-dive conversations with articles/news."""

    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    content_id = Column(Integer, nullable=True, index=True)  # soft ref to contents.id
    news_item_id = Column(Integer, nullable=True, index=True)  # soft ref to news_items.id
    parent_session_id = Column(Integer, nullable=True, index=True)
    title = Column(String(500), nullable=True)
    session_type = Column(String(50), nullable=True)  # article_brain, topic, ad_hoc
    topic = Column(String(500), nullable=True)
    context_snapshot = Column(Text, nullable=True)
    council_persona_id = Column(String(64), nullable=True, index=True)
    council_persona_name = Column(String(120), nullable=True)
    council_persona_prompt = Column(Text, nullable=True)
    council_mode = Column(Boolean, default=False, nullable=False)
    active_child_session_id = Column(Integer, nullable=True, index=True)
    branch_start_message_id = Column(Integer, nullable=True, index=True)
    council_message_id = Column(Integer, nullable=True, index=True)
    is_hidden_from_history = Column(Boolean, default=False, nullable=False)
    llm_model = Column(String(100), nullable=False, default="openai:gpt-5.6-terra")
    llm_provider = Column(String(50), nullable=False, default="openai")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    last_message_at = Column(DateTime, nullable=True, index=True)
    is_archived = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_chat_sessions_user_time", "user_id", "last_message_at"),
        Index("idx_chat_sessions_content", "user_id", "content_id"),
        Index("idx_chat_sessions_news_item", "user_id", "news_item_id"),
        Index("idx_chat_sessions_parent_hidden", "parent_session_id", "is_hidden_from_history"),
    )


class ChatMessage(Base):
    """Chat message history stored as pydantic-ai ModelMessage JSON."""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, nullable=False, index=True)  # soft ref to chat_sessions.id
    message_list = Column(Text, nullable=False)  # JSON from ModelMessagesTypeAdapter
    render_metadata = Column(JSON, nullable=True)
    # Immutable inputs captured when an asynchronous turn is accepted. Workers
    # read this row instead of mutable session context after queue delay.
    processing_context = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    # Async processing fields
    status = Column(
        String(20),
        nullable=False,
        default=MessageProcessingStatus.COMPLETED.value,
        index=True,
    )
    error = Column(Text, nullable=True)  # Error message if status=failed

    __table_args__ = (Index("idx_chat_messages_session_created", "session_id", "created_at"),)
