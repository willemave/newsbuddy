"""Response models for the news-native API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConvertNewsItemResponse(BaseModel):
    """Response for converting a news item into long-form article content."""

    status: str = Field(default="success")
    news_item_id: int
    new_content_id: int
    already_exists: bool
    message: str
