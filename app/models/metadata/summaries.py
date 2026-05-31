from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

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
    SUMMARY_KIND_LONGFORM_ARTIFACT,
    SUMMARY_KIND_SHORT_NEWS,
    SUMMARY_VERSION_V1,
    SUMMARY_VERSION_V2,
)
from app.models.metadata.longform_artifacts import LongformArtifactEnvelope


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


class GeneratedEditorialKeyPoint(EditorialKeyPoint):
    """Stricter key point entry used for new editorial summary generations."""

    point: str = Field(
        ...,
        min_length=10,
        max_length=180,
        description="Concrete key point, roughly 22 words or less.",
    )


class PodcastSourceDetails(BaseModel):
    """Structured details for podcast summaries."""

    template: Literal["podcast"] = "podcast"
    thesis: str = Field(..., min_length=20, max_length=500)
    speakers: list[str] = Field(..., min_length=1, max_length=6)
    notable_arguments: list[str] = Field(..., min_length=2, max_length=5)
    practical_takeaways: list[str] = Field(..., min_length=2, max_length=5)


class SubstackSourceDetails(BaseModel):
    """Structured details for essay/newsletter summaries."""

    template: Literal["substack"] = "substack"
    thesis: str = Field(..., min_length=20, max_length=500)
    supporting_arguments: list[str] = Field(..., min_length=2, max_length=5)
    evidence: list[str] = Field(..., min_length=1, max_length=5)
    implications: list[str] = Field(..., min_length=1, max_length=5)


class TwitterSourceDetails(BaseModel):
    """Structured details for X/Twitter summaries."""

    template: Literal["twitter"] = "twitter"
    primary_claim: str = Field(..., min_length=15, max_length=400)
    evidence: list[str] = Field(..., min_length=1, max_length=4)
    caveats: list[str] = Field(..., min_length=1, max_length=4)
    linked_context: list[str] = Field(default_factory=list, max_length=4)


class ResearchSourceDetails(BaseModel):
    """Structured details for research article summaries."""

    template: Literal["research"] = "research"
    hypothesis: str = Field(..., min_length=20, max_length=500)
    methods: list[str] = Field(..., min_length=1, max_length=5)
    arguments: list[str] = Field(..., min_length=2, max_length=6)
    limitations: list[str] = Field(..., min_length=1, max_length=5)
    implications: list[str] = Field(..., min_length=1, max_length=5)


class GitHubSourceDetails(BaseModel):
    """Structured details for GitHub repository or docs summaries."""

    template: Literal["github"] = "github"
    overview: str = Field(..., min_length=20, max_length=500)
    architecture: list[str] = Field(..., min_length=1, max_length=5)
    interfaces: list[str] = Field(..., min_length=1, max_length=5)
    setup_constraints: list[str] = Field(..., min_length=1, max_length=5)
    maturity_signals: list[str] = Field(..., min_length=1, max_length=5)
    best_fit_use_cases: list[str] = Field(..., min_length=1, max_length=5)


EditorialSourceDetails = Annotated[
    PodcastSourceDetails
    | SubstackSourceDetails
    | TwitterSourceDetails
    | ResearchSourceDetails
    | GitHubSourceDetails,
    Field(discriminator="template"),
]


class InterestingExternalLink(BaseModel):
    """Curated outbound link from article text worth showing in the reader."""

    url: str = Field(..., max_length=2048)
    title: str | None = Field(None, max_length=300)
    reason: str = Field(..., min_length=5, max_length=300)
    category: Literal[
        "primary_source",
        "research",
        "documentation",
        "tool",
        "dataset",
        "company_product",
        "related_context",
        "other",
    ] = "other"
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("title")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Trim optional text and collapse empty values."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, value: Any) -> Any:
        """Trim required reason text."""
        return value.strip() if isinstance(value, str) else value


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
                "key_points": [
                    {"point": "Budget owners are pushing for usage transparency by workflow."},
                    {"point": "Security reviews now happen before broad internal rollouts."},
                    {"point": "Tool consolidation is reducing duplicated agent infrastructure."},
                    {"point": "Teams that enforce evaluation gates ship faster over time."},
                ],
                "source_details": {
                    "template": "github",
                    "overview": "Open source workflow runtime for long-lived agent tasks.",
                    "architecture": ["Core runtime coordinates task state and plugin execution."],
                    "interfaces": ["CLI entrypoints, local configuration files, and plugin hooks."],
                    "setup_constraints": ["Requires Python 3.11 and local plugin access."],
                    "maturity_signals": ["Active maintenance and concrete production examples."],
                    "best_fit_use_cases": ["Developer workflow automation and local agents."],
                },
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
    key_points: list[EditorialKeyPoint] = Field(
        ..., min_length=4, max_length=12, description="4-12 concrete key points"
    )
    source_details: EditorialSourceDetails | None = Field(
        None,
        description="Optional source-specific structured details for specialized templates",
    )
    classification: str = Field(
        default="to_read",
        pattern="^(to_read|skip)$",
        description="Content classification: 'to_read' or 'skip'",
    )
    summarization_date: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GeneratedEditorialNarrativeSummary(EditorialNarrativeSummary):
    """Strict narrative summary payload used for new LLM generations."""

    title: str = Field(
        ..., min_length=5, max_length=110, description="Descriptive title for the content"
    )
    editorial_narrative: str = Field(
        ...,
        min_length=180,
        max_length=1200,
        description="Compact thesis-led narrative summary, roughly 90-150 words.",
    )
    quotes: list[EditorialQuote] = Field(
        ..., min_length=1, max_length=2, description="1-2 notable direct quotes"
    )
    key_points: list[GeneratedEditorialKeyPoint] = Field(  # type: ignore[assignment]
        ..., min_length=4, max_length=6, description="4-6 concrete key points"
    )


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


# News summary used for fast-scanning feeds

GeneratedNewsKeyPoint = Annotated[
    str,
    Field(
        min_length=1,
        max_length=220,
        description="Concrete news point, usually one complete sentence, <=220 characters.",
    ),
]


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
                    "OpenAI debuts GPT-5 with native multi-agent features for enterprise "
                    "customers. The launch positions agent orchestration as a first-party "
                    "workflow instead of a plug-in layer."
                ),
                "classification": "to_read",
                "summarization_date": "2025-09-22T10:30:00Z",
            },
        },
    )

    title: str | None = Field(None, description="Generated headline for the news item")
    article_url: str | None = Field(
        None,
        min_length=1,
        max_length=2083,
        description="Canonical article URL referenced by the news item",
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
        description="Timestamp when the summary was generated",
    )

    @field_validator("article_url")
    @classmethod
    def validate_article_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        adapter = TypeAdapter(HttpUrl)
        return str(adapter.validate_python(value))


class GeneratedNewsSummary(NewsSummary):
    """Strict news summary payload used for new LLM generations."""

    title: str = Field(
        ...,
        min_length=5,
        max_length=95,
        description="Generated factual headline for the news item, <=95 characters.",
    )
    key_points: list[GeneratedNewsKeyPoint] = Field(
        ...,
        min_length=2,
        max_length=4,
        description="2-4 headline-ready points summarizing the article",
    )
    summary: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Required short overview paragraph, 2-3 natural sentences, <=500 characters.",
    )

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            if not cleaned:
                raise ValueError("summary must not be blank")
            return cleaned
        return value


class DiscussionSummaryTopic(BaseModel):
    """One high-signal theme from a comment discussion."""

    title: str = Field(..., min_length=2, max_length=90)
    summary: str = Field(..., min_length=10, max_length=500)
    stance: str | None = Field(
        None,
        max_length=120,
        description="Optional split of opinion or prevailing sentiment for this theme",
    )


class DiscussionSummaryLink(BaseModel):
    """Interesting link surfaced by a discussion summary."""

    url: str = Field(..., min_length=1, max_length=2048)
    title: str | None = Field(None, max_length=240)
    reason: str | None = Field(None, max_length=300)
    source_comment_id: str | None = Field(None, max_length=120)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        adapter = TypeAdapter(HttpUrl)
        return str(adapter.validate_python(value))


class DiscussionSummaryComment(BaseModel):
    """Representative comment selected by the discussion summarizer."""

    comment_id: str | None = Field(None, max_length=120)
    author: str | None = Field(None, max_length=120)
    text: str = Field(..., min_length=1, max_length=500)
    reason: str | None = Field(None, max_length=220)


class DiscussionSummary(BaseModel):
    """Structured summary of one news item's external discussion."""

    overview: str = Field(
        ...,
        min_length=20,
        max_length=900,
        description="Short synthesis of the most important discussion takeaways",
    )
    topics: list[DiscussionSummaryTopic] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Major themes, disagreements, critiques, or useful context",
    )
    notable_links: list[DiscussionSummaryLink] = Field(
        default_factory=list,
        max_length=10,
        description="Interesting links mentioned by commenters",
    )
    representative_comments: list[DiscussionSummaryComment] = Field(
        default_factory=list,
        max_length=6,
        description="Small set of representative comments with attribution",
    )
    external_discussion_url: str | None = Field(None, max_length=2048)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_discussion_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        links = normalized.get("notable_links")
        if isinstance(links, list):
            normalized["notable_links"] = [
                link for link in links if _is_valid_discussion_link_payload(link)
            ]

        topics = normalized.get("topics")
        if not isinstance(topics, list) or not topics:
            overview = str(normalized.get("overview") or "").strip()
            normalized["topics"] = [
                {
                    "title": "General discussion",
                    "summary": overview
                    if len(overview) >= 10
                    else "Commenters shared limited but relevant discussion context.",
                }
            ]

        return normalized

    @field_validator("external_discussion_url")
    @classmethod
    def validate_external_discussion_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        adapter = TypeAdapter(HttpUrl)
        try:
            return str(adapter.validate_python(value))
        except ValueError:
            return None


def _is_valid_discussion_link_payload(value: Any) -> bool:
    if isinstance(value, DiscussionSummaryLink):
        return True
    if not isinstance(value, dict):
        return False
    url = value.get("url")
    if not isinstance(url, str) or not url.strip():
        return False
    adapter = TypeAdapter(HttpUrl)
    try:
        adapter.validate_python(url.strip())
    except ValueError:
        return False
    return True


SummaryPayload = (
    StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | GeneratedEditorialNarrativeSummary
    | LongformArtifactEnvelope
    | NewsSummary
    | GeneratedNewsSummary
    | DiscussionSummary
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
        if summary_version in {SUMMARY_VERSION_V1, SUMMARY_VERSION_V2}:
            return EditorialNarrativeSummary.model_validate(value)
        raise ValueError(f"Unsupported summary version: {summary_version}")
    if summary_kind == SUMMARY_KIND_LONG_STRUCTURED:
        return StructuredSummary.model_validate(value)
    if summary_kind == SUMMARY_KIND_LONGFORM_ARTIFACT:
        if summary_version == SUMMARY_VERSION_V1:
            return LongformArtifactEnvelope.model_validate(value)
        raise ValueError(f"Unsupported summary version: {summary_version}")
    if summary_kind == SUMMARY_KIND_SHORT_NEWS:
        return NewsSummary.model_validate(value)
    if summary_version == SUMMARY_VERSION_V1 and "summary" in value and "key_points" in value:
        return NewsSummary.model_validate(value)
    raise ValueError(f"Unsupported summary kind: {summary_kind}")


# Base metadata with source field added
