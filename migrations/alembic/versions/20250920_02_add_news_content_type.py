"""Add news content type support."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

revision = "20250920_02"
down_revision = "20250910_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add aggregate content flag with backwards-compatible defaults."""

    connection: Connection = op.get_bind()
    inspector = inspect(connection)
    existing_columns = {column["name"] for column in inspector.get_columns("contents")}
    if "is_aggregate" not in existing_columns:
        op.add_column(
            "contents",
            sa.Column("is_aggregate", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("contents")}
    if "idx_content_aggregate" not in existing_indexes:
        op.create_index(
            "idx_content_aggregate",
            "contents",
            ["content_type", "is_aggregate"],
        )

    op.execute(sa.text("UPDATE contents SET is_aggregate = FALSE WHERE is_aggregate IS NULL"))

    if connection.dialect.name != "sqlite":
        op.alter_column("contents", "is_aggregate", server_default=None)


def downgrade() -> None:
    """Remove aggregate content flag and supporting index."""

    op.drop_index("idx_content_aggregate", table_name="contents")
    op.drop_column("contents", "is_aggregate")
