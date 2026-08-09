from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.api.base import UTCDateTime
from app.models.contracts import AudioEpisodeKind, AudioEpisodeStatus

AudioEpisodeDelivery = Literal["background", "stream"]
CUSTOM_NARRATION_MAX_CONTENT_IDS = 12


class CustomNarrationCreateRequest(BaseModel):
    """Create one combined custom narration from selected long-form sources."""

    content_ids: list[int] = Field(default_factory=list)
    news_item_ids: list[int] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=120)
    mark_source_content_read_on_play: bool = False

    @model_validator(mode="after")
    def validate_source_selection(self) -> CustomNarrationCreateRequest:
        """Require at least one source while allowing mixed long-form/Fast Read picks."""

        source_count = len(self.content_ids) + len(self.news_item_ids)
        if source_count < 1:
            raise ValueError("Select at least one article, podcast, or Fast Read")
        if source_count > CUSTOM_NARRATION_MAX_CONTENT_IDS:
            raise ValueError(f"Select at most {CUSTOM_NARRATION_MAX_CONTENT_IDS} sources")
        return self


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
    read_on_play_content_ids: list[int] = Field(default_factory=list)
    read_on_play_news_item_ids: list[int] = Field(default_factory=list)
    duration_seconds: int | None = None
    audio_url: str | None = None
    stream_url: str | None = None
    script_text: str | None = None
    error_message: str | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime | None = None


class AudioEpisodeShareResponse(BaseModel):
    """Public sharing state for a completed narration."""

    share_enabled: bool
    share_page_url: str | None = None
    share_audio_url: str | None = None
