from __future__ import annotations

from pydantic import (
    ConfigDict,
    Field,
)

from app.models.metadata.base import BaseContentMetadata


class PodcastMetadata(BaseContentMetadata):
    """Metadata specific to podcasts."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "Lenny's Podcast",
                "audio_url": "https://example.com/episode.mp3",
                "transcript": "Full transcript text...",
                "duration": 3600,
                "episode_number": 42,
                "summary_kind": "long_structured",
                "summary_version": 1,
                "summary": {
                    "overview": "Brief overview of the podcast episode",
                    "bullet_points": [
                        {"text": "Key topic discussed", "category": "key_finding"},
                        {"text": "Important insight shared", "category": "insight"},
                        {"text": "Main conclusion", "category": "conclusion"},
                    ],
                    "quotes": [
                        {"text": "Memorable quote from the episode", "context": "Speaker Name"}
                    ],
                    "topics": ["Podcast", "Discussion", "Interview"],
                    "summarization_date": "2025-06-14T10:30:00Z",
                },
            }
        }
    )

    audio_url: str | None = Field(None, max_length=2000, description="URL to the audio file")
    transcript: str | None = Field(None, description="Full transcript text")
    duration: int | None = Field(None, ge=0, description="Duration in seconds")
    episode_number: int | None = Field(None, ge=0)

    # YouTube-specific fields
    video_url: str | None = Field(None, max_length=2000, description="Original YouTube video URL")
    video_id: str | None = Field(None, max_length=50, description="YouTube video ID")
    channel_name: str | None = Field(None, max_length=200, description="YouTube channel name")
    thumbnail_url: str | None = Field(None, max_length=2000, description="Video thumbnail URL")
    view_count: int | None = Field(None, ge=0, description="Number of views")
    like_count: int | None = Field(None, ge=0, description="Number of likes")
    has_transcript: bool | None = Field(None, description="Whether transcript is available")
