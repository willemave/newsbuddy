"""Add structured bullet details to daily digests."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260327_01"
down_revision: str | None = "20260326_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_news_digests",
        sa.Column(
            "bullet_details",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("daily_news_digests", "bullet_details")
