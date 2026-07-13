from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.api.audio_episodes import AudioEpisodeResponse
from app.models.api.base import UTCDateTime
from app.models.contracts import (
    BriefingBlockType,
    BriefingFigurePlacement,
    BriefingFirstRunPhase,
    BriefingFirstRunSourceOutcome,
    BriefingRunKind,
    BriefingTier,
    ContentType,
)

BRIEFING_DIG_FRAGMENT_MAX_LENGTH = 2000


class BriefingLensSummary(BaseModel):
    key: str
    tier: BriefingTier
    title: str
    deck: str
    position: int
    segment_count: int
    unread_source_count: int


class BriefingFirstRunSourceProgress(BaseModel):
    display_name: str
    processed_item_count: int = Field(ge=0)
    outcome: BriefingFirstRunSourceOutcome


class BriefingFirstRunProgress(BaseModel):
    run_id: int
    revision: int
    phase: BriefingFirstRunPhase
    connected_source_count: int
    completed_sources: list[BriefingFirstRunSourceProgress] = Field(default_factory=list)
    active_sources: list[str] = Field(default_factory=list)
    ready_category_keys: list[str] = Field(default_factory=list)


class BriefingIndexResponse(BaseModel):
    version: int
    masthead_title: str
    masthead_deck: str
    generated_at: UTCDateTime | None = None
    lenses: list[BriefingLensSummary] = Field(default_factory=list)
    first_run: BriefingFirstRunProgress | None = None


class BriefingRunDto(BaseModel):
    kind: BriefingRunKind
    text: str
    source_key: str | None = None
    insight_id: str | None = None
    bold: bool = False


class BriefingParagraphDto(BaseModel):
    runs: list[BriefingRunDto] = Field(default_factory=list)


class BriefingBlockDto(BaseModel):
    type: BriefingBlockType
    weight: str | None = None
    paragraphs: list[BriefingParagraphDto] | None = None
    source_key: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    caption: str | None = None
    placement: BriefingFigurePlacement | None = None
    text: str | None = None


class BriefingSegmentDto(BaseModel):
    id: int
    created_at: UTCDateTime
    status: str
    narration_text: str
    blocks: list[BriefingBlockDto] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)


class BriefingDiscussionDto(BaseModel):
    platform: str
    comment_count: int | None = None
    summary_status: str
    overview: str | None = None
    top_comment_author: str | None = None
    top_comment_text: str | None = None
    external_url: str | None = None
    updated_at: UTCDateTime | None = None


class BriefingSourceDto(BaseModel):
    source_key: str
    kind: str
    id: int
    title: str
    summary: str | None = None
    key_points: list[str] | None = None
    url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    published_at: UTCDateTime | None = None
    content_type: ContentType | None = None
    read: bool = False
    discussion: BriefingDiscussionDto | None = None


class BriefingLensResponse(BaseModel):
    version: int
    lens: BriefingLensSummary
    segments: list[BriefingSegmentDto] = Field(default_factory=list)
    sources: list[BriefingSourceDto] = Field(default_factory=list)


class BriefingReadMarkRequest(BaseModel):
    source_keys: list[str] = Field(..., min_length=1)


class BriefingReadMarkResponse(BaseModel):
    marked: int = Field(..., ge=0)
    version: int


class BriefingDigSearchRequest(BaseModel):
    fragment: str = Field(..., min_length=3, max_length=BRIEFING_DIG_FRAGMENT_MAX_LENGTH)


class BriefingDigSearchResult(BaseModel):
    title: str
    url: str
    snippet: str | None = None
    published_date: str | None = None


class BriefingDigSearchResponse(BaseModel):
    results: list[BriefingDigSearchResult] = Field(default_factory=list)
    elapsed_ms: int = Field(..., ge=0)


class BriefingDigSummarizeRequest(BaseModel):
    fragment: str = Field(..., min_length=3, max_length=BRIEFING_DIG_FRAGMENT_MAX_LENGTH)
    passage_context: str = Field(..., max_length=2000)
    results: list[BriefingDigSearchResult] = Field(default_factory=list)


class BriefingDigSummarizeResponse(BaseModel):
    summary: str
    model: str
    elapsed_ms: int = Field(..., ge=0)


class BriefingNarrationRequest(BaseModel):
    lens_key: str = Field(..., min_length=1, max_length=64)


class BriefingRefreshResponse(BaseModel):
    enqueued: bool
    version: int


BriefingNarrationResponse = AudioEpisodeResponse
