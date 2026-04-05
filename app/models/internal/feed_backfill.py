"""Shared schemas for feed backfill workflows."""

from pydantic import BaseModel, Field

MAX_BACKFILL_COUNT = 50


class FeedBackfillRequest(BaseModel):
    """Input for backfilling a single feed."""

    user_id: int = Field(..., gt=0)
    config_id: int = Field(..., gt=0)
    count: int = Field(..., ge=1, le=MAX_BACKFILL_COUNT)


class FeedBackfillResult(BaseModel):
    """Result from a feed backfill run."""

    config_id: int
    base_limit: int
    target_limit: int
    scraped: int
    saved: int
    duplicates: int
    errors: int
