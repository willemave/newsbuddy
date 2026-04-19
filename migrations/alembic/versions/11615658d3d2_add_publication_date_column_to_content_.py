"""Add publication_date column to content table

Revision ID: 11615658d3d2
Revises: 91acf780e27e
Create Date: 2025-07-04 23:23:35.696795

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11615658d3d2"
down_revision: str | None = "91acf780e27e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add publication_date column to contents table
    op.add_column("contents", sa.Column("publication_date", sa.DateTime(), nullable=True))

    # Create index for better query performance
    op.create_index(
        op.f("ix_contents_publication_date"), "contents", ["publication_date"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop index first
    op.drop_index(op.f("ix_contents_publication_date"), table_name="contents")

    # Drop publication_date column
    op.drop_column("contents", "publication_date")
