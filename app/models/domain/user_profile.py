from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_COUNCIL_EXPERTS = 3
MIN_COUNCIL_EXPERTS = 2


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


def resolve_user_council_personas(user: object) -> list[CouncilPersonaConfig]:
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
