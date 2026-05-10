"""Strict typed models for long-form artifact summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArtifactType = Literal[
    "argument",
    "mental_model",
    "playbook",
    "portrait",
    "briefing",
    "walkthrough",
    "findings",
]

ArtifactAsk = Literal["judge", "learn", "copy", "absorb", "track", "try", "update"]

ARTIFACT_ASK_BY_TYPE: dict[str, str] = {
    "argument": "judge",
    "mental_model": "learn",
    "playbook": "copy",
    "portrait": "absorb",
    "briefing": "track",
    "walkthrough": "try",
    "findings": "update",
}


class StrictArtifactModel(BaseModel):
    """Base model for artifact contracts that reject drift."""

    model_config = ConfigDict(extra="forbid")


class ArtifactQuote(StrictArtifactModel):
    """Source quote used by an artifact."""

    text: str = Field(..., min_length=10, max_length=5000)
    attribution: str | None = Field(None, max_length=300)


class ArtifactKeyPoint(StrictArtifactModel):
    """A headed artifact key point with concrete substance."""

    heading: str = Field(..., min_length=2, max_length=120)
    content: str = Field(..., min_length=20, max_length=900)


class ArgumentExtras(StrictArtifactModel):
    thesis: str = Field(..., min_length=20, max_length=700)
    counterpoint: str = Field(..., min_length=20, max_length=700)


class MentalModelExtras(StrictArtifactModel):
    what_it_explains: str = Field(..., min_length=20, max_length=700)
    when_to_use_it: str = Field(..., min_length=20, max_length=700)


class PlaybookExtras(StrictArtifactModel):
    situation: str = Field(..., min_length=20, max_length=700)
    outcome: str = Field(..., min_length=20, max_length=700)


class PortraitExtras(StrictArtifactModel):
    background: str = Field(..., min_length=20, max_length=700)
    current_focus: str = Field(..., min_length=20, max_length=700)


class BriefingTimelineItem(StrictArtifactModel):
    when: str = Field(..., min_length=1, max_length=160)
    what: str = Field(..., min_length=5, max_length=500)


class BriefingKeyActor(StrictArtifactModel):
    name: str = Field(..., min_length=1, max_length=200)
    stake: str = Field(..., min_length=5, max_length=500)


class BriefingExtras(StrictArtifactModel):
    timeline: list[BriefingTimelineItem] = Field(..., min_length=1, max_length=12)
    key_actors: list[BriefingKeyActor] = Field(..., min_length=1, max_length=12)
    what_to_watch: str = Field(..., min_length=20, max_length=700)


class WalkthroughExtras(StrictArtifactModel):
    what_youll_make: str = Field(..., min_length=20, max_length=700)
    prereqs: list[str] = Field(..., min_length=1, max_length=12)
    time_or_cost: str = Field(..., min_length=1, max_length=300)


class FindingsExtras(StrictArtifactModel):
    question: str = Field(..., min_length=20, max_length=700)
    method: str = Field(..., min_length=20, max_length=700)
    limits: str = Field(..., min_length=20, max_length=700)


class ArtifactPayloadBase(StrictArtifactModel):
    """Universal five-block artifact payload."""

    overview: str = Field(..., min_length=60, max_length=1600)
    quotes: list[ArtifactQuote] = Field(..., min_length=2, max_length=5)
    key_points: list[ArtifactKeyPoint] = Field(..., min_length=4, max_length=8)
    takeaway: str = Field(..., min_length=20, max_length=300)


class ArgumentPayload(ArtifactPayloadBase):
    extras: ArgumentExtras


class MentalModelPayload(ArtifactPayloadBase):
    extras: MentalModelExtras


class PlaybookPayload(ArtifactPayloadBase):
    extras: PlaybookExtras


class PortraitPayload(ArtifactPayloadBase):
    extras: PortraitExtras


class BriefingPayload(ArtifactPayloadBase):
    extras: BriefingExtras


class WalkthroughPayload(ArtifactPayloadBase):
    extras: WalkthroughExtras


class FindingsPayload(ArtifactPayloadBase):
    extras: FindingsExtras


class ArgumentArtifact(StrictArtifactModel):
    type: Literal["argument"] = "argument"
    payload: ArgumentPayload


class MentalModelArtifact(StrictArtifactModel):
    type: Literal["mental_model"] = "mental_model"
    payload: MentalModelPayload


class PlaybookArtifact(StrictArtifactModel):
    type: Literal["playbook"] = "playbook"
    payload: PlaybookPayload


class PortraitArtifact(StrictArtifactModel):
    type: Literal["portrait"] = "portrait"
    payload: PortraitPayload


class BriefingArtifact(StrictArtifactModel):
    type: Literal["briefing"] = "briefing"
    payload: BriefingPayload


class WalkthroughArtifact(StrictArtifactModel):
    type: Literal["walkthrough"] = "walkthrough"
    payload: WalkthroughPayload


class FindingsArtifact(StrictArtifactModel):
    type: Literal["findings"] = "findings"
    payload: FindingsPayload


LongformArtifactBody = Annotated[
    ArgumentArtifact
    | MentalModelArtifact
    | PlaybookArtifact
    | PortraitArtifact
    | BriefingArtifact
    | WalkthroughArtifact
    | FindingsArtifact,
    Field(discriminator="type"),
]


class SourceContext(StrictArtifactModel):
    url: str
    source_name: str | None = None
    publication_date: str | None = None
    platform: str | None = None


class SelectionTrace(StrictArtifactModel):
    source_hint: str
    candidates: list[ArtifactType] = Field(..., min_length=1, max_length=7)
    selected: ArtifactType
    reason: str = Field(..., min_length=10, max_length=1000)
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def selected_must_be_candidate(self) -> SelectionTrace:
        if self.selected not in self.candidates:
            raise ValueError("selection_trace.selected must be present in candidates")
        return self


class FeedPreview(StrictArtifactModel):
    title: str = Field(..., min_length=5, max_length=300)
    one_line: str = Field(..., min_length=20, max_length=500)
    preview_bullets: list[str] = Field(..., min_length=1, max_length=3)
    reason_to_read: str = Field(..., min_length=20, max_length=500)
    artifact_type: ArtifactType


class LongformArtifactEnvelope(StrictArtifactModel):
    title: str = Field(..., min_length=5, max_length=300)
    one_line: str = Field(..., min_length=20, max_length=500)
    ask: ArtifactAsk
    artifact: LongformArtifactBody
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_context: SourceContext
    selection_trace: SelectionTrace
    feed_preview: FeedPreview

    @model_validator(mode="after")
    def validate_consistent_type_fields(self) -> LongformArtifactEnvelope:
        artifact_type = self.artifact.type
        expected_ask = ARTIFACT_ASK_BY_TYPE[artifact_type]
        if self.ask != expected_ask:
            raise ValueError(f"ask must be {expected_ask!r} for artifact type {artifact_type!r}")
        if self.selection_trace.selected != artifact_type:
            raise ValueError("selection_trace.selected must match artifact.type")
        if self.feed_preview.artifact_type != artifact_type:
            raise ValueError("feed_preview.artifact_type must match artifact.type")
        return self
