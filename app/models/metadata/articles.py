from __future__ import annotations

from datetime import datetime

from pydantic import (
    ConfigDict,
    Field,
    field_validator,
)

from app.models.metadata.base import BaseContentMetadata
from app.models.metadata.summaries import InterestingExternalLink


class ArticleMetadata(BaseContentMetadata):
    """Metadata specific to articles."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "Import AI",
                "content": "Full article text...",
                "author": "John Doe",
                "publication_date": "2025-06-14T00:00:00",
                "content_type": "html",
                "final_url_after_redirects": "https://example.com/article",
                "word_count": 1500,
                "summary_kind": "long_structured",
                "summary_version": 1,
                "summary": {
                    "overview": "Brief overview of the article content",
                    "bullet_points": [
                        {"text": "Key point 1", "category": "key_finding"},
                        {"text": "Key point 2", "category": "methodology"},
                        {"text": "Key point 3", "category": "conclusion"},
                    ],
                    "quotes": [
                        {"text": "Notable quote from the article", "context": "Author Name"}
                    ],
                    "topics": ["Technology", "Innovation"],
                    "summarization_date": "2025-06-14T10:30:00Z",
                },
            }
        }
    )

    content: str | None = Field(default=None, description="Full article text content")

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        """Allow empty string for legacy data but convert to None."""
        if v == "":
            return None
        return v

    author: str | None = Field(default=None, max_length=200)
    publication_date: datetime | None = None
    content_type: str = Field(default="html", pattern="^(pdf|html|text|markdown|image)$")
    final_url_after_redirects: str | None = Field(default=None, max_length=2000)
    interesting_external_links: list[InterestingExternalLink] = Field(
        default_factory=list,
        max_length=8,
        description="Curated external links from the article body that are worth surfacing.",
    )


# Podcast metadata from app/schemas/metadata.py
