"""Analyze error logs and generate LLM-ready debugging prompts.

This script:
1. Parses all JSONL error logs in logs/errors/
2. Queries database for errored content details
3. Generates structured prompts for LLM-based debugging
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.settings import Settings
from app.models.schema import Content


def parse_jsonl_logs(log_dir: Path) -> list[dict[str, Any]]:
    """Parse all JSONL error logs."""
    errors = []
    for log_file in log_dir.glob("*.jsonl"):
        try:
            with open(log_file) as f:
                for line in f:
                    line = line.strip()
                    if line:  # Skip empty lines
                        try:
                            errors.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            print(f"Warning: Could not parse line in {log_file}: {e}")
        except Exception as e:
            print(f"Warning: Could not read {log_file}: {e}")
    return errors


def extract_content_identifiers(errors: list[dict[str, Any]]) -> set[str]:
    """Extract unique content URLs/IDs from error logs."""
    identifiers = set()
    for error in errors:
        # Try various fields that might contain content identifiers
        if "item_id" in error:
            identifiers.add(str(error["item_id"]))
        if "context_data" in error and isinstance(error["context_data"], dict):
            if "url" in error["context_data"]:
                identifiers.add(error["context_data"]["url"])
            if "content_id" in error["context_data"]:
                identifiers.add(str(error["context_data"]["content_id"]))
    return identifiers


def get_errored_content(db_path: str, identifiers: set[str]) -> list[Content]:
    """Query database for errored content."""
    engine = create_engine(db_path)
    content_list: list[Content] = []

    with Session(engine) as session:
        # Query by URL
        urls = {i for i in identifiers if i.startswith("http")}
        if urls:
            stmt = select(Content).where(Content.url.in_(urls))
            content_list.extend(session.execute(stmt).scalars().all())

        # Query by ID
        ids = {int(i) for i in identifiers if i.isdigit()}
        if ids:
            stmt = select(Content).where(Content.id.in_(ids))
            content_list.extend(session.execute(stmt).scalars().all())

        # Also get all content with error status
        stmt = select(Content).where(Content.status == "error")
        content_list.extend(session.execute(stmt).scalars().all())

    # Deduplicate by ID
    seen = set()
    unique_content = []
    for content in content_list:
        if content.id not in seen:
            seen.add(content.id)
            unique_content.append(content)

    return unique_content


def categorize_errors(errors: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group errors by type/component for better analysis."""
    categorized = defaultdict(list)
    for error in errors:
        component = error.get("component", "unknown")
        operation = error.get("operation", "")
        error_type = error.get("error_type", "")
        key = f"{component}/{operation or error_type}"
        categorized[key].append(error)
    return dict(categorized)


def generate_debug_prompt(
    errors: list[dict[str, Any]],
    categorized_errors: dict[str, list[dict[str, Any]]],
    errored_content: list[Content],
) -> str:
    """Generate a comprehensive debugging prompt for LLM."""

    prompt_parts = []

    # Header
    prompt_parts.append("# Error Analysis and Fix Request\n")
    prompt_parts.append(f"Generated: {datetime.now().isoformat()}\n")
    prompt_parts.append(f"Total Errors: {len(errors)}\n")
    prompt_parts.append(f"Errored Content Items: {len(errored_content)}\n\n")

    # Error Summary by Category
    prompt_parts.append("## Error Summary by Category\n")
    for category, cat_errors in sorted(categorized_errors.items()):
        prompt_parts.append(f"\n### {category} ({len(cat_errors)} occurrences)\n")

        # Sample error for this category
        sample = cat_errors[0]
        prompt_parts.append(f"**Error Type:** {sample.get('error_type', 'Unknown')}\n")
        prompt_parts.append(f"**Error Message:**\n```\n{sample.get('error_message', 'N/A')}\n```\n")

        if "stack_trace" in sample:
            prompt_parts.append(f"**Stack Trace:**\n```python\n{sample['stack_trace']}\n```\n")

        # Context data if available
        if "context_data" in sample and sample["context_data"]:
            prompt_parts.append(
                f"**Context:**\n```json\n{json.dumps(sample['context_data'], indent=2)}\n```\n"
            )

        # Show unique URLs affected
        urls = set()
        for err in cat_errors:
            if (
                "item_id" in err
                and isinstance(err["item_id"], str)
                and err["item_id"].startswith("http")
            ):
                urls.add(err["item_id"])
            if (
                "context_data" in err
                and isinstance(err["context_data"], dict)
                and "url" in err["context_data"]
            ):
                urls.add(err["context_data"]["url"])

        if urls:
            prompt_parts.append(f"**Affected URLs ({len(urls)}):**\n")
            for url in sorted(list(urls)[:5]):  # Show first 5
                prompt_parts.append(f"- {url}\n")
            if len(urls) > 5:
                prompt_parts.append(f"- ... and {len(urls) - 5} more\n")

    # Database Content Details
    if errored_content:
        prompt_parts.append("\n## Errored Content from Database\n")

        for content in errored_content[:10]:  # Show first 10
            prompt_parts.append(f"\n### Content ID: {content.id}\n")
            prompt_parts.append(f"- **URL:** {content.url}\n")
            prompt_parts.append(f"- **Title:** {content.title or 'N/A'}\n")
            prompt_parts.append(f"- **Source:** {content.source}\n")
            prompt_parts.append(f"- **Content Type:** {content.content_type}\n")
            prompt_parts.append(f"- **Status:** {content.status}\n")
            prompt_parts.append(f"- **Retry Count:** {content.retry_count}\n")

            if content.error_message:
                prompt_parts.append(
                    f"- **Error Message:**\n```\n{content.error_message[:500]}\n```\n"
                )

            if content.content_metadata:
                # Show relevant metadata fields
                metadata = content.content_metadata
                relevant_fields = ["summary", "author", "publish_date", "extraction_method"]
                filtered_meta = {k: v for k, v in metadata.items() if k in relevant_fields}
                if filtered_meta:
                    prompt_parts.append(
                        f"- **Metadata:**\n```json\n{json.dumps(filtered_meta, indent=2)}\n```\n"
                    )

        if len(errored_content) > 10:
            prompt_parts.append(
                f"\n... and {len(errored_content) - 10} more errored content items\n"
            )

    # Fix Request
    prompt_parts.append("\n## Fix Request\n")
    prompt_parts.append("Please analyze these errors and:\n")
    prompt_parts.append("1. Identify the root cause(s) of each error category\n")
    prompt_parts.append("2. Suggest code fixes with specific file paths and line numbers\n")
    prompt_parts.append("3. Recommend error handling improvements\n")
    prompt_parts.append(
        "4. Identify any pattern in failing URLs/content that might need special handling\n"
    )
    prompt_parts.append("5. Suggest retry strategies or fallback mechanisms where appropriate\n\n")

    prompt_parts.append("**Key Questions to Answer:**\n")
    prompt_parts.append("- Are these transient errors (network timeouts) or code bugs?\n")
    prompt_parts.append("- Should we add fallback extraction methods?\n")
    prompt_parts.append("- Do we need better timeout/retry configuration?\n")
    prompt_parts.append("- Are certain sources consistently problematic?\n")

    return "".join(prompt_parts)


def main():
    """Main execution."""
    # Setup
    project_root = Path(__file__).parent.parent
    log_dir = project_root / "logs" / "errors"

    if not log_dir.exists():
        print(f"Error: Log directory not found: {log_dir}")
        sys.exit(1)

    # Load settings
    settings = Settings()

    print("📊 Analyzing error logs...\n")

    # Parse logs
    errors = parse_jsonl_logs(log_dir)
    print(f"Found {len(errors)} error entries\n")

    if not errors:
        print("No errors found in logs!")
        return

    # Categorize
    categorized = categorize_errors(errors)
    print(f"Categorized into {len(categorized)} error types:\n")
    for category, cat_errors in sorted(categorized.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  - {category}: {len(cat_errors)} occurrences")
    print()

    # Extract identifiers and query DB
    identifiers = extract_content_identifiers(errors)
    print(f"Extracted {len(identifiers)} unique content identifiers\n")

    print("🗄️  Querying database for errored content...")
    errored_content = get_errored_content(settings.database_url, identifiers)
    print(f"Found {len(errored_content)} errored content items in database\n")

    # Generate prompt
    print("🤖 Generating LLM debugging prompt...\n")
    prompt = generate_debug_prompt(errors, categorized, errored_content)

    # Save to file
    output_file = (
        project_root / "logs" / f"error_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        f.write(prompt)

    print(f"✅ Analysis saved to: {output_file}\n")
    print("=" * 80)
    print("\n" + prompt)


if __name__ == "__main__":
    main()
