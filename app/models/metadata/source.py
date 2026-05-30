"""Source-level metadata shared across content families."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceMetadataKind = Literal["research_paper"]
SourceMetadataProvider = Literal["arxiv"]
AffiliationSource = Literal["arxiv_api", "pdf_inferred", "missing"]
AffiliationConfidence = Literal["direct", "inferred", "unknown"]


class SourceMetadataAuthor(BaseModel):
    """Author metadata for display-only source context."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=300)
    affiliation: str | None = Field(default=None, max_length=500)
    affiliation_source: AffiliationSource = "missing"
    confidence: AffiliationConfidence = "unknown"

    @field_validator("name", "affiliation", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            return cleaned or None
        return value


class SourceMetadataCategory(BaseModel):
    """Research category metadata from the source provider."""

    model_config = ConfigDict(extra="ignore")

    term: str = Field(..., min_length=1, max_length=120)
    primary: bool = False

    @field_validator("term", mode="before")
    @classmethod
    def normalize_term(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value


class SourceMetadataEnvelope(BaseModel):
    """Versioned metadata envelope for display-only source details."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    kind: SourceMetadataKind = "research_paper"
    provider: SourceMetadataProvider = "arxiv"
    source_id: str | None = Field(default=None, max_length=160)
    canonical_abs_url: str | None = Field(default=None, max_length=2048)
    pdf_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=1000)
    abstract: str | None = Field(default=None, max_length=12000)
    brief_synopsis: str | None = Field(default=None, max_length=700)
    authors: list[SourceMetadataAuthor] = Field(default_factory=list, max_length=100)
    categories: list[SourceMetadataCategory] = Field(default_factory=list, max_length=50)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    doi: str | None = Field(default=None, max_length=300)
    journal_ref: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=1000)
    extracted_at: datetime | None = None

    @field_validator(
        "source_id",
        "canonical_abs_url",
        "pdf_url",
        "title",
        "abstract",
        "brief_synopsis",
        "doi",
        "journal_ref",
        "comment",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            return cleaned or None
        return value
