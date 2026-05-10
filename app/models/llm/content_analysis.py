"""Shared schemas for content analysis workflows."""

from typing import Literal

from pydantic import BaseModel, Field


class ContentAnalysisResult(BaseModel):
    """Structured output schema for content analysis."""

    content_type: Literal["article", "podcast", "video"] = Field(
        ...,
        description=(
            "Type of content: 'article' for web pages/blog posts/news, "
            "'podcast' for audio episodes, 'video' for video content"
        ),
    )
    original_url: str = Field(..., description="The URL that was analyzed")
    media_url: str | None = Field(
        None,
        description=(
            "Direct URL to media file (mp3/mp4/m4a/webm) for podcasts/videos. "
            "Extract from page HTML if available. Look for audio/video source tags, "
            "RSS feed enclosures, or download links."
        ),
    )
    media_format: str | None = Field(
        None,
        description="Media file format/extension: mp3, mp4, m4a, webm, etc.",
    )
    title: str | None = Field(None, description="Content title if detectable from the page")
    description: str | None = Field(None, description="Brief description or subtitle if available")
    duration_seconds: int | None = Field(
        None, description="Duration in seconds for audio/video content if mentioned"
    )
    platform: str | None = Field(
        None,
        description=(
            "Platform name in lowercase: spotify, apple_podcasts, youtube, "
            "substack, medium, transistor, anchor, simplecast, etc."
        ),
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence score for the content type detection (0.0-1.0)",
    )


class InstructionLink(BaseModel):
    """Single link result derived from instruction handling."""

    url: str = Field(..., max_length=2048)
    title: str | None = Field(None, max_length=500)
    context: str | None = Field(None, max_length=1000)
    content_type: Literal["article", "podcast", "video", "news", "unknown"] | None = None
    platform: str | None = Field(None, max_length=50)
    source: str | None = Field(None, max_length=200)


class InstructionResult(BaseModel):
    """Result for share instruction processing."""

    text: str | None = Field(None, max_length=2000)
    links: list[InstructionLink] = Field(default_factory=list)


class ContentAnalysisOutput(BaseModel):
    """Combined analysis output for URL analysis + instruction handling."""

    analysis: ContentAnalysisResult
    instruction: InstructionResult | None = None
