from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AudioEpisodeKind = Literal[
    "fast_news_digest",
    "content_council_discussion",
    "news_item_discussion",
    "custom_narration",
]
AudioEpisodeDelivery = Literal["background", "stream", "inline"]
AudioEpisodeStatus = Literal["pending", "processing", "completed", "failed"]
CUSTOM_NARRATION_MAX_CONTENT_IDS = 12


class CustomNarrationCreateRequest(BaseModel):
    """Create one combined custom narration from selected long-form sources."""

    content_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=CUSTOM_NARRATION_MAX_CONTENT_IDS,
    )
    title: str | None = Field(default=None, max_length=120)


class AudioEpisodeResponse(BaseModel):
    """Client-visible state for one generated audio episode."""

    id: int
    kind: AudioEpisodeKind
    status: AudioEpisodeStatus
    title: str
    source_content_id: int | None = None
    source_item_ids: list[int] = Field(default_factory=list)
    source_content_ids: list[int] = Field(default_factory=list)
    source_count: int = 0
    source_titles: list[str] = Field(default_factory=list)
    duration_seconds: int | None = None
    audio_url: str | None = None
    stream_url: str | None = None
    script_text: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
