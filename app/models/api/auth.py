from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.api.users import UserResponse
from app.models.contracts import ReadingExperience


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
    reading_experience: ReadingExperience | None = None


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
