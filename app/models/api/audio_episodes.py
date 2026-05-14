from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AudioEpisodeKind = Literal["fast_news_digest", "content_council_discussion"]
AudioEpisodeStatus = Literal["pending", "processing", "completed", "failed"]


class AudioEpisodeResponse(BaseModel):
    """Client-visible state for one generated audio episode."""

    id: int
    kind: AudioEpisodeKind
    status: AudioEpisodeStatus
    title: str
    source_content_id: int | None = None
    source_item_ids: list[int] = Field(default_factory=list)
    duration_seconds: int | None = None
    audio_url: str | None = None
    script_text: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
