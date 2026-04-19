"""Add canonical content body storage table and search text column."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260329_02"
down_revision: str | None = "20260329_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contents", sa.Column("search_text", sa.Text(), nullable=True))

    op.create_table(
        "content_bodies",
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(length=20), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False),
        sa.Column("storage_bucket", sa.String(length=255), nullable=True),
        sa.Column("storage_key", sa.String(length=2048), nullable=False),
        sa.Column("content_format", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("content_id", "variant"),
    )
    op.create_index(
        "idx_content_bodies_content_id",
        "content_bodies",
        ["content_id"],
        unique=False,
    )
    op.create_index(
        "idx_content_bodies_storage_key",
        "content_bodies",
        ["storage_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_content_bodies_storage_key", table_name="content_bodies")
    op.drop_index("idx_content_bodies_content_id", table_name="content_bodies")
    op.drop_table("content_bodies")
    op.drop_column("contents", "search_text")
