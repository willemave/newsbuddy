from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.api.base import UTCDateTime
from app.models.domain.user_profile import (
    MAX_COUNCIL_EXPERTS,
    MIN_COUNCIL_EXPERTS,
    CouncilPersonaConfig,
)


class UserBase(BaseModel):
    """Base user schema."""

    email: EmailStr
    full_name: str | None = None


class UserResponse(UserBase):
    """Schema for user API responses."""

    id: int
    apple_id: str
    is_admin: bool
    is_active: bool
    twitter_username: str | None = None
    council_personas: list[CouncilPersonaConfig] = Field(default_factory=list)
    has_x_bookmark_sync: bool = False
    has_completed_onboarding: bool
    has_completed_new_user_tutorial: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @field_validator("council_personas", mode="before")
    @classmethod
    def normalize_council_personas(
        cls, value: list[CouncilPersonaConfig] | list[dict[str, Any]] | None
    ) -> list[CouncilPersonaConfig] | list[dict[str, Any]]:
        """Allow unset council personas on legacy users."""

        if value is None:
            return []
        return value

    model_config = ConfigDict(from_attributes=True)


class UpdateUserProfileRequest(BaseModel):
    """Request schema for updating the authenticated user's profile."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    twitter_username: str | None = Field(default=None, max_length=50)
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
