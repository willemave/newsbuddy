"""Factory helpers for pydantic-ai agents."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from pydantic_ai import Agent

from app.models.metadata import (
    BulletedSummary,
    ContentType,
    DailyNewsRollupSummary,
    EditorialNarrativeSummary,
    InterleavedSummary,
    InterleavedSummaryV2,
    NewsSummary,
    StructuredSummary,
)
from app.services.llm_models import build_pydantic_model

OutputT = TypeVar("OutputT")


def _cached_agent(model_spec: str, output_type: type[Any], system_prompt: str) -> Agent[None, Any]:
    """Build a simple Agent with no dependencies."""
    model, model_settings = build_pydantic_model(model_spec)
    return Agent(
        model,
        deps_type=None,
        output_type=output_type,
        system_prompt=system_prompt,
        model_settings=model_settings,
    )


def get_basic_agent[OutputT](
    model_spec: str, output_type: type[OutputT], system_prompt: str
) -> Agent[None, OutputT]:
    """Return a new agent for an arbitrary task."""
    agent = _cached_agent(model_spec, output_type, system_prompt)
    return cast(Agent[None, OutputT], agent)


def get_summarization_agent(
    model_spec: str,
    content_type: ContentType | str,
    system_prompt: str,
) -> Agent[
    None,
    StructuredSummary
    | InterleavedSummary
    | InterleavedSummaryV2
    | BulletedSummary
    | EditorialNarrativeSummary
    | DailyNewsRollupSummary
    | NewsSummary,
]:
    """Return a summarization agent for the requested content type."""
    ct = content_type
    content_kind = ct.value if isinstance(ct, ContentType) else str(ct)

    summary_type: type[
        StructuredSummary
        | InterleavedSummary
        | InterleavedSummaryV2
        | BulletedSummary
        | EditorialNarrativeSummary
        | DailyNewsRollupSummary
        | NewsSummary
    ]
    if content_kind in {"news", "news_digest"}:
        summary_type = NewsSummary
    elif content_kind == "daily_news_rollup":
        summary_type = DailyNewsRollupSummary
    elif content_kind == "editorial_narrative":
        summary_type = EditorialNarrativeSummary
    elif content_kind in {"long_bullets", "article", "podcast"}:
        summary_type = BulletedSummary
    elif content_kind == "interleaved":
        summary_type = InterleavedSummaryV2
    else:
        summary_type = StructuredSummary

    agent = _cached_agent(model_spec, summary_type, system_prompt)
    return cast(
        Agent[
            None,
            StructuredSummary
            | InterleavedSummary
            | InterleavedSummaryV2
            | BulletedSummary
            | EditorialNarrativeSummary
            | DailyNewsRollupSummary
            | NewsSummary,
        ],
        agent,
    )
