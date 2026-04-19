"""Merge heads for user scraper configs and user tracking."""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20250630_02"
down_revision: Sequence[str] | str | None = ("20250630_01", "f77c586ac45b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op merge."""
    pass


def downgrade() -> None:
    """No-op merge."""
    pass
