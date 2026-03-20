"""
Unified metadata models for content types.
Merges functionality from app/schemas/metadata.py and app/domain/content.py.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.constants import (
    SUMMARY_KIND_LONG_BULLETS,
    SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
    SUMMARY_KIND_LONG_INTERLEAVED,
    SUMMARY_KIND_LONG_STRUCTURED,
    SUMMARY_KIND_SHORT_NEWS_DIGEST,
    SUMMARY_VERSION_V1,
    SUMMARY_VERSION_V2,
)
from app.models.contracts import (
    ContentClassification,  # noqa: F401 - backward-compatible re-export
    ContentStatus,
    ContentType,
)
from app.utils.summary_utils import extract_short_summary, extract_summary_text


# Structured summary components from app/schemas/metadata.py
class SummaryBulletPoint(BaseModel):
    """Individual bullet point in a structured summary."""

    text: str = Field(..., min_length=10, max_length=500)
    category: str | None = Field(
        None,
        description=(
            "Category of the bullet point (e.g., 'key_finding', 'methodology', 'conclusion')"
        ),
    )


class SummaryTextBullet(BaseModel):
    """Simple bullet point with just text."""

    text: str = Field(..., min_length=10, max_length=500)


class ContentQuote(BaseModel):
    """Notable quote extracted from content."""

    text: str = Field(..., min_length=10, max_length=5000)
    context: str | None = Field(None, description="Context or attribution for the quote")
    attribution: str | None = Field(
        None, description="Who said the quote - author, speaker, or publication (optional)"
    )


class InterleavedInsight(BaseModel):
    """Single insight with bundled topic, text, and supporting quote."""

    topic: str = Field(
        ..., min_length=2, max_length=50, description="Key topic or theme (2-5 words)"
    )
    insight: str = Field(..., min_length=50, description="Substantive insight (2-3 sentences)")
    supporting_quote: str | None = Field(
        None,
        min_length=10,
        description="Short direct quote supporting the insight (optional)",
    )
    quote_attribution: str | None = Field(
        None, description="Who said the quote - author, speaker, or publication (optional)"
    )


class InterleavedSummary(BaseModel):
    """Interleaved summary v1 format that weaves topics with supporting quotes."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "summary_type": "interleaved",
                "title": "AI Advances in Natural Language Processing",
                "hook": (
                    "This article explores groundbreaking developments in NLP "
                    "that could reshape how we interact with technology."
                ),
                "insights": [
                    {
                        "topic": "Performance Gains",
                        "insight": (
                            "The new model achieves 40% improvement in accuracy "
                            "on standard benchmarks while using half the compute."
                        ),
                        "supporting_quote": (
                            "We were surprised by the magnitude of the improvements, "
                            "which exceeded our initial expectations significantly."
                        ),
                        "quote_attribution": "Lead Researcher",
                    }
                ],
                "takeaway": (
                    "These developments signal a fundamental shift in how AI systems "
                    "process and understand human language."
                ),
                "classification": "to_read",
                "summarization_date": "2025-06-14T10:30:00Z",
            }
        }
    )

    summary_type: str = Field(
        default="interleaved", description="Discriminator field for iOS client"
    )
    title: str = Field(
        ..., min_length=5, max_length=1000, description="Descriptive title for the content"
    )
    hook: str = Field(
        ..., min_length=80, description="Opening hook (2-3 sentences) capturing the main story"
    )
    insights: list[InterleavedInsight] = Field(
        ..., min_length=3, description="Key insights with optional supporting quotes (target <20)"
    )
    takeaway: str = Field(
        ..., min_length=80, description="Final takeaway (2-3 sentences) for the reader"
    )
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Content classification: 'to_read' or 'skip'",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InterleavedTopic(BaseModel):
    """Topic section with focused bullet points."""

    topic: str = Field(
        ..., min_length=2, max_length=80, description="Key topic or theme (2-5 words)"
    )
    bullets: list[SummaryTextBullet] = Field(
        ..., min_length=2, max_length=3, description="2-3 bullet points for the topic"
    )


class InterleavedSummaryV2(BaseModel):
    """Interleaved summary v2 format with key points, quotes, and topic bullets."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "AI Advances in Natural Language Processing",
                "hook": (
                    "This article explores groundbreaking developments in NLP "
                    "that could reshape how we interact with technology."
                ),
                "key_points": [
                    {"text": "Model accuracy improved ~40% on standard benchmarks."},
                    {"text": "Training cost dropped by roughly half."},
                    {"text": "Implications include faster deployment in production NLP."},
                ],
                "topics": [
                    {
                        "topic": "Performance Gains",
                        "bullets": [
                            {"text": "Benchmark improvements are consistent across tasks."},
                            {"text": "Compute efficiency allows broader deployment."},
                        ],
                    }
                ],
                "quotes": [
                    {
                        "text": (
                            "We were surprised by the magnitude of the improvements, "
                            "which exceeded our initial expectations significantly."
                        ),
                        "attribution": "Lead Researcher",
                        "context": "Interview with the lab",
                    }
                ],
                "takeaway": (
                    "These developments signal a fundamental shift in how AI systems "
                    "process and understand human language."
                ),
                "classification": "to_read",
                "summarization_date": "2025-06-14T10:30:00Z",
            }
        }
    )

    title: str = Field(
        ..., min_length=5, max_length=1000, description="Descriptive title for the content"
    )
    hook: str = Field(
        ..., min_length=80, description="Opening hook (2-3 sentences) capturing the main story"
    )
    key_points: list[SummaryTextBullet] = Field(
        ..., min_length=3, max_length=5, description="3-5 key bullet points"
    )
    topics: list[InterleavedTopic] = Field(
        ..., min_length=2, description="Topic sections with 2-3 bullets each"
    )
    quotes: list[ContentQuote] = Field(
        default_factory=list, max_length=20, description="Notable longer quotes"
    )
    takeaway: str = Field(
        ..., min_length=80, description="Final takeaway (2-3 sentences) for the reader"
    )
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Content classification: 'to_read' or 'skip'",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BulletSummaryPoint(BaseModel):
    """Bullet point with supporting detail and quotes."""

    text: str = Field(..., min_length=10, max_length=500, description="One-sentence main bullet")
    detail: str = Field(..., min_length=30, max_length=1200, description="2-3 sentence expansion")
    quotes: list[ContentQuote] = Field(
        ..., min_length=1, max_length=3, description="1-3 supporting quotes"
    )


class BulletedSummary(BaseModel):
    """Bullet-first summary format with expandable details and quotes."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "AI Agents Are Becoming a Default Interface",
                "points": [
                    {
                        "text": "Enterprises are standardizing agent workflows across teams.",
                        "detail": (
                            "Large orgs are consolidating agent tools to reduce duplication "
                            "and improve governance. This shift is driven by procurement and "
                            "security teams looking for consistent controls."
                        ),
                        "quotes": [
                            {
                                "text": "We can't have five different agent stacks in one company.",
                                "context": "Security lead",
                            }
                        ],
                    }
                ],
                "classification": "to_read",
                "summarization_date": "2025-10-01T12:00:00Z",
            }
        }
    )

    title: str = Field(
        ..., min_length=5, max_length=1000, description="Descriptive title for the content"
    )
    points: list[BulletSummaryPoint] = Field(..., min_length=10, max_length=30)
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Content classification: 'to_read' or 'skip'",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EditorialQuote(BaseModel):
    """Quote snippet in editorial narrative summaries."""

    text: str = Field(..., min_length=10, max_length=5000)
    attribution: str | None = Field(
        None, description="Who said the quote - author, speaker, or publication (optional)"
    )


class EditorialKeyPoint(BaseModel):
    """Key point entry in editorial narrative summaries."""

    point: str = Field(..., min_length=10, max_length=500)


class EditorialArchetypeReaction(BaseModel):
    """Persona-style reaction block for long-form summaries."""

    archetype: Literal["Paul Graham", "Andy Grove", "Charlie Munger"]
    paragraphs: list[str] = Field(
        ...,
        min_length=2,
        max_length=2,
        description="Exactly two compact paragraphs for the archetype reaction.",
    )


class EditorialNarrativeSummary(BaseModel):
    """Narrative-first summary format with explicit key points and quotes."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "AI Strategy Shifts from Tools to Operating Model",
                "editorial_narrative": (
                    "Enterprises are no longer treating AI as a pilot project. They are "
                    "restructuring workflows around model-assisted decision loops, with "
                    "procurement and security teams setting constraints early.\n\n"
                    "The article argues that performance gains alone are no longer enough; "
                    "organizations now prioritize reliability, auditability, and predictable "
                    "cost envelopes across teams."
                ),
                "quotes": [
                    {
                        "text": "We can't run five incompatible AI stacks in one company.",
                        "attribution": "Security lead",
                    },
                    {
                        "text": "The biggest shift is governance moving upstream.",
                        "attribution": "Platform engineering manager",
                    },
                ],
                "archetype_reactions": [
                    {
                        "archetype": "Paul Graham",
                        "paragraphs": [
                            (
                                "The interesting part is not the AI itself but the way "
                                "small teams can exploit workflow pain that incumbents "
                                "still treat as a procurement problem."
                            ),
                            (
                                "This kind of shift usually creates startup room around "
                                "better defaults, tighter UX, and direct contact with "
                                "users who feel the operational pain first."
                            ),
                        ],
                    },
                    {
                        "archetype": "Andy Grove",
                        "paragraphs": [
                            (
                                "The real story is a strategic inflection point where "
                                "governance and operating discipline become part of the "
                                "product, not an afterthought."
                            ),
                            (
                                "Leaders should watch the chokepoints: approval latency, "
                                "vendor sprawl, and the cost of running weak controls at "
                                "enterprise scale."
                            ),
                        ],
                    },
                    {
                        "archetype": "Charlie Munger",
                        "paragraphs": [
                            (
                                "What matters most is incentives: budget owners, security "
                                "teams, and workflow operators are now rewarded for "
                                "reliability over novelty."
                            ),
                            (
                                "Once those incentives lock in, the durable winners will "
                                "be the vendors that fit the new control structure rather "
                                "than the ones with the flashiest demo."
                            ),
                        ],
                    },
                ],
                "key_points": [
                    {"point": "Budget owners are pushing for usage transparency by workflow."},
                    {"point": "Security reviews now happen before broad internal rollouts."},
                    {"point": "Tool consolidation is reducing duplicated agent infrastructure."},
                    {"point": "Teams that enforce evaluation gates ship faster over time."},
                ],
                "classification": "to_read",
                "summarization_date": "2026-02-08T10:30:00Z",
            }
        }
    )

    title: str = Field(
        ..., min_length=5, max_length=1000, description="Descriptive title for the content"
    )
    editorial_narrative: str = Field(
        ...,
        min_length=180,
        description="Narrative summary (2-4 information-dense paragraphs).",
    )
    quotes: list[EditorialQuote] = Field(
        ..., min_length=2, max_length=6, description="2-6 notable direct quotes"
    )
    archetype_reactions: list[EditorialArchetypeReaction] = Field(
        default_factory=list,
        max_length=3,
        description="Optional set of three archetype reaction blocks for article/podcast detail.",
    )
    key_points: list[EditorialKeyPoint] = Field(
        ..., min_length=4, max_length=12, description="4-12 concrete key points"
    )
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Content classification: 'to_read' or 'skip'",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_archetype_reactions(self) -> EditorialNarrativeSummary:
        """Allow older payloads while enforcing complete archetype sets when present."""
        if not self.archetype_reactions:
            return self

        required = {"Paul Graham", "Andy Grove", "Charlie Munger"}
        actual = {reaction.archetype for reaction in self.archetype_reactions}
        if actual != required:
            raise ValueError(
                "archetype_reactions must include Paul Graham, Andy Grove, and Charlie Munger"
            )
        return self


class StructuredSummary(BaseModel):
    """Structured summary with bullet points and quotes."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "AI Advances in Natural Language Processing Transform Industry",
                "overview": "Brief overview of the content",
                "bullet_points": [
                    {"text": "Key point 1", "category": "key_finding"},
                    {"text": "Key point 2", "category": "methodology"},
                ],
                "quotes": [{"text": "Notable quote from the content", "context": "Author Name"}],
                "topics": ["AI", "Technology", "Innovation"],
                "questions": [
                    "How might these AI advances impact existing NLP applications?",
                    "What are the potential ethical implications of this technology?",
                ],
                "counter_arguments": [
                    (
                        "Critics argue that the claimed improvements may not generalize "
                        "beyond specific benchmarks"
                    ),
                    "Alternative approaches like symbolic AI might offer more explainability",
                ],
                "summarization_date": "2025-06-14T10:30:00Z",
                "full_markdown": (
                    "# AI Advances in Natural Language Processing\n\n"
                    "Full article content in markdown format..."
                ),
            }
        }
    )

    title: str = Field(
        ..., min_length=5, max_length=1000, description="Descriptive title for the content"
    )
    overview: str = Field(
        ..., min_length=50, description="Brief overview paragraph (longer for podcasts)"
    )
    bullet_points: list[SummaryBulletPoint] = Field(..., min_length=3, max_length=50)
    quotes: list[ContentQuote] = Field(default_factory=list, max_length=50)
    topics: list[str] = Field(default_factory=list, max_length=50)
    questions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Questions to help readers think critically about the content",
    )
    counter_arguments: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Counter-arguments or alternative perspectives to the main claims",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    classification: str = Field(
        default="to_read", description="Content classification: 'to_read' or 'skip'"
    )
    full_markdown: str = Field(
        default="", description="Full article content formatted as clean, readable markdown"
    )


# News digest summary used for fast-scanning feeds


class NewsSummary(BaseModel):
    """Compact summary payload for quick-glance news content."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "additionalProperties": False,
            "example": {
                "title": "Techmeme: OpenAI ships GPT-5 with native agents",
                "article_url": "https://example.com/story",
                "key_points": [
                    "OpenAI launches GPT-5 with native agent orchestration",
                    "Developers get first-party workflows that replace plug-ins",
                    "Initial rollout targets enterprise customers later expanding to prosumers",
                ],
                "summary": (
                    "OpenAI debuts GPT-5 with native multi-agent features and "
                    "enterprise-first rollout."
                ),
                "classification": "to_read",
                "summarization_date": "2025-09-22T10:30:00Z",
            },
        },
    )

    title: str | None = Field(
        None, min_length=5, max_length=240, description="Generated headline for the digest"
    )
    article_url: str | None = Field(
        None,
        min_length=1,
        max_length=2083,
        description="Canonical article URL referenced by the digest",
    )
    key_points: list[str] = Field(
        default_factory=list,
        min_length=0,
        max_length=10,
        description="Headline-ready bullet points summarizing the article",
    )
    summary: str | None = Field(
        None,
        min_length=0,
        max_length=500,
        description="Optional short overview paragraph",
    )
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Read recommendation classification",
    )
    summarization_date: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the digest was generated",
    )


    @field_validator("article_url")
    @classmethod
    def validate_article_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        adapter = TypeAdapter(HttpUrl)
        return str(adapter.validate_python(value))


class DailyNewsRollupSummary(BaseModel):
    """Multi-source daily rollup payload for one user's digest."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "additionalProperties": False,
            "example": {
                "title": "AI tooling, privacy regulation, and fintech deals led the day",
                "summary": (
                    "The day was defined by a mix of AI product launches, growing "
                    "regulatory pressure, and a steady stream of startup financings. "
                    "Infrastructure, payments, and policy stories carried the most "
                    "practical signal."
                ),
                "key_points": [
                    "AI developer tooling and automation launches dominated software news.",
                    "Privacy and child-safety regulation advanced across multiple jurisdictions.",
                    "Payments, commerce, and rural retail funding rounds remained active.",
                ],
            },
        },
    )

    title: str | None = Field(
        None,
        min_length=5,
        max_length=240,
        description="Generated title capturing the day's main themes",
    )
    summary: str | None = Field(
        None,
        min_length=0,
        max_length=1000,
        description="Short overview paragraph summarizing the day as a whole",
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="Variable-length list of distinct major themes or stories from the day",
    )


class NewsArticleMetadata(BaseModel):
    """Details about the linked article for a news item."""

    url: HttpUrl = Field(..., description="Canonical article URL to summarize")
    title: str | None = Field(None, max_length=500)
    source_domain: str | None = Field(None, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        """Normalize noisy titles and enforce max length defensively."""
        if value is None:
            return None
        title = str(value)
        # Drop script/style blocks and strip HTML tags to avoid persisting page markup as title.
        title = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", title)
        title = re.sub(r"(?is)<[^>]+>", " ", title)
        title = unescape(title)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None
        if len(title) > 500:
            return title[:500]
        return title


class NewsAggregatorMetadata(BaseModel):
    """Context about the upstream aggregator (HN, Techmeme, Twitter)."""

    name: str | None = Field(None, max_length=120)
    title: str | None = Field(None, max_length=500)
    external_id: str | None = Field(None, max_length=200)
    author: str | None = Field(None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


SummaryPayload = (
    StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | NewsSummary
)


def _parse_summary_payload(
    summary_kind: str | None,
    summary_version: int | None,
    value: dict[str, Any],
) -> SummaryPayload:
    if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
        if summary_version == SUMMARY_VERSION_V1:
            return InterleavedSummary.model_validate(value)
        if summary_version == SUMMARY_VERSION_V2:
            return InterleavedSummaryV2.model_validate(value)
        raise ValueError(f"Unsupported summary version: {summary_version}")
    if summary_kind == SUMMARY_KIND_LONG_BULLETS:
        if summary_version == SUMMARY_VERSION_V1:
            return BulletedSummary.model_validate(value)
        raise ValueError(f"Unsupported summary version: {summary_version}")
    if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
        if summary_version == SUMMARY_VERSION_V1:
            return EditorialNarrativeSummary.model_validate(value)
        raise ValueError(f"Unsupported summary version: {summary_version}")
    if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
        return StructuredSummary.model_validate(value)
    if summary_kind == SUMMARY_KIND_SHORT_NEWS_DIGEST:
        return NewsSummary.model_validate(value)
    raise ValueError(f"Unsupported summary kind: {summary_kind}")


# Base metadata with source field added
class BaseContentMetadata(BaseModel):
    """Base metadata fields common to all content types."""

    model_config = ConfigDict(extra="allow")

    # NEW: Source field to track content origin
    source: str | None = Field(
        None, description="Source of content (e.g., substack name, podcast name, subreddit name)"
    )

    summary_kind: str | None = Field(
        None,
        description=(
            "Summary discriminator (e.g., long_interleaved, long_structured, short_news_digest)"
        ),
    )
    summary_version: int | None = Field(
        None, ge=1, description="Summary schema version for the current summary_kind"
    )
    summary: SummaryPayload | None = Field(None, description="AI-generated summary payload")
    word_count: int | None = Field(None, ge=0)

    @field_validator("summary", mode="before")
    @classmethod
    def validate_summary(cls, value: SummaryPayload | dict[str, Any] | None, info):
        """Normalize summary payloads into structured models."""
        if value is None or isinstance(
            value,
            (
                StructuredSummary,
                InterleavedSummary,
                InterleavedSummaryV2,
                BulletedSummary,
                EditorialNarrativeSummary,
                NewsSummary,
            ),
        ):
            return value
        if isinstance(value, dict):
            summary_kind = info.data.get("summary_kind")
            summary_version = info.data.get("summary_version")
            if summary_kind and summary_version:
                return _parse_summary_payload(summary_kind, summary_version, value)
            raise ValueError(
                "summary_kind and summary_version are required when summary is present"
            )
        raise ValueError(
            "Summary must be StructuredSummary, InterleavedSummary, InterleavedSummaryV2, "
            "BulletedSummary, EditorialNarrativeSummary, NewsSummary, or dict"
        )


# Article metadata from app/schemas/metadata.py
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

    content: str | None = Field(None, description="Full article text content")

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        """Allow empty string for legacy data but convert to None."""
        if v == "":
            return None
        return v

    author: str | None = Field(None, max_length=200)
    publication_date: datetime | None = None
    content_type: str = Field(default="html", pattern="^(pdf|html|text|markdown|image)$")
    final_url_after_redirects: str | None = Field(None, max_length=2000)


# Podcast metadata from app/schemas/metadata.py
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


class NewsMetadata(BaseContentMetadata):
    """Metadata structure for single-link news content."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "example.com",
                "platform": "hackernews",
                "article": {
                    "url": "https://example.com/story",
                    "title": "Example Story",
                    "source_domain": "example.com",
                },
                "aggregator": {
                    "name": "Hacker News",
                    "external_id": "123",
                    "metadata": {"score": 420},
                },
                "discussion_url": "https://news.ycombinator.com/item?id=123",
                "summary_kind": "short_news_digest",
                "summary_version": 1,
                "summary": {
                    "title": "Techmeme: OpenAI ships GPT-5 with native agents",
                    "article_url": "https://example.com/story",
                    "key_points": [
                        "OpenAI launches GPT-5 with native agent orchestration",
                        "Developers get first-party workflows that replace plug-ins",
                        "Initial rollout targets enterprise customers later expanding to prosumers",
                    ],
                    "summary": (
                        "OpenAI debuts GPT-5 with native multi-agent features and enterprise-first "
                        "rollout."
                    ),
                    "classification": "to_read",
                    "summarization_date": "2025-09-22T10:30:00Z",
                },
            }
        }
    )

    article: NewsArticleMetadata = Field(..., description="Primary article information")
    aggregator: NewsAggregatorMetadata | None = Field(
        None, description="Upstream aggregator context"
    )
    discussion_url: HttpUrl | None = Field(
        None, description="Aggregator discussion link (HN thread, tweet, etc.)"
    )
    discovery_time: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the item was discovered",
    )
    top_comment: dict[str, str] | None = Field(
        None, description="First non-bot discussion comment {author, text} for feed preview"
    )
    comment_count: int | None = Field(
        None, ge=0, description="Discussion comment count denormalized by discussion fetcher"
    )


# Processing result from app/domain/content.py
class ProcessingResult(BaseModel):
    """Result from content processing."""

    success: bool
    content_type: ContentType
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    internal_links: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


# Processing error from app/schemas/metadata.py
class ProcessingError(BaseModel):
    """Error information for failed processing."""

    error: str = Field(..., description="Error message")
    error_type: str = Field(default="unknown", pattern="^(retryable|non_retryable|unknown)$")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ContentData wrapper from app/domain/content.py with enhancements
class ContentData(BaseModel):
    """
    Unified content data model for passing between layers.
    """

    model_config = ConfigDict(
        ignored_types=(property,)
    )

    id: int | None = None
    content_type: ContentType
    url: HttpUrl
    source_url: str | None = None
    title: str | None = None
    status: ContentStatus = ContentStatus.NEW
    metadata: dict[str, Any] = Field(default_factory=dict)

    platform: str | None = Field(default=None, exclude=True)
    source: str | None = Field(default=None, exclude=True)

    # Processing metadata
    error_message: str | None = None
    retry_count: int = 0

    # Timestamps
    created_at: datetime | None = None
    processed_at: datetime | None = None
    publication_date: datetime | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v, info):
        """Ensure metadata matches content type."""
        if info.data:
            content_type = info.data.get("content_type")

            # Clean up empty strings in metadata
            if isinstance(v, dict):
                cleaned_v = {}
                for key, value in v.items():
                    if value == "":
                        cleaned_v[key] = None
                    else:
                        cleaned_v[key] = value
                v = cleaned_v

            if content_type == ContentType.ARTICLE:
                # Validate article metadata
                try:
                    ArticleMetadata(**v)
                except Exception as e:
                    raise ValueError(f"Invalid article metadata: {e}") from e
            elif content_type == ContentType.PODCAST:
                # Validate podcast metadata
                try:
                    PodcastMetadata(**v)
                except Exception as e:
                    raise ValueError(f"Invalid podcast metadata: {e}") from e
            elif content_type == ContentType.NEWS:
                try:
                    return NewsMetadata(**v).model_dump(mode="json", exclude_none=True)
                except Exception as e:
                    raise ValueError(f"Invalid news metadata: {e}") from e
        return v

    def to_article_metadata(self) -> ArticleMetadata:
        """Convert metadata to ArticleMetadata."""
        if self.content_type != ContentType.ARTICLE:
            raise ValueError("Not an article")
        return ArticleMetadata(**self.metadata)

    def to_podcast_metadata(self) -> PodcastMetadata:
        """Convert metadata to PodcastMetadata."""
        if self.content_type != ContentType.PODCAST:
            raise ValueError("Not a podcast")
        return PodcastMetadata(**self.metadata)

    def to_news_metadata(self) -> NewsMetadata:
        """Convert metadata to NewsMetadata."""
        if self.content_type != ContentType.NEWS:
            raise ValueError("Not news content")
        return NewsMetadata(**self.metadata)

    @property
    def summary(self) -> str | None:
        """Get summary text (overview, hook, or plain summary)."""
        summary_data = self.metadata.get("summary")
        if not summary_data:
            if self.content_type == ContentType.NEWS:
                excerpt = self.metadata.get("excerpt")
                if excerpt:
                    return excerpt
            return None
        summary_text = extract_summary_text(summary_data)
        if summary_text:
            return summary_text
        return None

    @property
    def display_title(self) -> str:
        """Get title to display - prefer summary title over content title."""
        summary_data = self.metadata.get("summary")
        if isinstance(summary_data, dict) and summary_data.get("title"):
            return summary_data["title"]
        return self.title or "Untitled"

    @property
    def short_summary(self) -> str | None:
        """Get short version of summary for list view."""
        return extract_short_summary(self.metadata.get("summary"))

    @property
    def structured_summary(self) -> dict[str, Any] | None:
        """Get structured or interleaved summary if available."""
        summary_data = self.metadata.get("summary")
        summary_kind = self.metadata.get("summary_kind")
        if isinstance(summary_data, dict) and summary_kind in {
            SUMMARY_KIND_LONG_STRUCTURED,
            SUMMARY_KIND_LONG_INTERLEAVED,
            SUMMARY_KIND_LONG_BULLETS,
            SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
        }:
            return summary_data
        # Legacy fallback: infer by payload shape
        if isinstance(summary_data, dict) and (
            "bullet_points" in summary_data
            or "insights" in summary_data
            or "editorial_narrative" in summary_data
        ):
            return summary_data
        return None

    @property
    def bullet_points(self) -> list[dict[str, str]]:
        """Get bullet points from structured or interleaved summary.

        For interleaved summaries, converts insights to bullet point format.
        """
        if not self.structured_summary:
            return []

        summary_kind = self.metadata.get("summary_kind")
        summary_version = self.metadata.get("summary_version")

        # Standard structured summary with bullet_points
        if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
            return self.structured_summary.get("bullet_points", [])

        if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
            if summary_version == SUMMARY_VERSION_V2:
                return self.structured_summary.get("key_points", [])
            # Interleaved v1 - convert insights to bullet point format
            insights = self.structured_summary.get("insights", [])
            if insights:
                return [
                    {"text": ins.get("insight", ""), "category": ins.get("topic", "")}
                    for ins in insights
                    if ins.get("insight")
                ]
        if summary_kind == SUMMARY_KIND_LONG_BULLETS:
            points = self.structured_summary.get("points", [])
            if isinstance(points, list):
                return [
                    {"text": point.get("text", ""), "category": "key_point"}
                    for point in points
                    if isinstance(point, dict) and point.get("text")
                ]
        if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
            key_points = self.structured_summary.get("key_points", [])
            if isinstance(key_points, list):
                return [
                    {"text": point.get("point", ""), "category": "key_point"}
                    for point in key_points
                    if isinstance(point, dict) and point.get("point")
                ]

        return []

    @property
    def quotes(self) -> list[dict[str, str]]:
        """Get quotes from structured or interleaved summary.

        For interleaved summaries, extracts supporting quotes from insights.
        """
        if not self.structured_summary:
            return []

        summary_kind = self.metadata.get("summary_kind")
        summary_version = self.metadata.get("summary_version")

        # Standard structured summary with quotes
        if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
            return self.structured_summary.get("quotes", [])

        if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
            if summary_version == SUMMARY_VERSION_V2:
                return self.structured_summary.get("quotes", [])
            # Interleaved v1 - extract supporting quotes from insights
            insights = self.structured_summary.get("insights", [])
            quotes = []
            for ins in insights:
                quote_text = ins.get("supporting_quote")
                if quote_text:
                    quotes.append(
                        {
                            "text": quote_text,
                            "context": ins.get("quote_attribution", ins.get("topic", "")),
                        }
                    )
            return quotes
        if summary_kind == SUMMARY_KIND_LONG_BULLETS:
            points = self.structured_summary.get("points", [])
            if isinstance(points, list):
                flattened: list[dict[str, str]] = []
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    for quote in point.get("quotes", []) or []:
                        if not isinstance(quote, dict):
                            continue
                        text = quote.get("text")
                        if text:
                            flattened.append(
                                {
                                    "text": text,
                                    "context": quote.get("context") or quote.get("attribution", ""),
                                }
                            )
                return flattened
        if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
            raw_quotes = self.structured_summary.get("quotes", [])
            if isinstance(raw_quotes, list):
                return [
                    {
                        "text": quote.get("text", ""),
                        "context": quote.get("attribution", ""),
                    }
                    for quote in raw_quotes
                    if isinstance(quote, dict) and quote.get("text")
                ]

        return []

    @property
    def topics(self) -> list[str]:
        """Get topics from structured or interleaved summary.

        For interleaved summaries, extracts unique topic names from insights.
        """
        if self.structured_summary:
            summary_kind = self.metadata.get("summary_kind")
            summary_version = self.metadata.get("summary_version")

            # Standard topics array
            if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
                return self.structured_summary.get("topics", [])

            if summary_kind == SUMMARY_KIND_LONG_INTERLEAVED:
                if summary_version == SUMMARY_VERSION_V2:
                    topics = self.structured_summary.get("topics", [])
                    if isinstance(topics, list):
                        return [
                            topic.get("topic")
                            for topic in topics
                            if isinstance(topic, dict) and topic.get("topic")
                        ]
                # Interleaved v1 - extract unique topics from insights
                insights = self.structured_summary.get("insights", [])
                if insights:
                    seen = set()
                    topics = []
                    for ins in insights:
                        topic = ins.get("topic")
                        if topic and topic not in seen:
                            seen.add(topic)
                            topics.append(topic)
                    return topics
            if summary_kind == SUMMARY_KIND_LONG_BULLETS:
                return []
            if summary_kind == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE:
                return []

        return self.metadata.get("topics", [])

    @property
    def transcript(self) -> str | None:
        """Get transcript for podcasts."""
        if self.content_type == ContentType.PODCAST:
            return self.metadata.get("transcript")
        return None

    @property
    def source(self) -> str | None:  # noqa: F811
        """Get content source (substack name, podcast name, subreddit)."""
        return self.metadata.get("source")

    @property
    def platform(self) -> str | None:  # noqa: F811
        """Get content platform (twitter, substack, youtube, etc)."""
        return self.metadata.get("platform")

    @property
    def full_markdown(self) -> str | None:
        """Get full article content formatted as markdown from StructuredSummary."""
        summary_data = self.metadata.get("summary")
        if isinstance(summary_data, dict):
            return summary_data.get("full_markdown")
        return None

    def model_dump(self, *args, **kwargs):  # type: ignore[override]
        excludes = kwargs.pop("exclude", set())
        excludes = set(excludes) | {"platform", "source"}
        data = super().model_dump(*args, exclude=excludes, **kwargs)
        metadata = data.get("metadata") or {}
        platform = metadata.get("platform")
        source = metadata.get("source")
        if platform is not None:
            data["platform"] = platform
        if source is not None:
            data["source"] = source
        return data


# Helper functions from app/schemas/metadata.py
def validate_content_metadata(
    content_type: str, metadata: dict
) -> ArticleMetadata | PodcastMetadata | NewsMetadata:
    """
    Validate and parse metadata based on content type.

    Args:
        content_type: Type of content ('article' or 'podcast')
        metadata: Raw metadata dictionary

    Returns:
        Validated metadata model

    Raises:
        ValueError: If content_type is unknown
        ValidationError: If metadata doesn't match schema
    """
    # Remove error fields if present (they should be in separate columns)
    cleaned_metadata = {k: v for k, v in metadata.items() if k not in ["error", "error_type"]}

    if content_type == ContentType.ARTICLE.value:
        return ArticleMetadata(**cleaned_metadata)
    if content_type == ContentType.PODCAST.value:
        return PodcastMetadata(**cleaned_metadata)
    if content_type == ContentType.NEWS.value:
        return NewsMetadata(**cleaned_metadata)
    if content_type == ContentType.UNKNOWN.value:
        # UNKNOWN content uses minimal ArticleMetadata as placeholder
        return ArticleMetadata(**cleaned_metadata)
    raise ValueError(f"Unknown content type: {content_type}")
