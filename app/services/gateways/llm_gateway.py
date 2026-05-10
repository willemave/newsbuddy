"""Unified gateway for LLM analysis and summarization calls."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.contracts import ContentType
from app.models.llm.content_analysis import ContentAnalysisOutput
from app.models.metadata.summaries import SummaryPayload
from app.services.content_analyzer import AnalysisError, get_content_analyzer
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer


class LlmGateway:
    """Facade over analyzer and summarizer services."""

    def __init__(self, summarizer: ContentSummarizer | None = None) -> None:
        self._summarizer = summarizer or get_content_summarizer()

    def analyze_url(
        self,
        url: str,
        instruction: str | None = None,
        *,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> ContentAnalysisOutput | AnalysisError:
        """Analyze URL content and optional instruction links."""
        analyzer = get_content_analyzer()
        kwargs: dict[str, Any] = {}
        if instruction is not None:
            kwargs["instruction"] = instruction
        if db is not None:
            kwargs["db"] = db
        if usage_persist is not None:
            kwargs["usage_persist"] = usage_persist
        return analyzer.analyze_url(url, **kwargs)

    def summarize(
        self,
        content: str,
        content_type: str | ContentType,
        *,
        title: str | None = None,
        max_bullet_points: int = 6,
        max_quotes: int = 8,
        content_id: int | str | None = None,
        provider_override: str | None = None,
        model_hint: str | None = None,
        db: Session | None = None,
        usage_persist: dict[str, Any] | None = None,
    ) -> SummaryPayload | None:
        """Summarize content using canonical summarizer policy."""
        kwargs: dict[str, Any] = {
            "content": content,
            "content_type": content_type,
            "title": title,
            "max_bullet_points": max_bullet_points,
            "max_quotes": max_quotes,
            "content_id": content_id,
            "provider_override": provider_override,
            "model_hint": model_hint,
        }
        if db is not None:
            kwargs["db"] = db
        if usage_persist is not None:
            kwargs["usage_persist"] = usage_persist
        return self._summarizer.summarize(**kwargs)


_llm_gateway: LlmGateway | None = None


def get_llm_gateway() -> LlmGateway:
    """Return a cached LLM gateway."""
    global _llm_gateway
    if _llm_gateway is None:
        _llm_gateway = LlmGateway()
    return _llm_gateway
