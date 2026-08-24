from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.core.db import Base


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    apple_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    twitter_username = Column(String(50), nullable=True, index=True)
    council_personas = Column(JSON, nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    has_completed_new_user_tutorial = Column(Boolean, default=False, nullable=False)
    has_completed_onboarding = Column(Boolean, default=False, nullable=False)
    reading_experience = Column(String(16), default="briefing", nullable=False)
    # Durable ownership for the user's persistent agent VM and recovery checkpoint.
    # Credentials never belong in this state or in the projected corpus.
    agent_vm_sandbox_id = Column(String(255), nullable=True)
    agent_vm_template_revision = Column(String(255), nullable=True)
    agent_vm_snapshot_id = Column(String(255), nullable=True)
    agent_vm_snapshot_template_revision = Column(String(255), nullable=True)
    agent_data_revision = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Pydantic schemas
