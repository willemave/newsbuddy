"""backfill_chat_sessions_for_favorites

Create ChatSession records for existing favorites that don't have one.

Revision ID: 1f5c42ea0015
Revises: 281258c08af5
Create Date: 2025-12-27 13:55:55.304543

"""

from collections.abc import Sequence

from sqlalchemy.sql import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f5c42ea0015"
down_revision: str | None = "281258c08af5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default LLM settings for backfilled sessions
DEFAULT_LLM_PROVIDER = "anthropic"
DEFAULT_LLM_MODEL = "anthropic:claude-sonnet-4-20250514"


def upgrade() -> None:
    """Create ChatSession records for existing favorites without sessions."""
    conn = op.get_bind()

    # Find all favorites that don't have a corresponding chat session
    # (matching on user_id and content_id)
    favorites_without_sessions = conn.execute(
        text("""
            SELECT cf.user_id, cf.content_id, cf.favorited_at, c.title, c.source
            FROM content_favorites cf
            JOIN contents c ON c.id = cf.content_id
            LEFT JOIN chat_sessions cs ON cs.user_id = cf.user_id
                AND cs.content_id = cf.content_id
                AND cs.is_archived IS FALSE
            WHERE cs.id IS NULL
        """)
    ).fetchall()

    print(f"Found {len(favorites_without_sessions)} favorites without chat sessions")

    # Create chat sessions for each
    for row in favorites_without_sessions:
        user_id, content_id, favorited_at, title, source = row

        # Build session title
        session_title = title or source or "Saved Article"

        conn.execute(
            text("""
                INSERT INTO chat_sessions
                    (user_id, content_id, title, session_type, llm_provider, llm_model,
                     created_at, is_archived)
                VALUES
                    (:user_id, :content_id, :title, 'article_brain', :llm_provider, :llm_model,
                     :created_at, FALSE)
            """),
            {
                "user_id": user_id,
                "content_id": content_id,
                "title": session_title,
                "llm_provider": DEFAULT_LLM_PROVIDER,
                "llm_model": DEFAULT_LLM_MODEL,
                "created_at": favorited_at,
            },
        )

    print(f"Created {len(favorites_without_sessions)} chat sessions for favorites")


def downgrade() -> None:
    """Remove chat sessions created by this migration.

    Note: This only removes sessions that have no messages, to avoid data loss.
    """
    conn = op.get_bind()

    # Delete chat sessions that:
    # 1. Have a linked content_id
    # 2. Are article_brain type
    # 3. Have no messages
    result = conn.execute(
        text("""
            DELETE FROM chat_sessions
            WHERE id IN (
                SELECT cs.id
                FROM chat_sessions cs
                LEFT JOIN chat_messages cm ON cm.session_id = cs.id
                WHERE cs.content_id IS NOT NULL
                    AND cs.session_type = 'article_brain'
                    AND cm.id IS NULL
            )
        """)
    )
    print(f"Deleted {result.rowcount} empty article_brain chat sessions")
