"""Tests for source-aware summarization template routing."""

from app.models.contracts import SummaryVersion
from app.services.summarization_templates import (
    resolve_editorial_summary_version,
    resolve_summarization_prompt_route,
)


def test_resolve_summarization_prompt_route_uses_research_template_for_pdf() -> None:
    prompt_type, max_bullets, max_quotes = resolve_summarization_prompt_route(
        "article",
        url="https://example.com/paper.pdf",
        metadata={"content_type": "pdf"},
    )

    assert prompt_type == "editorial_research"
    assert max_bullets == 10
    assert max_quotes == 4


def test_resolve_summarization_prompt_route_uses_github_template_for_repo_urls() -> None:
    prompt_type, _, _ = resolve_summarization_prompt_route(
        "article",
        url="https://github.com/openai/openai-python",
        metadata={},
    )

    assert prompt_type == "editorial_github"


def test_resolve_summarization_prompt_route_uses_substack_template_for_newsletters() -> None:
    prompt_type, _, _ = resolve_summarization_prompt_route(
        "article",
        url="https://importai.substack.com/p/example",
        metadata={"platform": "substack"},
    )

    assert prompt_type == "editorial_substack"


def test_resolve_summarization_prompt_route_uses_twitter_template_for_x_links() -> None:
    prompt_type, max_bullets, max_quotes = resolve_summarization_prompt_route(
        "article",
        url="https://x.com/someone/status/123",
        metadata={"platform": "twitter"},
    )

    assert prompt_type == "editorial_twitter"
    assert max_bullets == 8
    assert max_quotes == 3


def test_resolve_editorial_summary_version_marks_specialized_templates_v2() -> None:
    assert resolve_editorial_summary_version("editorial_narrative") == SummaryVersion.V1.value
    assert resolve_editorial_summary_version("editorial_research") == SummaryVersion.V2.value
