from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.api.base import UTCDateTime
from app.models.contracts import LearningDeckRunStatus, LearningDeckSourceKind, LearningDeckStatus


class LearningDeckCreateRequest(BaseModel):
    """Create or rerun a Learning Deck from one source."""

    content_id: int | None = Field(default=None, gt=0)
    news_item_id: int | None = Field(default=None, gt=0)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    interests_prompt: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_single_source(self) -> LearningDeckCreateRequest:
        source_count = sum(
            value is not None for value in (self.content_id, self.news_item_id, self.url)
        )
        if source_count != 1:
            raise ValueError("Provide exactly one of content_id, news_item_id, or url")
        return self


class LearningDeckTimelineEntry(BaseModel):
    """One coarse user-visible generation note."""

    status: LearningDeckRunStatus
    note: str
    created_at: UTCDateTime


class LearningDeckRunResponse(BaseModel):
    """Client-visible generation attempt state."""

    id: int
    status: LearningDeckRunStatus
    interests_prompt: str | None = None
    timeline: list[LearningDeckTimelineEntry] = Field(default_factory=list)
    error_message: str | None = None
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime | None = None


class LearningDeckResponse(BaseModel):
    """Client-visible Learning Deck state."""

    id: int
    title: str
    source_kind: LearningDeckSourceKind
    source_url: str | None = None
    source_content_id: int | None = None
    source_title: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    status: LearningDeckStatus | None = None
    share_enabled: bool = False
    viewer_available: bool = False
    source_notes_available: bool = False
    thumbnail_url: str | None = None
    latest_successful_run_id: int | None = None
    latest_run: LearningDeckRunResponse | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime | None = None


class LearningDeckListResponse(BaseModel):
    """List response for current user's Learning Decks."""

    decks: list[LearningDeckResponse] = Field(default_factory=list)


class LearningDeckUrlResponse(BaseModel):
    """A URL the client can open in Safari."""

    url: str
    expires_at: UTCDateTime | None = None


class LearningDeckShareResponse(BaseModel):
    """Share state and stable public URL for one deck."""

    share_enabled: bool
    share_url: str | None = None
