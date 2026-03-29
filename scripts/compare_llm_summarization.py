#!/usr/bin/env python3
"""
Compare summarization quality between OpenAI GPT-5 mini and Anthropic Haiku 4.5.

This script:
1. Fetches 10 random articles/podcasts from the database
2. Runs summarization with both models
3. Compares and displays the results
"""

import json
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.anthropic_llm import AnthropicSummarizationService

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.openai_llm import OpenAISummarizationService

logger = get_logger(__name__)
console = Console()


def get_random_content_items(session: Session, count: int = 10) -> list[dict[str, Any]]:
    """Fetch random articles and podcasts with content from database."""
    console.print(f"[cyan]Fetching {count} random content items from database...[/cyan]")

    # Query for completed articles and podcasts that have content
    query = sa.text("""
        SELECT
            id,
            content_type,
            url,
            title,
            status,
            content_metadata,
            created_at
        FROM contents
        WHERE status = 'completed'
            AND content_type IN ('article', 'podcast')
            AND json_extract(content_metadata, '$.content') IS NOT NULL
            AND json_extract(content_metadata, '$.summary') IS NOT NULL
        ORDER BY RANDOM()
        LIMIT :count
    """)

    result = session.execute(query, {"count": count})
    rows = result.fetchall()

    items = []
    for row in rows:
        items.append(
            {
                "id": row[0],
                "content_type": row[1],
                "url": row[2],
                "title": row[3],
                "status": row[4],
                "content_metadata": json.loads(row[5]) if isinstance(row[5], str) else row[5],
                "created_at": row[6],
            }
        )

    console.print(f"[green]Found {len(items)} content items[/green]")
    return items


def extract_content_text(item: dict[str, Any]) -> str | None:
    """Extract the actual content text from metadata."""
    metadata = item["content_metadata"]
    content_type = item["content_type"]

    if content_type == "article":
        return metadata.get("content")
    elif content_type == "podcast":
        return metadata.get("transcript")

    return None


def compare_summaries(
    item: dict[str, Any],
    openai_summary: Any,
    anthropic_summary: Any,
) -> None:
    """Display comparison of summaries from both models."""
    content_type = item["content_type"]
    title = item["title"] or "Untitled"

    console.print("\n" + "=" * 100)
    console.print(
        Panel(f"[bold cyan]{title}[/bold cyan]\n[dim]{content_type} | ID: {item['id']}[/dim]")
    )

    # Create comparison table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Aspect", style="cyan", width=20)
    table.add_column("OpenAI GPT-5 mini", style="green", width=38)
    table.add_column("Anthropic Haiku 4.5", style="yellow", width=38)

    # Compare titles
    openai_title = openai_summary.title if openai_summary else "N/A"
    anthropic_title = anthropic_summary.title if anthropic_summary else "N/A"
    table.add_row("Title", openai_title[:100], anthropic_title[:100])

    # Compare overviews
    openai_overview = openai_summary.overview if openai_summary else "N/A"
    anthropic_overview = anthropic_summary.overview if anthropic_summary else "N/A"
    table.add_row(
        "Overview",
        openai_overview[:200] + "..." if len(openai_overview) > 200 else openai_overview,
        anthropic_overview[:200] + "..." if len(anthropic_overview) > 200 else anthropic_overview,
    )

    # Compare bullet point counts
    openai_bp_count = len(openai_summary.bullet_points) if openai_summary else 0
    anthropic_bp_count = len(anthropic_summary.bullet_points) if anthropic_summary else 0
    table.add_row("Bullet Points", str(openai_bp_count), str(anthropic_bp_count))

    # Compare quote counts
    openai_quote_count = len(openai_summary.quotes) if openai_summary else 0
    anthropic_quote_count = len(anthropic_summary.quotes) if anthropic_summary else 0
    table.add_row("Quotes", str(openai_quote_count), str(anthropic_quote_count))

    # Compare topic counts
    openai_topic_count = len(openai_summary.topics) if openai_summary else 0
    anthropic_topic_count = len(anthropic_summary.topics) if anthropic_summary else 0
    table.add_row("Topics", str(openai_topic_count), str(anthropic_topic_count))

    # Compare classifications
    openai_classification = openai_summary.classification if openai_summary else "N/A"
    anthropic_classification = anthropic_summary.classification if anthropic_summary else "N/A"
    table.add_row("Classification", openai_classification, anthropic_classification)

    console.print(table)

    # Show first bullet point from each
    if openai_summary and openai_summary.bullet_points:
        console.print("\n[green]OpenAI First Bullet:[/green]")
        console.print(f"  • {openai_summary.bullet_points[0].text}")

    if anthropic_summary and anthropic_summary.bullet_points:
        console.print("\n[yellow]Anthropic First Bullet:[/yellow]")
        console.print(f"  • {anthropic_summary.bullet_points[0].text}")


def main():
    """Main comparison script."""
    console.print(
        Panel.fit(
            "[bold cyan]LLM Summarization Comparison[/bold cyan]\n"
            "OpenAI GPT-5 mini vs Anthropic Haiku 4.5",
            border_style="cyan",
        )
    )

    # Get settings and database connection
    settings = get_settings()
    engine = create_engine(str(settings.database_url))

    # Initialize both services
    console.print("\n[cyan]Initializing LLM services...[/cyan]")
    try:
        openai_service = OpenAISummarizationService()
        console.print("[green]✓ OpenAI service initialized[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to initialize OpenAI service: {e}[/red]")
        return

    try:
        anthropic_service = AnthropicSummarizationService()
        console.print("[green]✓ Anthropic service initialized[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to initialize Anthropic service: {e}[/red]")
        return

    # Fetch random content
    with Session(engine) as session:
        items = get_random_content_items(session, count=10)

    if not items:
        console.print("[red]No content items found in database![/red]")
        return

    # Process each item
    results = []
    for idx, item in enumerate(items, 1):
        console.print(f"\n[bold]Processing item {idx}/{len(items)}...[/bold]")

        content_text = extract_content_text(item)
        if not content_text:
            console.print(f"[yellow]⚠ No content text found for {item['title']}, skipping[/yellow]")
            continue

        content_type = item["content_type"]

        # Summarize with OpenAI
        console.print("[cyan]  → Running OpenAI summarization...[/cyan]")
        try:
            openai_summary = openai_service.summarize(
                content=content_text,
                content_type=content_type,
            )
        except Exception as e:
            console.print(f"[red]  ✗ OpenAI failed: {e}[/red]")
            openai_summary = None

        # Summarize with Anthropic
        console.print("[cyan]  → Running Anthropic summarization...[/cyan]")
        try:
            anthropic_summary = anthropic_service.summarize(
                content=content_text,
                content_type=content_type,
            )
        except Exception as e:
            console.print(f"[red]  ✗ Anthropic failed: {e}[/red]")
            anthropic_summary = None

        # Store and display results
        result = {
            "item": item,
            "openai_summary": openai_summary,
            "anthropic_summary": anthropic_summary,
        }
        results.append(result)

        compare_summaries(item, openai_summary, anthropic_summary)

    # Summary statistics
    console.print("\n\n" + "=" * 100)
    console.print(Panel.fit("[bold cyan]Summary Statistics[/bold cyan]", border_style="cyan"))

    stats_table = Table(show_header=True, header_style="bold magenta")
    stats_table.add_column("Model", style="cyan")
    stats_table.add_column("Successful", style="green")
    stats_table.add_column("Failed", style="red")
    stats_table.add_column("Avg Bullets", style="yellow")
    stats_table.add_column("Avg Quotes", style="yellow")

    openai_successful = sum(1 for r in results if r["openai_summary"] is not None)
    openai_failed = len(results) - openai_successful
    openai_avg_bullets = (
        sum(len(r["openai_summary"].bullet_points) for r in results if r["openai_summary"])
        / openai_successful
        if openai_successful > 0
        else 0
    )
    openai_avg_quotes = (
        sum(len(r["openai_summary"].quotes) for r in results if r["openai_summary"])
        / openai_successful
        if openai_successful > 0
        else 0
    )

    anthropic_successful = sum(1 for r in results if r["anthropic_summary"] is not None)
    anthropic_failed = len(results) - anthropic_successful
    anthropic_avg_bullets = (
        sum(len(r["anthropic_summary"].bullet_points) for r in results if r["anthropic_summary"])
        / anthropic_successful
        if anthropic_successful > 0
        else 0
    )
    anthropic_avg_quotes = (
        sum(len(r["anthropic_summary"].quotes) for r in results if r["anthropic_summary"])
        / anthropic_successful
        if anthropic_successful > 0
        else 0
    )

    stats_table.add_row(
        "OpenAI GPT-5 mini",
        str(openai_successful),
        str(openai_failed),
        f"{openai_avg_bullets:.1f}",
        f"{openai_avg_quotes:.1f}",
    )
    stats_table.add_row(
        "Anthropic Haiku 4.5",
        str(anthropic_successful),
        str(anthropic_failed),
        f"{anthropic_avg_bullets:.1f}",
        f"{anthropic_avg_quotes:.1f}",
    )

    console.print(stats_table)
    console.print("\n[bold green]Comparison complete![/bold green]")


if __name__ == "__main__":
    main()
