"""drop news list preference prompt

Revision ID: 20260507_02
Revises: 20260507_01
Create Date: 2026-05-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260507_02"
down_revision: str | None = "20260507_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("users", "news_list_preference_prompt")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("users", sa.Column("news_list_preference_prompt", sa.Text(), nullable=True))
