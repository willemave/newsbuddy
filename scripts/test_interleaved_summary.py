#!/usr/bin/env python
"""
Test script for interleaved topics + quotes summary format.

Compares Gemini Flash 3 vs Claude Haiku 4.5 on the last 10 articles.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.core.db import get_session_factory
from app.models.schema import Content
from app.services.llm_models import resolve_model


# New interleaved output format
class InterleavedInsight(BaseModel):
    """A single insight that combines a topic with supporting quote."""

    topic: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="The key topic or theme (2-5 words)",
    )
    insight: str = Field(
        ...,
        min_length=50,
        description="The key insight about this topic (2-3 sentences, be specific)",
    )
    supporting_quote: str | None = Field(
        None,
        min_length=20,
        description=("Full direct quote (20+ words) from the article that supports this insight"),
    )
    quote_attribution: str | None = Field(
        None, description="Who said the quote - author name, speaker, or publication"
    )


class InterleavedSummary(BaseModel):
    """Summary format that interleaves topics with quotes."""

    title: str = Field(..., min_length=10, description="Descriptive title for the content")
    hook: str = Field(
        ...,
        min_length=80,
        description="Opening hook that captures the main takeaway (2-3 sentences)",
    )
    insights: list[InterleavedInsight] = Field(
        ...,
        min_length=5,
        max_length=6,
        description="5-6 key insights with supporting quotes",
    )
    takeaway: str = Field(
        ...,
        min_length=80,
        description="Final takeaway or implication for the reader (2-3 sentences)",
    )
    classification: str = Field(..., description="'to_read' or 'skip'")


INTERLEAVED_SYSTEM_PROMPT = """You are an expert content analyst creating summaries
that weave together key topics with supporting quotes for a cohesive reading experience.

Your task is to create an "interleaved" summary where each insight is paired with a relevant quote
from the content that supports or illustrates it. This creates a more engaging,
evidence-based summary.

Guidelines:
1. Start with a compelling hook that captures the main story (2-3 sentences)
2. Generate 5-6 insights (not fewer). For each insight:
   - Identify a key topic/theme (2-5 words)
   - Write a substantive insight (2-3 sentences minimum, be specific with data/details)
   - Include a FULL direct quote (20+ words) that supports this insight - do not truncate
   - Always note who said the quote when available (author name, publication, speaker)
3. End with a takeaway that tells the reader why this matters to them (2-3 sentences)
4. Classify as "to_read" if substantive, "skip" if promotional/shallow

IMPORTANT:
- Be thorough and detailed - avoid brevity
- Quotes must be substantial (20+ words), not fragments
- Each insight should provide real value, not just restate the topic
- Include specific numbers, names, and data points when available

The goal is to create summaries that feel like a curated narrative rather than
separate bullet lists of topics and quotes."""


@dataclass
class TestResult:
    """Result from a single model test."""

    model_name: str
    article_id: int
    article_title: str
    summary: InterleavedSummary | None
    error: str | None
    duration_ms: int


def get_last_n_articles(n: int = 10) -> list[Content]:
    """Fetch the last N completed articles from the database."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        articles = (
            session.query(Content)
            .filter(Content.content_type == "article")
            .filter(Content.status == "completed")
            .order_by(Content.created_at.desc())
            .limit(n)
            .all()
        )
        # Detach from session
        for article in articles:
            session.expunge(article)
        return articles


def get_article_title(article: Content) -> str:
    """Get the best available title for an article."""
    # Use DB title if it's real
    if article.title and article.title != "Untitled":
        return article.title

    # Fall back to summary title from LLM
    metadata = article.content_metadata or {}
    summary_data = metadata.get("summary", {})
    if isinstance(summary_data, dict):
        summary_title = summary_data.get("title", "")
        if summary_title:
            return summary_title

    return article.title or "Unknown"


def get_article_content(article: Content) -> str:
    """Extract the full article content for summarization."""
    metadata = article.content_metadata or {}

    # Priority 1: content field (usually has most data)
    content = metadata.get("content", "")
    if content and len(content) > 500:
        return content

    # Priority 2: full markdown from existing summary
    summary_data = metadata.get("summary", {})
    if isinstance(summary_data, dict):
        full_md = summary_data.get("full_markdown", "")
        if full_md and len(full_md) > 500:
            return full_md

    # Priority 3: raw content
    raw_content = metadata.get("raw_content", "")
    if raw_content and len(raw_content) > 500:
        return raw_content

    # Return whatever we have, even if short
    return content or (full_md if isinstance(summary_data, dict) else "") or raw_content or ""


def create_agent(model_spec: str) -> Agent[None, InterleavedSummary]:
    """Create a pydantic-ai agent for the interleaved summary."""
    _, resolved_spec = resolve_model(None, model_spec)

    return Agent(
        resolved_spec,
        output_type=InterleavedSummary,
        system_prompt=INTERLEAVED_SYSTEM_PROMPT,
    )


async def run_test(
    agent: Agent[None, InterleavedSummary],
    model_name: str,
    article: Content,
    content: str,
    display_title: str,
) -> TestResult:
    """Run a single test with one model on one article."""
    start = datetime.now()

    try:
        user_msg = f"Title: {display_title}\n\nContent:\n\n{content[:50000]}"
        result = await agent.run(user_msg)
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        article_id = article.id
        if article_id is None:
            raise ValueError("Article missing id")

        return TestResult(
            model_name=model_name,
            article_id=article_id,
            article_title=display_title,
            summary=result.output,
            error=None,
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        article_id = article.id or 0
        return TestResult(
            model_name=model_name,
            article_id=article_id,
            article_title=display_title,
            summary=None,
            error=str(e),
            duration_ms=duration_ms,
        )


def print_result(result: TestResult) -> None:
    """Pretty print a test result."""
    print(f"\n{'=' * 80}")
    print(f"Model: {result.model_name}")
    print(f"Article: [{result.article_id}] {result.article_title[:60]}...")
    print(f"Duration: {result.duration_ms}ms")
    print("-" * 80)

    if result.error:
        print(f"ERROR: {result.error}")
        return

    if not result.summary:
        print("No summary generated")
        return

    s = result.summary
    print(f"\nTITLE: {s.title}")
    print(f"\nHOOK: {s.hook}")
    print(f"\nINSIGHTS ({len(s.insights)}):")

    for i, insight in enumerate(s.insights, 1):
        print(f"\n  {i}. [{insight.topic}]")
        print(f"     {insight.insight}")
        if insight.supporting_quote:
            quote_text = insight.supporting_quote[:150]
            if len(insight.supporting_quote) > 150:
                quote_text += "..."
            print(f'     > "{quote_text}"')
            if insight.quote_attribution:
                print(f"       — {insight.quote_attribution}")

    print(f"\nTAKEAWAY: {s.takeaway}")
    print(f"\nClassification: {s.classification}")


async def main() -> None:
    """Run the interleaved summary experiment."""
    print("=" * 80)
    print("INTERLEAVED SUMMARY EXPERIMENT")
    print("Comparing Gemini Flash 3 vs Claude Haiku 4.5")
    print("=" * 80)

    # Define models to test
    models = {
        "gemini-3-flash-preview": "google-gla:gemini-2.0-flash",
        "haiku-4.5": "anthropic:claude-haiku-4-5-20251001",
    }

    # Fetch articles - get more to ensure we have enough with content
    print("\nFetching last 30 articles (will process up to 10 with content)...")
    articles = get_last_n_articles(30)
    print(f"Found {len(articles)} articles")

    if not articles:
        print("No articles found!")
        return

    # Create agents
    agents = {name: create_agent(spec) for name, spec in models.items()}

    # Run tests
    all_results: list[TestResult] = []
    articles_processed = 0
    max_articles = 10

    for article in articles:
        if articles_processed >= max_articles:
            break

        content = get_article_content(article)
        if not content or len(content) < 500:
            print(f"\nSkipping article {article.id} - insufficient content ({len(content)} chars)")
            continue

        articles_processed += 1
        display_title = get_article_title(article)
        print(f"\n{'=' * 80}")
        print(f"Processing: [{article.id}] {display_title[:60]}...")
        print(f"Content length: {len(content)} chars")

        for model_name, agent in agents.items():
            print(f"  Running {model_name}...", end=" ", flush=True)
            result = await run_test(agent, model_name, article, content, display_title)
            all_results.append(result)
            if result.error:
                print(f"ERROR ({result.duration_ms}ms)")
            else:
                print(f"OK ({result.duration_ms}ms)")

    # Print detailed results
    print("\n\n" + "=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)

    for result in all_results:
        print_result(result)

    # Print comparison summary
    print("\n\n" + "=" * 80)
    print("SUMMARY COMPARISON")
    print("=" * 80)

    for model_name in models:
        model_results = [r for r in all_results if r.model_name == model_name]
        successes = [r for r in model_results if r.summary]
        errors = [r for r in model_results if r.error]
        avg_duration = sum(r.duration_ms for r in successes) / len(successes) if successes else 0

        print(f"\n{model_name}:")
        print(f"  Success: {len(successes)}/{len(model_results)}")
        print(f"  Errors: {len(errors)}")
        print(f"  Avg duration: {avg_duration:.0f}ms")

        successful_summaries = [r.summary for r in successes if r.summary is not None]
        if successful_summaries:
            total_summary_count = len(successful_summaries)
            avg_insights = (
                sum(
                    (len(summary.insights) for summary in successful_summaries),
                    0,
                )
                / total_summary_count
            )
            quotes_present = sum(
                1
                for summary in successful_summaries
                for insight in summary.insights
                if insight.supporting_quote
            )
            total_insights = sum(
                (len(summary.insights) for summary in successful_summaries),
                0,
            )
            print(f"  Avg insights: {avg_insights:.1f}")
            print(f"  Quotes attached: {quotes_present}/{total_insights}")


if __name__ == "__main__":
    asyncio.run(main())
