from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.models.metadata.longform_artifacts import LongformArtifactEnvelope
from app.models.metadata.summaries import (
    BulletedSummary,
    DiscussionSummary,
    EditorialNarrativeSummary,
    GeneratedEditorialNarrativeSummary,
    InterleavedSummary,
    InterleavedSummaryV2,
    NewsSummary,
    StructuredSummary,
    SummaryPayload,
    _parse_summary_payload,
)


class BaseContentMetadata(BaseModel):
    """Base metadata fields common to all content types."""

    model_config = ConfigDict(extra="allow")

    # NEW: Source field to track content origin
    source: str | None = Field(
        None, description="Source of content (e.g., substack name, podcast name, subreddit name)"
    )

    summary_kind: str | None = Field(
        None,
        description=("Summary discriminator (e.g., long_interleaved, long_structured, short_news)"),
    )
    summary_version: int | None = Field(
        None, ge=1, description="Summary schema version for the current summary_kind"
    )
    summary: SummaryPayload | None = Field(None, description="AI-generated summary payload")
    word_count: int | None = Field(None, ge=0)

    @field_validator("summary", mode="before")
    @classmethod
    def validate_summary(cls, value: SummaryPayload | dict[str, Any] | None, info):
        """Normalize summary payloads into structured models."""
        if value is None or isinstance(
            value,
            (
                StructuredSummary,
                InterleavedSummary,
                InterleavedSummaryV2,
                BulletedSummary,
                EditorialNarrativeSummary,
                GeneratedEditorialNarrativeSummary,
                LongformArtifactEnvelope,
                NewsSummary,
                DiscussionSummary,
            ),
        ):
            return value
        if isinstance(value, dict):
            summary_kind = info.data.get("summary_kind")
            summary_version = info.data.get("summary_version")
            if summary_kind and summary_version:
                return _parse_summary_payload(summary_kind, summary_version, value)
            raise ValueError(
                "summary_kind and summary_version are required when summary is present"
            )
        raise ValueError(
            "Summary must be StructuredSummary, InterleavedSummary, InterleavedSummaryV2, "
            "BulletedSummary, EditorialNarrativeSummary, LongformArtifactEnvelope, "
            "NewsSummary, DiscussionSummary, or dict"
        )
