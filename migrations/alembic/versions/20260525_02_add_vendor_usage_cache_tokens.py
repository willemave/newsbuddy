"""add vendor usage cache tokens

Revision ID: 20260525_02
Revises: 20260525_01
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260525_02"
down_revision: str | None = "20260525_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("vendor_usage_records", sa.Column("cache_read_tokens", sa.Integer()))
    op.add_column("vendor_usage_records", sa.Column("cache_write_tokens", sa.Integer()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("vendor_usage_records", "cache_write_tokens")
    op.drop_column("vendor_usage_records", "cache_read_tokens")
