"""Portable search backend for repository queries."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import String, cast, func, or_

from app.models.schema import Content


class SearchBackend(Protocol):
    """Interface for DB-backed content search strategies."""

    def supports_full_text(self) -> bool:
        """Return whether backend uses native full-text support."""

    def apply_search(self, query, query_text: str, context: dict | None = None):
        """Apply search filtering to a SQLAlchemy query."""


class GenericSearchBackend:
    """Portable case-insensitive LIKE search backend."""

    def supports_full_text(self) -> bool:
        """Generic backend does not use native FTS."""
        return False

    def apply_search(self, query, query_text: str, context: dict | None = None):
        """Apply portable string/JSON search predicates."""
        del context
        search = f"%{query_text.lower()}%"
        conditions = or_(
            func.lower(Content.title).like(search),
            func.lower(Content.source).like(search),
            func.lower(cast(Content.content_metadata["summary"]["title"], String)).like(search),
            func.lower(cast(Content.content_metadata["summary"]["overview"], String)).like(search),
            func.lower(cast(Content.content_metadata["summary"]["hook"], String)).like(search),
            func.lower(cast(Content.content_metadata["summary"]["takeaway"], String)).like(search),
            func.lower(cast(Content.search_text, String)).like(search),
        )
        return query.filter(conditions)


def get_search_backend(_db) -> SearchBackend:
    """Return the portable search backend."""
    return GenericSearchBackend()
