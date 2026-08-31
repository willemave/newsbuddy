"""Versioned wire contracts for the private document extractor.

These models intentionally contain no Newsly database identifiers or persistence commands. The
Rust caller owns durable state and passes only a public document URL, a bounded extraction intent,
and tracing/deadline metadata.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    TypeAdapter,
)

SCHEMA_VERSION: Literal[1] = 1

RequestId = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
TraceIdentifier = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
BoundedMessage = Annotated[str, StringConstraints(min_length=1, max_length=2_000)]


class WireModel(BaseModel):
    """Strict base model for a language-neutral private wire boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractIntent(StrEnum):
    STATIC_ANALYZE = "static_analyze"
    EXTRACT_ARTICLE = "extract_article"
    RESOLVE_PUBMED = "resolve_pubmed"


class ExtractionProfile(StrEnum):
    AUTOMATIC = "automatic"
    ARTICLE = "article"
    NEWSLETTER = "newsletter"
    SCIENTIFIC = "scientific"


class ExtractionMethod(StrEnum):
    STATIC_READABILITY = "static_readability"
    CRAWL4AI = "crawl4ai"


class FallbackKind(StrEnum):
    FIRECRAWL = "firecrawl"


class ExtractionFailureCode(StrEnum):
    ACCESS_GATE = "access_gate"
    CRAWL_FAILED = "crawl_failed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    FETCH_FAILED = "fetch_failed"
    INTERNAL_ERROR = "internal_error"
    INVALID_URL = "invalid_url"
    NO_CONTENT = "no_content"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class ExtractOptions(WireModel):
    """Bounded policy selectors; arbitrary Crawl4AI configuration is never accepted."""

    profile: ExtractionProfile
    allow_browser_fallback: bool
    discover_feeds: bool
    max_download_bytes: int = Field(ge=65_536, le=10_000_000)
    max_markdown_bytes: int = Field(ge=4_096, le=2_000_000)
    static_minimum_characters: int = Field(ge=100, le=10_000)
    browser_timeout_ms: int = Field(ge=1_000, le=180_000)

    @classmethod
    def defaults(cls) -> ExtractOptions:
        return cls(
            profile=ExtractionProfile.AUTOMATIC,
            allow_browser_fallback=True,
            discover_feeds=True,
            max_download_bytes=5_000_000,
            max_markdown_bytes=1_000_000,
            static_minimum_characters=400,
            browser_timeout_ms=90_000,
        )


class TraceContext(WireModel):
    trace_id: TraceIdentifier | None
    span_id: TraceIdentifier | None


class ExtractRequest(WireModel):
    schema_version: Literal[1]
    request_id: RequestId
    url: HttpUrl
    intent: ExtractIntent
    absolute_deadline: AwareDatetime
    options: ExtractOptions
    trace: TraceContext


class UsageEvent(WireModel):
    kind: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    quantity: int = Field(ge=0, le=100_000_000)
    unit: Annotated[str, StringConstraints(min_length=1, max_length=32)]


class ExtractionTiming(WireModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    milliseconds: int = Field(ge=0, le=3_600_000)


class ExtractionSuccess(WireModel):
    schema_version: Literal[1]
    request_id: RequestId
    kind: Literal["success"]
    final_url: HttpUrl
    title: Annotated[str, StringConstraints(min_length=1, max_length=1_000)]
    author: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None
    published_at: AwareDatetime | None
    markdown: Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
    tables: list[Annotated[str, StringConstraints(min_length=1, max_length=250_000)]] = Field(
        max_length=50
    )
    feed_links: list[HttpUrl] = Field(max_length=50)
    method: ExtractionMethod
    warnings: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(
        max_length=25
    )
    usage_events: list[UsageEvent] = Field(max_length=25)
    timings: list[ExtractionTiming] = Field(max_length=25)


class PubMedDelegation(WireModel):
    schema_version: Literal[1]
    request_id: RequestId
    kind: Literal["delegation"]
    next_url: HttpUrl
    reason: Literal["pubmed_full_text"]
    warnings: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(
        max_length=25
    )
    usage_events: list[UsageEvent] = Field(max_length=25)
    timings: list[ExtractionTiming] = Field(max_length=25)


class ExtractionFallbackRequired(WireModel):
    schema_version: Literal[1]
    request_id: RequestId
    kind: Literal["fallback_required"]
    fallback: FallbackKind
    url: HttpUrl
    reason: BoundedMessage
    retryable: bool
    usage_events: list[UsageEvent] = Field(max_length=25)
    timings: list[ExtractionTiming] = Field(max_length=25)


class ExtractionFailure(WireModel):
    schema_version: Literal[1]
    request_id: RequestId
    kind: Literal["failure"]
    code: ExtractionFailureCode
    retryable: bool
    http_status: int | None = Field(ge=100, le=599)
    message: BoundedMessage
    timings: list[ExtractionTiming] = Field(max_length=25)


ExtractResult = Annotated[
    ExtractionSuccess | PubMedDelegation | ExtractionFallbackRequired | ExtractionFailure,
    Field(discriminator="kind"),
]
EXTRACT_RESULT_ADAPTER: TypeAdapter[ExtractResult] = TypeAdapter(ExtractResult)
