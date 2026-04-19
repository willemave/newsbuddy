"""Merge live voice onboarding and analytics interaction heads.

Revision ID: 20260215_03
Revises: 20260215_01, 20260215_02
Create Date: 2026-02-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260215_03"
down_revision: Sequence[str] | str | None = ("20260215_01", "20260215_02")
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    """Merge both heads without additional schema changes."""

    return


def downgrade() -> None:
    """No-op downgrade for merge revision."""

    return
