"""Add per-user X digest filter prompt."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260326_01"
down_revision: str | None = "20260319_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("x_digest_filter_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "x_digest_filter_prompt")
