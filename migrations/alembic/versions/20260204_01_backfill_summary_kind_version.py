"""Backfill summary metadata for legacy content."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260204_01"
down_revision: str | None = "20260201_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SUMMARY_KIND_LONG_INTERLEAVED = "long_interleaved"
SUMMARY_KIND_LONG_STRUCTURED = "long_structured"
SUMMARY_KIND_LONG_BULLETS = "long_bullets"
SUMMARY_KIND_SHORT_NEWS_DIGEST = "short_news_digest"
SUMMARY_VERSION_V1 = 1
SUMMARY_VERSION_V2 = 2


def _infer_summary_kind_version(
    content_type: str,
    summary: dict[str, Any],
    summary_kind: str | None,
    summary_version: int | None,
) -> tuple[str, int] | None:
    if summary_kind and summary_version:
        return summary_kind, summary_version

    if summary_kind and not summary_version:
        if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
            if "key_points" in summary:
                return summary_kind, SUMMARY_VERSION_V2
            return summary_kind, SUMMARY_VERSION_V1
        if summary_kind in {
            SUMMARY_KIND_LONG_STRUCTURED,
            SUMMARY_KIND_LONG_BULLETS,
            SUMMARY_KIND_SHORT_NEWS_DIGEST,
        }:
            return summary_kind, SUMMARY_VERSION_V1

    if content_type == "news":
        return SUMMARY_KIND_SHORT_NEWS_DIGEST, SUMMARY_VERSION_V1

    summary_type = summary.get("summary_type")
    if summary_type == "interleaved":
        return SUMMARY_KIND_LONG_INTERLEAVED, SUMMARY_VERSION_V1
    if summary_type == "news_digest":
        return SUMMARY_KIND_SHORT_NEWS_DIGEST, SUMMARY_VERSION_V1

    if "key_points" in summary and "topics" in summary:
        return SUMMARY_KIND_LONG_INTERLEAVED, SUMMARY_VERSION_V2
    if "insights" in summary:
        return SUMMARY_KIND_LONG_INTERLEAVED, SUMMARY_VERSION_V1
    if "points" in summary:
        return SUMMARY_KIND_LONG_BULLETS, SUMMARY_VERSION_V1
    if "bullet_points" in summary or "overview" in summary:
        return SUMMARY_KIND_LONG_STRUCTURED, SUMMARY_VERSION_V1

    return None


def upgrade() -> None:
    """Backfill summary_kind/summary_version in content metadata."""
    connection = op.get_bind()
    contents = sa.table(
        "contents",
        sa.column("id", sa.Integer),
        sa.column("content_type", sa.String),
        sa.column("content_metadata", sa.JSON),
    )

    rows = connection.execute(
        sa.select(contents.c.id, contents.c.content_type, contents.c.content_metadata).where(
            contents.c.content_metadata.is_not(None)
        )
    )

    for row in rows:
        metadata = row.content_metadata or {}
        if not isinstance(metadata, dict):
            continue

        summary = metadata.get("summary")
        if not isinstance(summary, dict):
            continue

        summary_kind = metadata.get("summary_kind")
        summary_version = metadata.get("summary_version")
        if summary_kind and summary_version:
            continue

        inferred = _infer_summary_kind_version(
            row.content_type or "",
            summary,
            summary_kind,
            summary_version,
        )
        if not inferred:
            continue

        inferred_kind, inferred_version = inferred
        updated = False
        if not summary_kind:
            metadata["summary_kind"] = inferred_kind
            updated = True
        if not summary_version:
            metadata["summary_version"] = inferred_version
            updated = True
        if updated:
            connection.execute(
                sa.update(contents).where(contents.c.id == row.id).values(content_metadata=metadata)
            )


def downgrade() -> None:
    """No-op downgrade."""
    return None
