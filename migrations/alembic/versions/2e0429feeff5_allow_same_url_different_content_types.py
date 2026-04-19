"""allow_same_url_different_content_types

Revision ID: 2e0429feeff5
Revises: 20250920_02
Create Date: 2025-10-13 10:37:46.657625

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e0429feeff5"
down_revision: str | None = "20250920_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove unique constraint on url, add composite unique on (url, content_type)."""
    # Check database dialect
    conn = op.get_bind()

    if conn.dialect.name == "sqlite":
        # SQLite: Manual table recreation approach
        # Create new table with composite unique constraint
        op.execute("""
            CREATE TABLE contents_new (
                id INTEGER NOT NULL,
                content_type VARCHAR(20) NOT NULL,
                url VARCHAR(2048) NOT NULL,
                title VARCHAR(500),
                source VARCHAR(100),
                status VARCHAR(20) DEFAULT 'new' NOT NULL,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                classification VARCHAR(20),
                checked_out_by VARCHAR(100),
                checked_out_at DATETIME,
                content_metadata JSON DEFAULT '{}' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME,
                processed_at DATETIME,
                publication_date DATETIME,
                platform VARCHAR(50),
                is_aggregate BOOLEAN DEFAULT 0 NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (url, content_type)
            )
        """)

        # Copy data from old table
        op.execute("""
            INSERT INTO contents_new
            SELECT * FROM contents
        """)

        # Drop old table
        op.execute("DROP TABLE contents")

        # Rename new table
        op.execute("ALTER TABLE contents_new RENAME TO contents")

        # Recreate indexes
        op.create_index("ix_contents_checked_out_by", "contents", ["checked_out_by"])
        op.create_index("idx_content_type_status", "contents", ["content_type", "status"])
        op.create_index("ix_contents_source", "contents", ["source"])
        op.create_index("ix_contents_content_type", "contents", ["content_type"])
        op.create_index("idx_content_aggregate", "contents", ["content_type", "is_aggregate"])
        op.create_index("idx_checkout", "contents", ["checked_out_by", "checked_out_at"])
        op.create_index("ix_contents_publication_date", "contents", ["publication_date"])
        op.create_index("ix_contents_classification", "contents", ["classification"])
        op.create_index("idx_created_at", "contents", ["created_at"])
        op.create_index("ix_contents_status", "contents", ["status"])
        op.create_index("ix_contents_platform", "contents", ["platform"])
        op.create_index("ix_contents_is_aggregate", "contents", ["is_aggregate"])
    else:
        # PostgreSQL: Drop constraint and create index
        op.drop_constraint("contents_url_key", "contents", type_="unique")
        op.create_index("idx_url_content_type", "contents", ["url", "content_type"], unique=True)


def downgrade() -> None:
    """Restore single url unique constraint."""
    conn = op.get_bind()

    if conn.dialect.name == "sqlite":
        # SQLite: Manual table recreation approach
        # Create new table with single URL unique constraint
        op.execute("""
            CREATE TABLE contents_new (
                id INTEGER NOT NULL,
                content_type VARCHAR(20) NOT NULL,
                url VARCHAR(2048) NOT NULL,
                title VARCHAR(500),
                source VARCHAR(100),
                status VARCHAR(20) DEFAULT 'new' NOT NULL,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                classification VARCHAR(20),
                checked_out_by VARCHAR(100),
                checked_out_at DATETIME,
                content_metadata JSON DEFAULT '{}' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME,
                processed_at DATETIME,
                publication_date DATETIME,
                platform VARCHAR(50),
                is_aggregate BOOLEAN DEFAULT 0 NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (url)
            )
        """)

        # Copy data from old table
        op.execute("""
            INSERT INTO contents_new
            SELECT * FROM contents
        """)

        # Drop old table
        op.execute("DROP TABLE contents")

        # Rename new table
        op.execute("ALTER TABLE contents_new RENAME TO contents")

        # Recreate indexes
        op.create_index("ix_contents_checked_out_by", "contents", ["checked_out_by"])
        op.create_index("idx_content_type_status", "contents", ["content_type", "status"])
        op.create_index("ix_contents_source", "contents", ["source"])
        op.create_index("ix_contents_content_type", "contents", ["content_type"])
        op.create_index("idx_content_aggregate", "contents", ["content_type", "is_aggregate"])
        op.create_index("idx_checkout", "contents", ["checked_out_by", "checked_out_at"])
        op.create_index("ix_contents_publication_date", "contents", ["publication_date"])
        op.create_index("ix_contents_classification", "contents", ["classification"])
        op.create_index("idx_created_at", "contents", ["created_at"])
        op.create_index("ix_contents_status", "contents", ["status"])
        op.create_index("ix_contents_platform", "contents", ["platform"])
        op.create_index("ix_contents_is_aggregate", "contents", ["is_aggregate"])
    else:
        # PostgreSQL: Drop index and restore constraint
        op.drop_index("idx_url_content_type", "contents")
        op.create_unique_constraint("contents_url_key", "contents", ["url"])
