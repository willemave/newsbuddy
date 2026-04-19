"""Bridge to new consolidated schema

Revision ID: 824291a177f2
Revises:
Create Date: 2025-01-04 04:00:00.000000

This migration exists solely to bridge from the old migration chain to the new one.
It's a no-op that just serves as a target for the existing alembic_version entry.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "824291a177f2"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This is a bridge migration - the schema already exists
    # Just mark this revision as complete
    pass


def downgrade() -> None:
    # No-op
    pass
