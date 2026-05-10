"""Pydantic models for pagination."""

from datetime import datetime

from pydantic import BaseModel, Field


class PaginationCursorData(BaseModel):
    """Internal model for cursor structure validation."""

    last_id: int = Field(..., description="ID of last item in current page")
    last_created_at: datetime = Field(..., description="Created timestamp of last item")
    filters_hash: str | None = Field(None, description="Hash of filter parameters for validation")


class PaginationMetadata(BaseModel):
    """Pagination metadata for responses."""

    next_cursor: str | None = Field(
        None, description="Opaque cursor token for next page (null if no more results)"
    )
    has_more: bool = Field(False, description="Whether more results are available")
    page_size: int = Field(..., description="Number of items in current response")
    total: int | None = Field(
        None,
        description="Total number of items (expensive to compute, may be omitted)",
    )
