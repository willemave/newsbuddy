"""Pydantic models for pagination."""

from pydantic import BaseModel, Field


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
