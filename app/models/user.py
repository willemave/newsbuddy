"""User models and schemas for authentication."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.db import Base

MAX_COUNCIL_EXPERTS = 3
MIN_COUNCIL_EXPERTS = 2


def build_default_council_personas() -> list[dict[str, Any]]:
    """Return the default council persona presets for new users.

    Returns an empty list — experts are personal and must be chosen by the user.
    """

    return []


class CouncilPersonaConfig(BaseModel):
    """User-configurable expert for council chat.

    Each expert represents a real person whose perspective the user values.
    The ``instruction_prompt`` is kept for backward compatibility but is no
    longer required — the council chat service generates a rich impersonation
    prompt from the ``display_name`` at runtime.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=80)
    instruction_prompt: str = Field(default="", max_length=1500)
    sort_order: int = Field(..., ge=0, le=MAX_COUNCIL_EXPERTS - 1)

    @field_validator("id", "display_name", "instruction_prompt", mode="before")
    @classmethod
    def normalize_string_fields(cls, value: object) -> object:
        """Trim council persona string fields before validation."""

        if isinstance(value, str):
            return value.strip()
        return value


def resolve_user_council_personas(user: User | object) -> list[CouncilPersonaConfig]:
    """Return validated council personas for a user (empty when unconfigured)."""

    raw_value = getattr(user, "council_personas", None)
    if isinstance(raw_value, list) and raw_value:
        try:
            personas = [CouncilPersonaConfig.model_validate(item) for item in raw_value]
            if MIN_COUNCIL_EXPERTS <= len(personas) <= MAX_COUNCIL_EXPERTS:
                return sorted(personas, key=lambda persona: persona.sort_order)
        except Exception:  # noqa: BLE001
            pass
    return []


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    apple_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    twitter_username = Column(String(50), nullable=True, index=True)
    news_list_preference_prompt = Column(Text, nullable=True)
    council_personas = Column(JSON, nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    has_completed_new_user_tutorial = Column(Boolean, default=False, nullable=False)
    has_completed_onboarding = Column(Boolean, default=False, nullable=False)
    has_completed_live_voice_onboarding = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Pydantic schemas
class UserBase(BaseModel):
    """Base user schema."""

    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    """Schema for creating a user."""

    apple_id: str


class UserResponse(UserBase):
    """Schema for user API responses."""

    id: int
    apple_id: str
    is_admin: bool
    is_active: bool
    twitter_username: str | None = None
    news_list_preference_prompt: str | None = None
    council_personas: list[CouncilPersonaConfig] = Field(default_factory=list)
    has_x_bookmark_sync: bool = False
    has_completed_onboarding: bool
    has_completed_new_user_tutorial: bool
    has_completed_live_voice_onboarding: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("council_personas", mode="before")
    @classmethod
    def normalize_council_personas(
        cls, value: list[CouncilPersonaConfig] | list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        """Allow unset council personas on legacy users."""

        if value is None:
            return []
        return value

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime, _info) -> str:
        """
        Serialize datetime to ISO8601 with 'Z' timezone indicator.

        Ensures iOS Swift compatibility - ISO8601DateFormatter requires timezone.

        Args:
            dt: Datetime to serialize (assumed UTC if naive)

        Returns:
            ISO8601 string with 'Z' suffix (e.g., '2025-11-01T15:29:31Z')
        """
        # Ensure datetime has UTC timezone info
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

        # Format as ISO8601 with 'Z' suffix
        return dt.isoformat().replace("+00:00", "Z")

    model_config = ConfigDict(from_attributes=True)


class AppleSignInRequest(BaseModel):
    """Request schema for Apple Sign In."""

    id_token: str = Field(..., description="Apple identity token")
    email: str | None = None  # Optional - will extract from token if not provided
    full_name: str | None = None


class DebugUserSessionRequest(BaseModel):
    """Request schema for creating or resuming a debug user session."""

    model_config = ConfigDict(extra="forbid")

    user_id: int | None = Field(default=None, ge=1)
    has_completed_onboarding: bool | None = None
    has_completed_new_user_tutorial: bool | None = None
    has_completed_live_voice_onboarding: bool | None = None


class TokenResponse(BaseModel):
    """Response schema for authentication tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
    is_new_user: bool = False


class RefreshTokenRequest(BaseModel):
    """Request schema for token refresh."""

    refresh_token: str


class AccessTokenResponse(BaseModel):
    """Response schema for token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AdminLoginRequest(BaseModel):
    """Request schema for admin login."""

    password: str


class AdminLoginResponse(BaseModel):
    """Response schema for admin login."""

    message: str


class UpdateUserProfileRequest(BaseModel):
    """Request schema for updating the authenticated user's profile."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    twitter_username: str | None = Field(default=None, max_length=50)
    news_list_preference_prompt: str | None = Field(default=None, max_length=4000)
    council_personas: list[CouncilPersonaConfig] | None = Field(
        default=None,
        min_length=MIN_COUNCIL_EXPERTS,
        max_length=MAX_COUNCIL_EXPERTS,
    )

    @field_validator("council_personas")
    @classmethod
    def validate_council_personas(
        cls, value: list[CouncilPersonaConfig] | None
    ) -> list[CouncilPersonaConfig] | None:
        """Enforce council expert slots (2-3 real-person experts)."""

        if value is None:
            return None
        count = len(value)
        if not (MIN_COUNCIL_EXPERTS <= count <= MAX_COUNCIL_EXPERTS):
            raise ValueError(
                f"council_personas must contain {MIN_COUNCIL_EXPERTS}-{MAX_COUNCIL_EXPERTS} entries"
            )

        persona_ids = [persona.id for persona in value]
        if len(set(persona_ids)) != count:
            raise ValueError("council_personas must use unique ids")

        sort_orders = sorted(persona.sort_order for persona in value)
        if sort_orders != list(range(count)):
            raise ValueError(f"council_personas sort_order values must be 0 through {count - 1}")

        return sorted(value, key=lambda persona: persona.sort_order)
