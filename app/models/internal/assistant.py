"""Shared assistant-related internal schemas."""

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_VISIBLE_CONTENT_IDS = 12


class AssistantScreenContext(BaseModel):
    """Compact screen context passed to the assistant router."""

    screen_type: str = Field(default="unknown", max_length=64)
    screen_title: str | None = Field(default=None, max_length=200)
    content_id: int | None = Field(default=None, ge=1)
    news_item_id: int | None = Field(default=None, ge=1)
    visible_content_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_VISIBLE_CONTENT_IDS,
    )
    visible_news_item_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_VISIBLE_CONTENT_IDS,
    )
    selected_topic: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    assistant_action: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_primary_context(self) -> "AssistantScreenContext":
        """Keep canonical content IDs and news item IDs in separate namespaces."""
        if self.content_id is not None and self.news_item_id is not None:
            raise ValueError("content_id and news_item_id are mutually exclusive")
        return self

    @field_validator("visible_content_ids", "visible_news_item_ids", mode="before")
    @classmethod
    def truncate_visible_ids(cls, value: object) -> object:
        """Bound client-provided visible item IDs to the supported limit."""

        if isinstance(value, list):
            return value[:MAX_VISIBLE_CONTENT_IDS]
        return value
