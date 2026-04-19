"""Add YouTube metadata fields to content table

Revision ID: 6a13c2b1e1f8
Revises: 11615658d3d2
Create Date: 2025-07-08 19:15:37.576951

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6a13c2b1e1f8"
down_revision: str | None = "11615658d3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The YouTube-specific fields are stored in the JSONB metadata column
    # No schema changes needed as metadata is flexible
    # This migration serves as documentation that we've added YouTube support

    # Documentation for YouTube metadata fields:
    # - video_url: Original YouTube URL
    # - video_id: YouTube video ID
    # - channel_name: YouTube channel name
    # - thumbnail_url: Video thumbnail URL
    # - view_count: Number of views
    # - like_count: Number of likes
    # - has_transcript: Whether transcript is available
    # - youtube_video: Boolean flag for YouTube content
    pass


def downgrade() -> None:
    """Downgrade schema."""
    # No schema changes to revert
    pass
