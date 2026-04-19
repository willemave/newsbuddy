"""add_content_read_status_tracking

Revision ID: 91acf780e27e
Revises: 824291a177f2
Create Date: 2025-07-05 16:45:31.737745

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91acf780e27e"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create content_read_status table
    op.create_table(
        "content_read_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for better performance
    op.create_index(
        "idx_content_read_session_content",
        "content_read_status",
        ["session_id", "content_id"],
        unique=True,
    )
    op.create_index("idx_content_read_session", "content_read_status", ["session_id"])
    op.create_index("idx_content_read_content", "content_read_status", ["content_id"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_content_read_content", table_name="content_read_status")
    op.drop_index("idx_content_read_session", table_name="content_read_status")
    op.drop_index("idx_content_read_session_content", table_name="content_read_status")

    # Drop table
    op.drop_table("content_read_status")
