from __future__ import annotations

from pydantic import BaseModel


class FavoriteSnapshot(BaseModel):
    """Compact representation of a favorited content item."""

    id: int
    title: str | None = None
    source: str | None = None
    url: str
    content_type: str
    summary: str | None = None


class DiscoveryRunResult(BaseModel):
    """Result summary for a feed discovery run."""

    run_id: int
    feeds: int
    podcasts: int
    youtube: int
    status: str
