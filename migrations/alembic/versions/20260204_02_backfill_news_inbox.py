"""Backfill inbox status for recent news items."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260204_02"
down_revision: str | None = "20260204_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

NEWS_BACKFILL_LIMIT = 100


def upgrade() -> None:
    """Seed recent news items into active user inboxes."""
    connection = op.get_bind()

    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    contents = sa.table(
        "contents",
        sa.column("id", sa.Integer),
        sa.column("content_type", sa.String),
        sa.column("status", sa.String),
        sa.column("classification", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    content_status = sa.table(
        "content_status",
        sa.column("user_id", sa.Integer),
        sa.column("content_id", sa.Integer),
        sa.column("status", sa.String),
    )

    user_rows = connection.execute(
        sa.select(users.c.id).where(users.c.is_active.is_(True))
    ).fetchall()
    user_ids = [row[0] for row in user_rows]
    if not user_ids:
        return None

    for user_id in user_ids:
        existing = sa.select(content_status.c.content_id).where(content_status.c.user_id == user_id)
        news_ids = connection.execute(
            sa.select(contents.c.id)
            .where(
                contents.c.content_type == "news",
                contents.c.status == "completed",
                (contents.c.classification != "skip") | (contents.c.classification.is_(None)),
            )
            .where(~contents.c.id.in_(existing))
            .order_by(contents.c.created_at.desc())
            .limit(NEWS_BACKFILL_LIMIT)
        ).fetchall()

        if not news_ids:
            continue

        connection.execute(
            sa.insert(content_status),
            [
                {
                    "user_id": user_id,
                    "content_id": content_id,
                    "status": "inbox",
                }
                for (content_id,) in news_ids
            ],
        )

    return None


def downgrade() -> None:
    """No-op downgrade."""
    return None
