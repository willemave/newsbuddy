"""Unify digest preference prompt across news and X surfaces."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260329_01"
down_revision: str | None = "20260328_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("news_digest_preference_prompt", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE users "
            "SET news_digest_preference_prompt = x_digest_filter_prompt "
            "WHERE x_digest_filter_prompt IS NOT NULL"
        )
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("x_digest_filter_prompt")


def downgrade() -> None:
    op.add_column("users", sa.Column("x_digest_filter_prompt", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE users "
            "SET x_digest_filter_prompt = news_digest_preference_prompt "
            "WHERE news_digest_preference_prompt IS NOT NULL"
        )
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("news_digest_preference_prompt")
