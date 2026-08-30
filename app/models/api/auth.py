from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(..., min_length=1)
    attempt_id: str | None = Field(default=None, min_length=36, max_length=36)

    @field_validator("attempt_id")
    @classmethod
    def normalize_attempt_id(cls, value: str | None) -> str | None:
        """Store one canonical UUID spelling for replay comparisons."""
        if value is None:
            return None
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("attempt_id must be a UUID") from exc


class DeleteAccountRequest(BaseModel):
    """Fresh Apple credentials proving the account deletion request."""

    id_token: str = Field(..., min_length=1)
    authorization_code: str = Field(..., min_length=1)


class DeleteAccountResponse(BaseModel):
    """Accepted account deletion response."""

    status: str = "deletion_scheduled"


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
