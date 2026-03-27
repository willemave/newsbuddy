"""Add per-user X digest filter prompt."""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260326_01"
down_revision: Union[str, None] = "20260319_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("x_digest_filter_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "x_digest_filter_prompt")
