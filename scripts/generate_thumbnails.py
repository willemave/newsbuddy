#!/usr/bin/env python3
"""
Generate interesting thumbnails for news articles using Google Gemini 3.1 Flash Image.

Uses information theory principles to craft prompts that maximize visual interest:
- Information Density: How much meaning is packed per visual element
- Semantic Variety: Diversity of concepts/themes represented
- Surprise/Novelty: Deviation from expected/mundane visuals
- Conceptual Tension: Juxtaposition of contrasting ideas
- Abstractness: Level of conceptual vs literal representation

Usage:
    python scripts/generate_thumbnails.py --limit 100 --dry-run
    python scripts/generate_thumbnails.py --limit 10  # Generate for 10 articles
    python scripts/generate_thumbnails.py --content-id 123  # Single article
"""

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import and_

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables before importing app modules
load_dotenv()

from app.core.db import get_db  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.models.metadata import ContentStatus  # noqa: E402
from app.models.schema import Content  # noqa: E402

setup_logging()
logger = get_logger(__name__)

# Model for image generation
IMAGE_MODEL = "gemini-3.1-flash-image-preview"

# Output directories
OUTPUT_DIR = Path("static/images/thumbnails_experiment")
GAUGE_DIR = OUTPUT_DIR / "gauges"
HTML_OUTPUT = OUTPUT_DIR / "gallery.html"


# ============================================================================
# Information Theory Scoring
# ============================================================================


@dataclass
class InterestingScore:
    """Score components for thumbnail interestingness based on information theory."""

    # Core information theory metrics (0-100 each)
    information_density: float = 0.0  # Meaning per visual element
    semantic_variety: float = 0.0  # Diversity of concepts
    surprise_novelty: float = 0.0  # Deviation from mundane
    conceptual_tension: float = 0.0  # Contrasting ideas juxtaposed
    abstractness: float = 0.0  # Conceptual vs literal

    # Raw analysis data
    key_concepts: list[str] = field(default_factory=list)
    named_entities: list[str] = field(default_factory=list)
    action_words: list[str] = field(default_factory=list)
    contrast_pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        """Weighted combination of all metrics (0-100)."""
        weights = {
            "information_density": 0.20,
            "semantic_variety": 0.20,
            "surprise_novelty": 0.25,  # Highest weight - we want surprising images
            "conceptual_tension": 0.20,
            "abstractness": 0.15,
        }
        return (
            self.information_density * weights["information_density"]
            + self.semantic_variety * weights["semantic_variety"]
            + self.surprise_novelty * weights["surprise_novelty"]
            + self.conceptual_tension * weights["conceptual_tension"]
            + self.abstractness * weights["abstractness"]
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "overall_score": round(self.overall_score, 1),
            "information_density": round(self.information_density, 1),
            "semantic_variety": round(self.semantic_variety, 1),
            "surprise_novelty": round(self.surprise_novelty, 1),
            "conceptual_tension": round(self.conceptual_tension, 1),
            "abstractness": round(self.abstractness, 1),
            "key_concepts": self.key_concepts,
            "named_entities": self.named_entities,
            "action_words": self.action_words,
            "contrast_pairs": self.contrast_pairs,
        }


def analyze_content_interestingness(
    title: str,
    overview: str,
    bullet_points: list[str],
    quotes: list[str],
) -> InterestingScore:
    """
    Analyze content using information theory principles to score interestingness.

    Metrics based on:
    - Shannon entropy: variety of symbols/concepts
    - Kolmogorov complexity: how compressible is the content
    - Surprise: deviation from prior expectations
    """
    score = InterestingScore()

    # Combine all text
    all_text = " ".join([title, overview] + bullet_points + quotes)
    words = re.findall(r"\b[a-zA-Z]{3,}\b", all_text.lower())

    # Extract key concepts (nouns that appear multiple times or are capitalized)
    word_freq = Counter(words)
    score.key_concepts = [w for w, c in word_freq.most_common(10) if c > 1 or w[0].isupper()]

    # Extract named entities (capitalized multi-word phrases)
    entities = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", all_text)
    score.named_entities = list(set(entities))[:5]

    # Extract action words (verbs - simplified heuristic)
    action_patterns = [
        "launches",
        "reveals",
        "announces",
        "introduces",
        "creates",
        "destroys",
        "transforms",
        "disrupts",
        "challenges",
        "dominates",
        "crashes",
        "soars",
        "plunges",
        "explodes",
        "collapses",
        "revolutionizes",
        "threatens",
        "enables",
        "unlocks",
        "breaks",
    ]
    score.action_words = [w for w in words if w in action_patterns]

    # Find contrast pairs (opposites or tensions in the text)
    contrast_markers = [
        ("but", "however"),
        ("despite", "although"),
        ("vs", "versus"),
        ("rise", "fall"),
        ("growth", "decline"),
        ("success", "failure"),
        ("old", "new"),
        ("past", "future"),
        ("human", "ai"),
    ]
    for pair in contrast_markers:
        if any(p in all_text.lower() for p in pair):
            score.contrast_pairs.append(pair)

    # === Calculate Metrics ===

    # 1. Information Density: unique concepts / total words
    if len(words) > 0:
        unique_ratio = len(set(words)) / len(words)
        # Also consider entity density
        entity_density = len(score.named_entities) / max(len(words) / 10, 1)
        score.information_density = min(100, (unique_ratio * 60 + entity_density * 40))

    # 2. Semantic Variety: entropy of word distribution
    if word_freq:
        total = sum(word_freq.values())
        entropy = -sum((c / total) * math.log2(c / total) for c in word_freq.values() if c > 0)
        max_entropy = math.log2(len(word_freq)) if len(word_freq) > 1 else 1
        score.semantic_variety = min(100, (entropy / max_entropy) * 100) if max_entropy > 0 else 0

    # 3. Surprise/Novelty: presence of unusual words, numbers, or dramatic language
    unusual_indicators = [
        "first",
        "never",
        "breakthrough",
        "unprecedented",
        "shocking",
        "surprising",
        "unexpected",
        "secret",
        "exclusive",
        "revolutionary",
        "billion",
        "million",
        "trillion",
        "%",
        "record",
    ]
    unusual_count = sum(1 for ind in unusual_indicators if ind in all_text.lower())
    score.surprise_novelty = min(100, unusual_count * 15 + len(score.action_words) * 10)

    # 4. Conceptual Tension: number of contrast pairs found
    score.conceptual_tension = min(100, len(score.contrast_pairs) * 25)

    # 5. Abstractness: ratio of abstract concepts to concrete nouns
    abstract_words = [
        "technology",
        "future",
        "innovation",
        "change",
        "growth",
        "crisis",
        "opportunity",
        "challenge",
        "vision",
        "strategy",
        "power",
        "control",
        "freedom",
        "security",
        "privacy",
    ]
    concrete_words = [
        "company",
        "person",
        "product",
        "money",
        "building",
        "computer",
        "phone",
        "car",
        "office",
        "market",
    ]
    abstract_count = sum(1 for w in words if w in abstract_words)
    concrete_count = sum(1 for w in words if w in concrete_words)
    if abstract_count + concrete_count > 0:
        score.abstractness = (abstract_count / (abstract_count + concrete_count)) * 100
    else:
        score.abstractness = 50  # Default to middle

    return score


# ============================================================================
# Gauge Image Generation
# ============================================================================


def create_gauge_image(score: float, size: int = 200) -> Image.Image:
    """
    Create a gauge/dial image showing the interestingness score (0-100).

    Uses a semi-circular gauge with color gradient from red (low) to green (high).
    """
    # Create image with transparent background
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Gauge parameters
    center = (size // 2, size // 2 + 20)
    radius = size // 2 - 20
    arc_width = 15

    # Draw background arc (gray)
    for i in range(180):
        angle = math.radians(180 - i)
        x = center[0] + radius * math.cos(angle)
        y = center[1] - radius * math.sin(angle)
        draw.ellipse(
            [x - arc_width // 2, y - arc_width // 2, x + arc_width // 2, y + arc_width // 2],
            fill=(60, 60, 60, 200),
        )

    # Draw colored arc based on score
    score_angle = int((score / 100) * 180)
    for i in range(score_angle):
        angle = math.radians(180 - i)
        x = center[0] + radius * math.cos(angle)
        y = center[1] - radius * math.sin(angle)

        # Color gradient: red -> yellow -> green
        if i < 60:
            r = 255
            g = int((i / 60) * 255)
        elif i < 120:
            r = int(255 - ((i - 60) / 60) * 255)
            g = 255
        else:
            r = 0
            g = 255

        draw.ellipse(
            [x - arc_width // 2, y - arc_width // 2, x + arc_width // 2, y + arc_width // 2],
            fill=(r, g, 100, 255),
        )

    # Draw needle
    needle_angle = math.radians(180 - (score / 100) * 180)
    needle_length = radius - 10
    needle_end = (
        center[0] + needle_length * math.cos(needle_angle),
        center[1] - needle_length * math.sin(needle_angle),
    )
    draw.line([center, needle_end], fill=(255, 255, 255, 255), width=3)

    # Draw center circle
    draw.ellipse(
        [center[0] - 8, center[1] - 8, center[0] + 8, center[1] + 8], fill=(255, 255, 255, 255)
    )

    # Draw score text
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()

    score_text = f"{int(score)}"
    bbox = draw.textbbox((0, 0), score_text, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text(
        (center[0] - text_width // 2, center[1] + 15),
        score_text,
        fill=(255, 255, 255, 255),
        font=font,
    )

    return img


# ============================================================================
# Prompt Engineering for Interesting Thumbnails
# ============================================================================


def build_interesting_prompt(
    title: str,
    overview: str,
    bullet_points: list[str],
    score: InterestingScore,
) -> str:
    """
    Build a prompt optimized for generating visually interesting thumbnails.

    Uses information theory analysis to enhance the prompt.
    """
    # Extract tension if present
    tension = score.contrast_pairs[0] if score.contrast_pairs else None

    # Build visual elements based on abstractness score
    if score.abstractness > 60:
        # Abstract visualization
        style_direction = """
- Abstract, conceptual representation
- Simple geometric shapes
- Plenty of negative space
- Minimalist composition"""
    elif score.abstractness > 30:
        # Semi-abstract
        style_direction = """
- Stylized, understated illustration
- Simple shapes and forms
- Subtle metaphorical imagery
- Balanced, calm composition"""
    else:
        # More literal
        style_direction = """
- Clean, simple illustration style
- Recognizable subjects, minimal detail
- Quiet visual hierarchy
- Refined editorial aesthetic"""

    # Build tension/contrast instructions if present
    tension_instruction = ""
    if tension:
        tension_instruction = f"\n- Visual tension between {tension[0]} and {tension[1]}"

    # Build the prompt
    prompt = f"""Create a striking editorial thumbnail illustration.

CONTENT:
Title: {title}
Summary: {overview[:300] if overview else "N/A"}
Key themes: {", ".join(score.key_concepts[:5])}

VISUAL REQUIREMENTS:
{style_direction}
- No text, logos, or watermarks
- Square 1:1 aspect ratio
- Muted, subtle color palette
- Soft contrast, understated aesthetic
- Clean and minimal{tension_instruction}

MOOD: {_get_mood_from_score(score)}

Create a refined, elegant thumbnail image."""

    return prompt


def _get_mood_from_score(score: InterestingScore) -> str:
    """Determine the mood/tone based on the interestingness score components."""
    moods = []

    if score.surprise_novelty > 60:
        moods.append("dramatic")
    if score.conceptual_tension > 50:
        moods.append("thought-provoking")
    if score.abstractness > 60:
        moods.append("futuristic")
    if score.information_density > 70:
        moods.append("complex")

    if not moods:
        moods = ["professional", "engaging"]

    return " and ".join(moods[:2])


# ============================================================================
# Thumbnail Generation
# ============================================================================


@dataclass
class ThumbnailResult:
    """Result of thumbnail generation for one article."""

    content_id: int
    title: str
    url: str
    score: InterestingScore
    image_path: str | None = None
    gauge_path: str | None = None
    prompt_used: str = ""
    error: str | None = None
    generation_time_ms: int = 0


def generate_thumbnail(
    client: genai.Client,
    content_id: int,
    title: str,
    url: str,
    overview: str,
    bullet_points: list[str],
    quotes: list[str],
) -> ThumbnailResult:
    """Generate a thumbnail for a single article."""
    start_time = time.time()

    # Analyze content interestingness
    score = analyze_content_interestingness(title, overview, bullet_points, quotes)

    result = ThumbnailResult(
        content_id=content_id,
        title=title,
        url=url,
        score=score,
    )

    try:
        # Build optimized prompt
        prompt = build_interesting_prompt(title, overview, bullet_points, score)
        result.prompt_used = prompt

        # Generate image
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=ImageConfig(
                    aspect_ratio="1:1",
                ),
            ),
        )

        # Extract and save image
        image_saved = False
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts or []:
                if (
                    part.inline_data
                    and part.inline_data.mime_type
                    and part.inline_data.mime_type.startswith("image/")
                ):
                    # Save thumbnail
                    image_data = part.inline_data.data
                    if image_data is None:
                        continue
                    image_path = OUTPUT_DIR / f"{content_id}_thumb.png"
                    image_path.write_bytes(image_data)
                    result.image_path = str(image_path)
                    image_saved = True
                    break

        if not image_saved:
            result.error = "No image in API response"
            return result

        # Generate gauge image
        gauge_img = create_gauge_image(score.overall_score)
        gauge_path = GAUGE_DIR / f"{content_id}_gauge.png"
        gauge_img.save(gauge_path, "PNG")
        result.gauge_path = str(gauge_path)

    except Exception as e:
        result.error = str(e)
        logger.exception("Failed to generate thumbnail for content %s", content_id)

    result.generation_time_ms = int((time.time() - start_time) * 1000)
    return result


# ============================================================================
# HTML Gallery Generation
# ============================================================================


def generate_html_gallery(results: list[ThumbnailResult]) -> str:
    """Generate an HTML gallery to view all thumbnails."""
    # Sort by overall score descending
    sorted_results = sorted(results, key=lambda r: r.score.overall_score, reverse=True)

    # Calculate stats
    total = len(results)
    success = len([r for r in results if r.image_path])
    avg_score = sum(r.score.overall_score for r in results) / len(results) if results else 0
    high_score = len([r for r in results if r.score.overall_score >= 70])

    # Build HTML using f-string (CSS braces work fine in f-strings)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Thumbnail Gallery - Interestingness Analysis</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
        }}
        h1 {{ text-align: center; margin-bottom: 10px; color: #fff; }}
        .subtitle {{ text-align: center; color: #888; margin-bottom: 30px; }}
        .stats {{
            display: flex; justify-content: center; gap: 40px;
            margin-bottom: 30px; flex-wrap: wrap;
        }}
        .stat {{ text-align: center; }}
        .stat-value {{ font-size: 2em; font-weight: bold; color: #4ade80; }}
        .stat-label {{ color: #888; font-size: 0.9em; }}
        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px; max-width: 1800px; margin: 0 auto;
        }}
        .card {{
            background: #16213e; border-radius: 12px; overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3); transition: transform 0.2s;
        }}
        .card:hover {{ transform: translateY(-5px); }}
        .card.error {{ border: 2px solid #ef4444; }}
        .thumbnail-container {{
            position: relative; aspect-ratio: 16/9; background: #0f0f23;
        }}
        .thumbnail {{ width: 100%; height: 100%; object-fit: cover; }}
        .gauge-overlay {{
            position: absolute; top: 10px; right: 10px; width: 80px; height: 80px;
        }}
        .score-badge {{
            position: absolute; top: 10px; left: 10px;
            background: rgba(0,0,0,0.7); padding: 5px 12px;
            border-radius: 20px; font-weight: bold;
        }}
        .score-high {{ color: #4ade80; }}
        .score-medium {{ color: #fbbf24; }}
        .score-low {{ color: #ef4444; }}
        .card-content {{ padding: 15px; }}
        .title {{
            font-size: 1em; font-weight: 600; margin-bottom: 10px;
            line-height: 1.4; display: -webkit-box;
            -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
        }}
        .title a {{ color: #fff; text-decoration: none; }}
        .title a:hover {{ color: #60a5fa; }}
        .metrics {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }}
        .metric {{
            background: #1e3a5f; padding: 3px 8px;
            border-radius: 4px; font-size: 0.75em;
        }}
        .metric-label {{ color: #888; }}
        .metric-value {{ color: #60a5fa; font-weight: 500; }}
        .concepts {{ font-size: 0.8em; color: #888; }}
        .prompt-toggle {{
            margin-top: 10px; padding: 5px 10px; background: #0f3460;
            border: none; color: #60a5fa; border-radius: 4px;
            cursor: pointer; font-size: 0.8em;
        }}
        .prompt {{
            display: none; margin-top: 10px; padding: 10px;
            background: #0a0a1a; border-radius: 6px; font-size: 0.75em;
            white-space: pre-wrap; color: #aaa; max-height: 200px; overflow-y: auto;
        }}
        .prompt.show {{ display: block; }}
        .error-msg {{ color: #ef4444; font-size: 0.85em; margin-top: 5px; }}
        .no-image {{
            display: flex; align-items: center; justify-content: center;
            height: 100%; color: #666; font-size: 0.9em;
        }}
        .generation-time {{ font-size: 0.75em; color: #666; margin-top: 5px; }}
    </style>
</head>
<body>
    <h1>Thumbnail Gallery</h1>
    <p class="subtitle">Information Theory-Based Interestingness Analysis</p>

    <div class="stats">
        <div class="stat">
            <div class="stat-value">{total}</div>
            <div class="stat-label">Total Articles</div>
        </div>
        <div class="stat">
            <div class="stat-value">{success}</div>
            <div class="stat-label">Generated</div>
        </div>
        <div class="stat">
            <div class="stat-value">{avg_score:.1f}</div>
            <div class="stat-label">Avg Score</div>
        </div>
        <div class="stat">
            <div class="stat-value">{high_score}</div>
            <div class="stat-label">High Score (70+)</div>
        </div>
    </div>

    <div class="gallery">
"""

    for result in sorted_results:
        score = result.score.overall_score
        if score >= 70:
            score_class = "score-high"
        elif score >= 40:
            score_class = "score-medium"
        else:
            score_class = "score-low"

        # Thumbnail image or placeholder
        if result.image_path:
            img_name = Path(result.image_path).name
            thumb_html = f'<img src="{img_name}" class="thumbnail" loading="lazy">'
        else:
            thumb_html = '<div class="no-image">No image generated</div>'

        # Gauge overlay
        gauge_html = ""
        if result.gauge_path:
            gauge_html = f'<img src="gauges/{Path(result.gauge_path).name}" class="gauge-overlay">'

        # Metrics
        s = result.score
        metric_tpl = (
            '<div class="metric">'
            '<span class="metric-label">{}</span> '
            '<span class="metric-value">{:.0f}</span>'
            "</div>"
        )
        metrics_html = "\n".join(
            [
                metric_tpl.format("Density:", s.information_density),
                metric_tpl.format("Variety:", s.semantic_variety),
                metric_tpl.format("Surprise:", s.surprise_novelty),
                metric_tpl.format("Tension:", s.conceptual_tension),
                metric_tpl.format("Abstract:", s.abstractness),
            ]
        )

        # Concepts
        concepts_html = ", ".join(s.key_concepts[:5]) if s.key_concepts else "N/A"

        # Error message
        error_html = f'<div class="error-msg">{result.error}</div>' if result.error else ""

        # Escape prompt for HTML
        prompt_escaped = (
            result.prompt_used.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

        card_class = "card error" if result.error else "card"

        # Build card HTML in parts to avoid long lines
        gen_time = result.generation_time_ms
        cid = result.content_id
        title_link = f'<a href="{result.url}" target="_blank">{result.title}</a>'
        toggle_js = "this.nextElementSibling.classList.toggle('show')"

        html += f"""
            <div class="{card_class}">
                <div class="thumbnail-container">
                    {thumb_html}
                    {gauge_html}
                    <div class="score-badge {score_class}">{score:.0f}</div>
                </div>
                <div class="card-content">
                    <div class="title">{title_link}</div>
                    <div class="metrics">{metrics_html}</div>
                    <div class="concepts">Concepts: {concepts_html}</div>
                    {error_html}
                    <div class="generation-time">Generated in {gen_time}ms | ID: {cid}</div>
                    <button class="prompt-toggle" onclick="{toggle_js}">
                        Show Prompt
                    </button>
                    <div class="prompt">{prompt_escaped}</div>
                </div>
            </div>
        """

    html += """
    </div>
    <script>
        document.addEventListener('keydown', function(e) {
            if (e.key === 'ArrowRight') {
                window.scrollBy({ top: 400, behavior: 'smooth' });
            } else if (e.key === 'ArrowLeft') {
                window.scrollBy({ top: -400, behavior: 'smooth' });
            }
        });
    </script>
</body>
</html>
"""

    return html


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate interesting thumbnails for news articles"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Number of articles to process (default: 100)",
    )
    parser.add_argument(
        "--content-id",
        type=int,
        help="Generate for a specific content ID only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze articles without generating images",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        choices=["article", "podcast", "news"],
        default=["news", "article"],
        help="Content types to process (default: news, article)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip articles that already have generated thumbnails",
    )

    args = parser.parse_args()

    # Ensure output directories exist
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GAUGE_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize Gemini client
    client = None
    if not args.dry_run:
        settings = get_settings()
        if settings.google_cloud_project:
            client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
        else:
            if not settings.google_api_key:
                raise ValueError("GOOGLE_API_KEY not configured for Vertex image generation.")
            client = genai.Client(vertexai=True, api_key=settings.google_api_key)
        print(f"Using model: {IMAGE_MODEL}")

    print(f"Output directory: {OUTPUT_DIR}")
    print()

    # Query database for content
    results: list[ThumbnailResult] = []

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

        content_items = query.all()
        print(f"Found {len(content_items)} content items to process")
        print()

        for i, content in enumerate(content_items):
            content_id = content.id
            content_url = content.url
            if content_id is None or not content_url:
                print(f"[{i + 1}/{len(content_items)}] Skipping content with missing id/url")
                continue

            # Skip if already exists and flag is set
            if args.skip_existing and (OUTPUT_DIR / f"{content_id}_thumb.png").exists():
                print(f"[{i + 1}/{len(content_items)}] Skipping {content_id} (exists)")
                continue

            # Extract content data
            metadata = content.content_metadata or {}
            summary = metadata.get("summary", {})

            if not summary:
                print(f"[{i + 1}/{len(content_items)}] Skipping {content.id} (no summary)")
                continue

            title = summary.get("title") or content.title or "Untitled"
            overview = summary.get("overview", "")
            bullet_points = []
            for bp in summary.get("bullet_points", []):
                text = bp.get("text") if isinstance(bp, dict) else bp
                if text:
                    bullet_points.append(text)
            quotes = []
            for q in summary.get("quotes", []):
                text = q.get("text") if isinstance(q, dict) else q
                if text:
                    quotes.append(text)

            print(f"[{i + 1}/{len(content_items)}] Processing: {title[:60]}...")

            if args.dry_run:
                # Just analyze without generating
                score = analyze_content_interestingness(title, overview, bullet_points, quotes)
                result = ThumbnailResult(
                    content_id=content_id,
                    title=title,
                    url=content_url,
                    score=score,
                )
                print(
                    f"    Score: {score.overall_score:.1f} "
                    f"(density={score.information_density:.0f}, "
                    f"variety={score.semantic_variety:.0f}, "
                    f"surprise={score.surprise_novelty:.0f}, "
                    f"tension={score.conceptual_tension:.0f}, "
                    f"abstract={score.abstractness:.0f})"
                )
            else:
                if client is None:
                    raise RuntimeError("Gemini client not initialized")
                result = generate_thumbnail(
                    client=client,
                    content_id=content_id,
                    title=title,
                    url=content_url,
                    overview=overview,
                    bullet_points=bullet_points,
                    quotes=quotes,
                )
                if result.error:
                    print(f"    ERROR: {result.error}")
                else:
                    print(
                        f"    Score: {result.score.overall_score:.1f} | "
                        f"Time: {result.generation_time_ms}ms | "
                        f"Image: {result.image_path}"
                    )

            results.append(result)

            # Small delay to avoid rate limiting
            if not args.dry_run and i < len(content_items) - 1:
                time.sleep(0.5)

    # Generate HTML gallery
    if results:
        html = generate_html_gallery(results)
        HTML_OUTPUT.write_text(html)
        print(f"\nGenerated gallery: {HTML_OUTPUT}")

        # Save results as JSON for analysis
        json_output = OUTPUT_DIR / "results.json"
        json_data = [
            {
                "content_id": r.content_id,
                "title": r.title,
                "url": r.url,
                "score": r.score.to_dict(),
                "image_path": r.image_path,
                "error": r.error,
                "generation_time_ms": r.generation_time_ms,
            }
            for r in results
        ]
        json_output.write_text(json.dumps(json_data, indent=2))
        print(f"Saved results JSON: {json_output}")

        # Print summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Total processed: {len(results)}")
        print(f"Successfully generated: {len([r for r in results if r.image_path])}")
        print(f"Errors: {len([r for r in results if r.error])}")
        avg_score = sum(r.score.overall_score for r in results) / len(results)
        print(f"Average interestingness score: {avg_score:.1f}")

        # Top 5 by score
        print("\nTop 5 by Interestingness Score:")
        for r in sorted(results, key=lambda x: x.score.overall_score, reverse=True)[:5]:
            print(f"  {r.score.overall_score:.1f} - {r.title[:50]}...")


if __name__ == "__main__":
    main()
