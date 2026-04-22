"""One-shot prototype runner for the insight report service.

Generates a report for a single user and prints the output as JSON plus a
rendered markdown version. Use this to iterate on prompts and output shape
before wiring the feature through the task queue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import setup_logging
from app.services.insight_report import InsightReport, generate_insight_report


def _render_markdown(report: InsightReport) -> str:
    lines = [f"# {report.title}"]
    if report.subtitle:
        lines.append(f"_{report.subtitle}_")
    lines.append("")
    lines.append(report.intro)
    lines.append("")

    if report.themes:
        lines.append("## Themes")
        for theme in report.themes:
            lines.append(f"- {theme}")
        lines.append("")

    lines.append("## Insights")
    for item in report.insights:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Learnings")
    for item in report.learnings:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Curiosities")
    for item in report.curiosities:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Dig Deeper")
    for area in report.dig_deeper_areas:
        lines.append(f"### {area.title}")
        lines.append(f"> {area.prompt}")
        lines.append("")

    if report.referenced_knowledge_ids:
        lines.append("## Referenced knowledge (content ids)")
        lines.append(", ".join(f"#{cid}" for cid in report.referenced_knowledge_ids))

    return "\n".join(lines)


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Generate a prototype insight report")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Synthesis model spec (e.g. 'anthropic:claude-opus-4-7'). Defaults to service default."
        ),
    )
    parser.add_argument(
        "--effort",
        default=None,
        choices=["low", "medium", "high", "max"],
        help=(
            "Reasoning effort. Maps to anthropic_effort / "
            "openai_reasoning_effort / google thinking_level."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./.local_dumps/insight_reports"),
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with get_db() as db:
        kwargs = {"user_id": args.user_id}
        if args.model:
            kwargs["synthesis_model"] = args.model
        if args.effort:
            kwargs["effort"] = args.effort
        report = generate_insight_report(db, **kwargs)

    from datetime import datetime

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    model_tag = (args.model or "default").replace(":", "_").replace("/", "_")
    effort_tag = f"_effort-{args.effort}" if args.effort else ""
    json_path = args.output_dir / f"user{args.user_id}_{model_tag}{effort_tag}_{stamp}.json"
    md_path = args.output_dir / f"user{args.user_id}_{model_tag}{effort_tag}_{stamp}.md"

    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    md_path.write_text(_render_markdown(report))

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print()
    print("=" * 80)
    print(_render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
