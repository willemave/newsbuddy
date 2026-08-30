"""Retarget stored Cerebras chat sessions to the supported default model.

Revision ID: 20260829_02
Revises: 20260829_01
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260829_02"
down_revision: str | None = "20260829_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Move discontinued provider sessions onto the canonical OpenAI default."""
    op.execute(
        """
        UPDATE chat_sessions
        SET llm_provider = 'openai',
            llm_model = 'openai:gpt-5.6-terra'
        WHERE llm_provider = 'cerebras'
           OR llm_model LIKE 'cerebras:%'
        """
    )


def downgrade() -> None:
    """Keep migrated sessions usable; retired provider assignments are not recoverable."""
