"""User models and schemas for authentication."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.constants import DEFAULT_NEWS_DIGEST_INTERVAL_HOURS
from app.core.db import Base


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    apple_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    twitter_username = Column(String(50), nullable=True, index=True)
    news_digest_timezone = Column(String(100), nullable=False, default="UTC")
    news_digest_interval_hours = Column(
        Integer,
        nullable=False,
        default=DEFAULT_NEWS_DIGEST_INTERVAL_HOURS,
    )
    news_digest_preference_prompt = Column(Text, nullable=True)
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
    news_digest_timezone: str = "UTC"
    news_digest_interval_hours: int = DEFAULT_NEWS_DIGEST_INTERVAL_HOURS
    news_digest_preference_prompt: str | None = None
    has_x_bookmark_sync: bool = False
    has_completed_onboarding: bool
    has_completed_new_user_tutorial: bool
    has_completed_live_voice_onboarding: bool
    created_at: datetime
    updated_at: datetime

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
    news_digest_timezone: str | None = Field(default=None, max_length=100)
    news_digest_interval_hours: int | None = Field(default=None)
    news_digest_preference_prompt: str | None = Field(default=None, max_length=4000)
