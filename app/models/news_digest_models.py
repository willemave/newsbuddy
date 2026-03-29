"""Structured LLM outputs for news-native digest generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NewsDigestBulletDraft(BaseModel):
    """One grounded digest bullet generated from a cluster."""

    topic: str = Field(..., min_length=3, max_length=240)
    details: str = Field(..., min_length=20)
    news_item_ids: list[int] = Field(default_factory=list, min_length=1)


class NewsDigestBatchBulletDraft(BaseModel):
    """One curated digest bullet selected from the ranked cluster set."""

    cluster_rank: int = Field(..., ge=1)
    topic: str = Field(..., min_length=3, max_length=240)
    details: str = Field(..., min_length=20)
    news_item_ids: list[int] = Field(default_factory=list, min_length=1)


class NewsDigestBatchDraft(BaseModel):
    """Structured digest curation result for the full candidate set."""

    bullets: list[NewsDigestBatchBulletDraft] = Field(default_factory=list)


class NewsDigestHeaderDraft(BaseModel):
    """Digest-level title and summary generated from final bullets."""

    title: str = Field(..., min_length=3, max_length=240)
    summary: str = Field(..., min_length=20, max_length=800)
