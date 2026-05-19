"""drop reddit aggregator configs

Revision ID: 20260519_01
Revises: 20260513_01
Create Date: 2026-05-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260519_01"
down_revision: str | None = "20260513_01"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Remove stale Reddit-as-aggregator subscription rows."""
    op.execute(
        sa.text(
            """
            DELETE FROM user_scraper_configs
            WHERE scraper_type = 'aggregator'
              AND (
                lower(feed_url) = 'aggregator://reddit'
                OR lower(config ->> 'key') = 'reddit'
              )
            """
        )
    )


def downgrade() -> None:
    """No-op: deleted per-user rows cannot be reconstructed."""
