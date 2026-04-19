"""Add source_url column and migrate discussion URLs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision = "20260103_01"
down_revision = "9b2a6f9e5c1d"
branch_labels = None
depends_on = None


def _is_http_url(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith("http://") or value.startswith("https://")


def upgrade() -> None:
    """Add source_url column and backfill discussion_url for news metadata."""
    connection: Connection = op.get_bind()
    inspector = inspect(connection)
    existing_columns = {column["name"] for column in inspector.get_columns("contents")}

    if "source_url" not in existing_columns:
        op.add_column("contents", sa.Column("source_url", sa.String(2048), nullable=True))

    connection.execute(sa.text("UPDATE contents SET source_url = url WHERE source_url IS NULL"))

    contents = sa.table(
        "contents",
        sa.column("id", sa.Integer),
        sa.column("content_type", sa.String),
        sa.column("content_metadata", sa.JSON),
    )

    rows = connection.execute(
        sa.select(contents.c.id, contents.c.content_metadata).where(
            contents.c.content_type == "news"
        )
    ).fetchall()

    for row in rows:
        metadata = row.content_metadata or {}
        if not isinstance(metadata, dict):
            continue

        updated = False
        discussion_url = metadata.get("discussion_url")
        aggregator = metadata.get("aggregator")

        if not discussion_url and isinstance(aggregator, dict):
            agg_url = aggregator.get("url")
            if isinstance(agg_url, str) and _is_http_url(agg_url):
                metadata["discussion_url"] = agg_url
                aggregator.pop("url", None)
                metadata["aggregator"] = aggregator
                updated = True

        primary_url = metadata.get("primary_url")
        if (
            not metadata.get("discussion_url")
            and isinstance(primary_url, str)
            and _is_http_url(primary_url)
        ):
            metadata["discussion_url"] = primary_url
            updated = True

        if updated:
            connection.execute(
                sa.update(contents).where(contents.c.id == row.id).values(content_metadata=metadata)
            )


def downgrade() -> None:
    """Drop source_url column."""
    op.drop_column("contents", "source_url")
