"""Shared assistant-related internal schemas."""

from pydantic import BaseModel, Field, field_validator

MAX_VISIBLE_CONTENT_IDS = 12


class AssistantScreenContext(BaseModel):
    """Compact screen context passed to the assistant router."""

    screen_type: str = Field(default="unknown", max_length=64)
    screen_title: str | None = Field(default=None, max_length=200)
    content_id: int | None = Field(default=None, ge=1)
    visible_content_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_VISIBLE_CONTENT_IDS,
    )
    selected_topic: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("visible_content_ids", mode="before")
    @classmethod
    def truncate_visible_content_ids(cls, value: object) -> object:
        """Bound client-provided visible content IDs to the supported limit."""

        if isinstance(value, list):
            return value[:MAX_VISIBLE_CONTENT_IDS]
        return value
