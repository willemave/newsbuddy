#!/usr/bin/env python3
"""
Generate article infographics as SVG images using LLM.

LLMs are good at generating structured markup like SVG.
This approach produces clean vector graphics that scale well.

Usage:
    python scripts/generate_svg_infographics.py --limit 5
    python scripts/generate_svg_infographics.py --content-id 123
"""

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai import Agent
from sqlalchemy import and_

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables before importing app modules
load_dotenv()

from app.core.db import get_db  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.models.metadata import ContentStatus  # noqa: E402
from app.models.schema import Content  # noqa: E402
from app.services.llm_models import build_pydantic_model  # noqa: E402

setup_logging()
logger = get_logger(__name__)

# Default model
DEFAULT_MODEL = "google-gla:gemini-2.5-flash-preview-09-2025"

# Output directory
OUTPUT_DIR = Path("static/images/svg_experiment")
HTML_OUTPUT = OUTPUT_DIR / "gallery.html"


@dataclass
class SvgResult:
    """Result of SVG infographic generation."""

    content_id: int
    title: str
    svg_content: str
    svg_path: str | None = None
    error: str | None = None
    generation_time_ms: int = 0


def build_svg_prompt(title: str, key_points: list[str]) -> str:
    """Build prompt to generate SVG infographic."""
    points_text = "\n".join(f"- {p}" for p in key_points[:3])

    return f"""Create a minimal SVG infographic for this article.

ARTICLE: {title}
KEY POINTS:
{points_text}

Generate a clean, modern SVG that visually represents the core concept.

REQUIREMENTS:
1. Output ONLY valid SVG code, nothing else
2. Use viewBox="0 0 400 225" (16:9 aspect ratio)
3. Dark theme: background #1a1a2e, use bright accent colors
4. Include simple geometric shapes, icons, or diagrams
5. Add 1-2 short text labels (max 3 words each)
6. Keep it minimal and clean - no clutter
7. Use modern design: rounded corners, subtle gradients allowed

STYLE INSPIRATION:
- Flat design icons
- Minimalist infographics
- Tech company presentation graphics

SVG TEMPLATE TO START FROM:
<svg viewBox="0 0 400 225" xmlns="http://www.w3.org/2000/svg">
  <rect width="400" height="225" fill="#1a1a2e"/>
  <!-- Your design here -->
</svg>

Generate the complete SVG now:"""


def extract_svg(text: str) -> str:
    """Extract SVG content from LLM response."""
    # Try to find SVG tags
    svg_match = re.search(r"<svg[^>]*>.*?</svg>", text, re.DOTALL | re.IGNORECASE)
    if svg_match:
        return svg_match.group(0)

    # If wrapped in code blocks, extract
    code_match = re.search(r"```(?:svg|xml)?\s*(.*?)```", text, re.DOTALL)
    if code_match:
        content = code_match.group(1).strip()
        svg_match = re.search(r"<svg[^>]*>.*?</svg>", content, re.DOTALL | re.IGNORECASE)
        if svg_match:
            return svg_match.group(0)
        return content

    return text.strip()


def generate_svg_infographic(
    agent: Agent[None, str],
    content_id: int,
    title: str,
    key_points: list[str],
) -> SvgResult:
    """Generate SVG infographic for an article."""
    start_time = time.time()

    result = SvgResult(
        content_id=content_id,
        title=title,
        svg_content="",
    )

    try:
        prompt = build_svg_prompt(title, key_points)
        response = agent.run_sync(prompt)
        svg_content = extract_svg(response.output)

        # Validate it looks like SVG
        if not svg_content.strip().startswith("<svg"):
            raise ValueError("Response does not contain valid SVG")

        result.svg_content = svg_content

        # Save SVG file
        svg_path = OUTPUT_DIR / f"{content_id}.svg"
        svg_path.write_text(svg_content)
        result.svg_path = str(svg_path)

    except Exception as e:
        result.error = str(e)
        logger.exception("Failed to generate SVG for %s", content_id)

    result.generation_time_ms = int((time.time() - start_time) * 1000)
    return result


def generate_html_gallery(results: list[SvgResult]) -> str:
    """Generate HTML gallery for viewing SVG results."""
    cards_html = ""

    for result in results:
        if result.svg_path:
            svg_name = Path(result.svg_path).name
            # Embed SVG inline for better display
            svg_html = f'<img src="{svg_name}" alt="SVG infographic" class="svg-img">'
        else:
            svg_html = '<div class="no-svg">No SVG generated</div>'

        error_html = f'<div class="error">{result.error}</div>' if result.error else ""

        cards_html += f"""
        <div class="card">
            <div class="svg-container">{svg_html}</div>
            <div class="card-content">
                <div class="title">{result.title[:80]}</div>
                <div class="meta">ID: {result.content_id} | {result.generation_time_ms}ms</div>
                {error_html}
            </div>
        </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SVG Infographic Experiment</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0a0a0a;
            color: #eee;
            padding: 20px;
        }}
        h1 {{ text-align: center; margin-bottom: 10px; color: #60a5fa; }}
        .subtitle {{ text-align: center; color: #888; margin-bottom: 30px; }}
        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
            gap: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }}
        .card {{
            background: #111;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #222;
        }}
        .svg-container {{
            background: #1a1a2e;
            aspect-ratio: 16/9;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .svg-img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        .no-svg {{
            color: #666;
            font-size: 0.9em;
        }}
        .card-content {{
            padding: 15px;
        }}
        .title {{
            font-size: 0.9em;
            color: #60a5fa;
            margin-bottom: 8px;
            line-height: 1.4;
        }}
        .meta {{
            font-size: 0.75em;
            color: #666;
        }}
        .error {{
            color: #f44;
            font-size: 0.8em;
            margin-top: 8px;
        }}
    </style>
</head>
<body>
    <h1>SVG Infographic Experiment</h1>
    <p class="subtitle">LLM-generated vector graphics</p>
    <div class="gallery">
{cards_html}
    </div>
</body>
</html>
"""


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate SVG infographics")
    parser.add_argument("--limit", type=int, default=10, help="Number of articles")
    parser.add_argument("--content-id", type=int, help="Specific content ID")
    parser.add_argument(
        "--types",
        nargs="+",
        choices=["article", "podcast", "news"],
        default=["article"],
        help="Content types (default: article)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )

    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build pydantic-ai model and agent
    model, model_settings = build_pydantic_model(args.model)
    agent: Agent[None, str] = Agent(
        model,
        system_prompt=(
            "You are a graphic designer who creates clean, minimal SVG infographics. "
            "Output only valid SVG code, no explanations."
        ),
        output_type=str,
    )
    print(f"Using model: {args.model}")
    print(f"Output: {OUTPUT_DIR}\n")

    results: list[SvgResult] = []

    with get_db() as db:
        query = db.query(Content).filter(
            and_(
                Content.status == ContentStatus.COMPLETED.value,
                Content.content_type.in_(args.types),
            )
        )

        if args.content_id:
            query = query.filter(Content.id == args.content_id)

        query = query.order_by(Content.created_at.desc())

        if args.limit and not args.content_id:
            query = query.limit(args.limit)

        items = query.all()
        print(f"Found {len(items)} items\n")

        for i, content in enumerate(items):
            metadata = content.content_metadata or {}
            summary = metadata.get("summary", {})

            if not summary:
                print(f"[{i + 1}] Skipping {content.id} (no summary)")
                continue

            title = summary.get("title") or content.title or "Untitled"

            # Extract key points
            key_points = []
            for bp in summary.get("bullet_points", [])[:3]:
                text = bp.get("text") if isinstance(bp, dict) else bp
                if text:
                    key_points.append(text)

            print(f"[{i + 1}/{len(items)}] {title[:60]}...")
            content_id = content.id
            if content_id is None:
                print("    ERROR: missing content id")
                continue

            result = generate_svg_infographic(
                agent=agent,
                content_id=content_id,
                title=title,
                key_points=key_points,
            )

            if result.error:
                print(f"  ERROR: {result.error}")
            else:
                print(f"  Generated in {result.generation_time_ms}ms -> {result.svg_path}")

            results.append(result)
            time.sleep(0.3)

    # Generate gallery
    if results:
        html = generate_html_gallery(results)
        HTML_OUTPUT.write_text(html)
        print(f"\nGallery: {HTML_OUTPUT}")

        success = len([r for r in results if r.svg_path])
        print(f"Generated: {success}/{len(results)}")


if __name__ == "__main__":
    main()
