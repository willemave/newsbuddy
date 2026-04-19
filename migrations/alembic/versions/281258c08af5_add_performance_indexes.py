"""add_performance_indexes

Revision ID: 281258c08af5
Revises: cdcc53c1ac56
Create Date: 2025-12-07 10:37:11.867536

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "281258c08af5"
down_revision: str | None = "cdcc53c1ac56"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add performance indexes for slow queries.

    Key optimizations:
    1. content_status composite index - speeds up inbox_exists correlated subquery
    2. contents visibility index - speeds up filtered content list queries
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Critical: composite index for inbox_exists subquery lookups
    # Query pattern: WHERE user_id=? AND status='inbox' AND content_id=?
    if not _index_exists(inspector, "content_status", "idx_content_status_user_status_content"):
        op.create_index(
            "idx_content_status_user_status_content",
            "content_status",
            ["user_id", "status", "content_id"],
            unique=False,
        )

    # Index for content visibility queries (summarized non-skipped content)
    # Query pattern: WHERE classification != 'skip' OR classification IS NULL
    if not _index_exists(inspector, "contents", "idx_contents_classification_status"):
        op.create_index(
            "idx_contents_classification_status",
            "contents",
            ["classification", "status", "content_type"],
            unique=False,
        )


def downgrade() -> None:
    """Remove performance indexes."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "content_status", "idx_content_status_user_status_content"):
        op.drop_index("idx_content_status_user_status_content", table_name="content_status")
    if _index_exists(inspector, "contents", "idx_contents_classification_status"):
        op.drop_index("idx_contents_classification_status", table_name="contents")


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    existing = inspector.get_indexes(table_name)
    return any(idx.get("name") == index_name for idx in existing)
