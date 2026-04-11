#!/usr/bin/env python3
"""
Generate article infographics using ASCII art rendered as images.

Approach:
1. Use OpenAI gpt-5.4-mini to generate ASCII art representing the article
2. Render the ASCII art as a styled image using PIL

This is dramatically cheaper than image generation models.

Usage:
    python scripts/generate_ascii_infographics.py --limit 5
    python scripts/generate_ascii_infographics.py --content-id 123
    python scripts/generate_ascii_infographics.py --dry-run  # Show ASCII only
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
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

# Default model (can be overridden with --model flag)
DEFAULT_MODEL = "openai:gpt-5.4-mini"
FALLBACK_MODEL = "google-gla:gemini-2.5-flash-preview-09-2025"

# Output directory
OUTPUT_DIR = Path("static/images/ascii_experiment")
HTML_OUTPUT = OUTPUT_DIR / "gallery.html"


@dataclass
class AsciiResult:
    """Result of ASCII infographic generation."""

    content_id: int
    title: str
    ascii_text: str
    image_path: str | None = None
    error: str | None = None
    generation_time_ms: int = 0


def build_ascii_prompt(title: str, key_points: list[str], quotes: list[str]) -> str:
    """Build prompt to generate ASCII art infographic."""
    points_text = "\n".join(f"- {p}" for p in key_points[:3])

    # Using raw string to avoid escape sequence warnings in ASCII art examples
    return rf"""Create ASCII ART that visually represents this news article's core concept.

ARTICLE TOPIC: {title}
KEY POINTS:
{points_text}

YOUR TASK: Draw an ASCII art illustration - NOT text descriptions.
Create a VISUAL PICTURE using ASCII characters that captures the essence of this story.

STRICT RULES:
1. Draw actual ASCII ART - shapes, objects, scenes, symbols
2. Use these characters: / \ | - _ = + * # @ . : ; ' " ^ ~ < > ( ) [ ] {{ }}
3. Maximum 14 lines, 44 characters wide
4. NO sentences or paragraphs - only visual art with minimal labels (1-3 words max)
5. Be creative - draw metaphors, not literal descriptions

GOOD EXAMPLES OF ASCII ART:

Tech/AI topic:
    .---.
   /     \\
  | () () |    NEURAL
  |   ^   |    NET
   \\ === /
    '---'
  /|||||\\

Money/Finance topic:
     $$$
    $   $
   $  $  $     MARKET
    $   $      RISE
     $$$
      |
   __|__

Cloud/Data topic:
    .---.
   (     )
  (       )   DATA
   (     )    FLOW
    '---'
      |
   [_____]

Growth topic:
         *
        /|\\
       / | \\
      /  |  \\    UP
     /   |   \\
    /____|____\\

Now create ASCII art for the article above. Output ONLY the ASCII art, nothing else:"""


def render_ascii_to_image(
    ascii_text: str,
    width: int = 800,
    height: int = 450,
    bg_color: tuple = (20, 20, 35),
    text_color: tuple = (0, 255, 150),
    font_size: int = 18,
) -> Image.Image:
    """Render ASCII text as a styled terminal-like image."""
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to use a monospace font
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
    font_paths = [
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Courier.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except OSError:
                continue

    if font is None:
        font = ImageFont.load_default()

    # Add subtle scanline effect
    for y in range(0, height, 4):
        draw.line([(0, y), (width, y)], fill=(15, 15, 30), width=1)

    # Add subtle border/glow
    border_color = (0, 100, 80)
    draw.rectangle([5, 5, width - 6, height - 6], outline=border_color, width=2)

    # Center the text block
    lines = ascii_text.strip().split("\n")

    # Calculate text dimensions
    line_height = font_size + 4
    total_text_height = len(lines) * line_height
    start_y = (height - total_text_height) // 2

    # Find max line width for centering
    max_line_width = 0.0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        max_line_width = max(max_line_width, float(line_width))

    start_x = int((width - max_line_width) // 2)

    # Draw each line
    for i, line in enumerate(lines):
        y = start_y + i * line_height

        # Slight color variation for visual interest
        if "+" in line or "-" in line or "|" in line:
            color = (0, 200, 120)  # Border chars slightly different
        elif line.strip().startswith("*") or line.strip().startswith("-"):
            color = (100, 255, 180)  # Bullets brighter
        else:
            color = text_color

        draw.text((start_x, y), line, fill=color, font=font)

    # Add "terminal" decorations
    draw.text((15, 10), "● ● ●", fill=(80, 80, 80), font=font)

    return img


def generate_ascii_infographic(
    agent: Agent[None, str],
    content_id: int,
    title: str,
    key_points: list[str],
    quotes: list[str],
) -> AsciiResult:
    """Generate ASCII art infographic for an article."""
    start_time = time.time()

    result = AsciiResult(
        content_id=content_id,
        title=title,
        ascii_text="",
    )

    try:
        # Build prompt and generate ASCII art
        prompt = build_ascii_prompt(title, key_points, quotes)

        response = agent.run_sync(prompt)
        ascii_text = response.output.strip()

        # Clean up any markdown code blocks
        if "```" in ascii_text:
            # Extract content between code blocks
            lines = ascii_text.split("\n")
            cleaned = []
            in_block = False
            for line in lines:
                if "```" in line:
                    in_block = not in_block
                    continue
                if in_block or not line.startswith("```"):
                    cleaned.append(line)
            ascii_text = "\n".join(cleaned)

        result.ascii_text = ascii_text

        # Render to image
        img = render_ascii_to_image(ascii_text)
        image_path = OUTPUT_DIR / f"{content_id}_ascii.png"
        img.save(image_path, "PNG")
        result.image_path = str(image_path)

    except Exception as e:
        result.error = str(e)
        logger.exception("Failed to generate ASCII infographic for %s", content_id)

    result.generation_time_ms = int((time.time() - start_time) * 1000)
    return result


def generate_html_gallery(results: list[AsciiResult]) -> str:
    """Generate HTML gallery for viewing results."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ASCII Infographic Experiment</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0a0a0a;
            color: #eee;
            padding: 20px;
        }
        h1 { text-align: center; margin-bottom: 10px; color: #0f8; }
        .subtitle { text-align: center; color: #888; margin-bottom: 30px; }
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
            gap: 20px;
            max-width: 1600px;
            margin: 0 auto;
        }
        .card {
            background: #111;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #222;
        }
        .card img {
            width: 100%;
            height: auto;
            display: block;
        }
        .card-content {
            padding: 15px;
        }
        .title {
            font-size: 0.9em;
            color: #0f8;
            margin-bottom: 10px;
            line-height: 1.4;
        }
        .meta {
            font-size: 0.75em;
            color: #666;
        }
        .ascii-toggle {
            margin-top: 10px;
            padding: 5px 10px;
            background: #1a1a1a;
            border: 1px solid #333;
            color: #0f8;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.8em;
        }
        .ascii-raw {
            display: none;
            margin-top: 10px;
            padding: 10px;
            background: #0a0a0a;
            border-radius: 4px;
            font-family: monospace;
            font-size: 0.7em;
            white-space: pre;
            color: #0f8;
            overflow-x: auto;
        }
        .ascii-raw.show { display: block; }
        .error { color: #f44; }
    </style>
    <script>
        function toggleAscii(btn) { btn.nextElementSibling.classList.toggle('show'); }
    </script>
</head>
<body>
    <h1>ASCII Infographic Experiment</h1>
    <p class="subtitle">LLM-generated ASCII text rendered as images</p>
    <div class="gallery">
"""

    for result in results:
        if result.image_path:
            img_name = Path(result.image_path).name
            img_html = f'<img src="{img_name}" alt="ASCII infographic">'
        else:
            img_html = '<div style="padding: 20px; color: #666;">No image</div>'

        error_html = f'<div class="error">{result.error}</div>' if result.error else ""

        ascii_escaped = (
            result.ascii_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

        html += f"""
        <div class="card">
            {img_html}
            <div class="card-content">
                <div class="title">{result.title[:80]}</div>
                <div class="meta">ID: {result.content_id} | {result.generation_time_ms}ms</div>
                {error_html}
                <button class="ascii-toggle" onclick="toggleAscii(this)">Show ASCII</button>
                <div class="ascii-raw">{ascii_escaped}</div>
            </div>
        </div>
"""

    html += """
    </div>
</body>
</html>
"""
    return html


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate ASCII infographics")
    parser.add_argument("--limit", type=int, default=10, help="Number of articles")
    parser.add_argument("--content-id", type=int, help="Specific content ID")
    parser.add_argument("--dry-run", action="store_true", help="Print ASCII only")
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
        help=f"Model to use (default: {DEFAULT_MODEL}, fallback: {FALLBACK_MODEL})",
    )

    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build pydantic-ai model and agent
    model_spec = args.model
    model, model_settings = build_pydantic_model(model_spec)
    agent: Agent[None, str] = Agent(
        model,
        system_prompt=(
            "You are an ASCII artist. You create visual ASCII art illustrations, "
            "not text descriptions. Output only ASCII art, no explanations."
        ),
        output_type=str,
    )
    print(f"Using model: {model_spec}")
    print(f"Output: {OUTPUT_DIR}\n")

    results: list[AsciiResult] = []

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
            for bp in summary.get("bullet_points", [])[:4]:
                text = bp.get("text") if isinstance(bp, dict) else bp
                if text:
                    key_points.append(text)

            # Extract quotes
            quotes = []
            for q in summary.get("quotes", [])[:2]:
                text = q.get("text") if isinstance(q, dict) else q
                if text:
                    quotes.append(text)

            print(f"[{i + 1}/{len(items)}] {title[:60]}...")
            content_id = content.id
            if content_id is None:
                print("    ERROR: missing content id")
                continue

            result = generate_ascii_infographic(
                agent=agent,
                content_id=content_id,
                title=title,
                key_points=key_points,
                quotes=quotes,
            )

            if args.dry_run or result.ascii_text:
                print("\n" + result.ascii_text + "\n")

            if result.error:
                print(f"  ERROR: {result.error}")
            else:
                print(f"  Generated in {result.generation_time_ms}ms")

            results.append(result)
            time.sleep(0.3)  # Rate limiting

    # Generate gallery
    if results:
        html = generate_html_gallery(results)
        HTML_OUTPUT.write_text(html)
        print(f"\nGallery: {HTML_OUTPUT}")

        success = len([r for r in results if r.image_path])
        print(f"Generated: {success}/{len(results)}")


if __name__ == "__main__":
    main()
