"""
AI image generation service using Google Gemini and Runware.

Generates two types of images:
- News thumbnails: Simple 1:1 images using a configured Gemini image model
- Infographics: Complex 16:9 editorial images using a configured provider
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import requests
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig, Part
from PIL import Image

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.metadata import ContentData, ContentType
from app.services.langfuse_tracing import (
    extract_google_usage_details,
    langfuse_generation_context,
)
from app.services.vendor_costs import record_vendor_usage_out_of_band
from app.utils.image_paths import (
    get_content_images_dir,
    get_news_thumbnails_dir,
    get_thumbnails_dir,
)

logger = get_logger(__name__)

DEFAULT_IMAGE_GENERATION_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_RUNWARE_INFOGRAPHIC_MODEL = "runware:101@1"
RUNWARE_API_URL = "https://api.runware.ai/v1"
RUNWARE_INFOGRAPHIC_WIDTH = 1024
RUNWARE_INFOGRAPHIC_HEIGHT = 576
RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT = (
    "readable text, words, letters, numbers, captions, labels, headlines, logos, "
    "watermarks, screenshots, website UI, app interface, chart axes, poster, document "
    "page, printed page, magazine spread, dashboard, phone screen, tablet screen, "
    "desktop monitor, laptop, computer, office workstation"
)
IMAGE_TEXT_DETECTION_MODEL = "gemini-3.1-flash-lite-preview"
RUNWARE_INLINE_RETRY_ATTEMPTS = 2
INFOGRAPHIC_TEXT_RETRY_ATTEMPTS = 1

# Image size settings
INFOGRAPHIC_IMAGE_SIZE = "512"

# Thumbnail settings
THUMBNAIL_SIZE = (200, 200)  # Max dimensions for thumbnails


@dataclass
class ImageGenerationResult:
    """Result from image generation."""

    content_id: int
    image_path: str
    success: bool
    error_message: str | None = None
    thumbnail_path: str | None = None


@dataclass(frozen=True)
class ImageTextCheck:
    """Result from the readable-text quality check."""

    has_readable_text: bool
    reason: str = ""
    confidence: float | None = None


class RunwareGenerationError(RuntimeError):
    """Structured Runware failure that can drive local retries and fallback."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        parameter: str | None = None,
        status_code: int | None = None,
        task_uuid: str | None = None,
        retryable: bool = True,
        fallback_allowed: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.parameter = parameter
        self.status_code = status_code
        self.task_uuid = task_uuid
        self.retryable = retryable
        self.fallback_allowed = fallback_allowed


# ============================================================================
# Information Theory Scoring for News Thumbnails
# ============================================================================


@dataclass
class InterestingScore:
    """Score components for thumbnail interestingness based on information theory."""

    information_density: float = 0.0
    semantic_variety: float = 0.0
    surprise_novelty: float = 0.0
    conceptual_tension: float = 0.0
    abstractness: float = 0.0

    key_concepts: list[str] = field(default_factory=list)
    contrast_pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        """Weighted combination of all metrics (0-100)."""
        return (
            self.information_density * 0.20
            + self.semantic_variety * 0.20
            + self.surprise_novelty * 0.25
            + self.conceptual_tension * 0.20
            + self.abstractness * 0.15
        )


def _analyze_content_interestingness(
    title: str,
    overview: str,
    bullet_points: list[str],
) -> InterestingScore:
    """Analyze content using information theory principles."""
    score = InterestingScore()

    all_text = " ".join([title, overview] + bullet_points)
    words = re.findall(r"\b[a-zA-Z]{3,}\b", all_text.lower())

    word_freq = Counter(words)
    score.key_concepts = [w for w, c in word_freq.most_common(10) if c > 1 or w[0].isupper()]

    # Find contrast pairs
    contrast_markers = [
        ("but", "however"),
        ("despite", "although"),
        ("vs", "versus"),
        ("rise", "fall"),
        ("growth", "decline"),
        ("old", "new"),
        ("past", "future"),
        ("human", "ai"),
    ]
    for pair in contrast_markers:
        if any(p in all_text.lower() for p in pair):
            score.contrast_pairs.append(pair)

    # Information Density
    if len(words) > 0:
        unique_ratio = len(set(words)) / len(words)
        score.information_density = min(100, unique_ratio * 100)

    # Semantic Variety (entropy)
    if word_freq:
        total = sum(word_freq.values())
        entropy = -sum((c / total) * math.log2(c / total) for c in word_freq.values() if c > 0)
        max_entropy = math.log2(len(word_freq)) if len(word_freq) > 1 else 1
        score.semantic_variety = min(100, (entropy / max_entropy) * 100) if max_entropy > 0 else 0

    # Surprise/Novelty
    unusual_indicators = [
        "first",
        "never",
        "breakthrough",
        "unprecedented",
        "shocking",
        "surprising",
        "unexpected",
        "billion",
        "million",
        "record",
    ]
    unusual_count = sum(1 for ind in unusual_indicators if ind in all_text.lower())
    score.surprise_novelty = min(100, unusual_count * 15)

    # Conceptual Tension
    score.conceptual_tension = min(100, len(score.contrast_pairs) * 25)

    # Abstractness
    abstract_words = [
        "technology",
        "future",
        "innovation",
        "change",
        "growth",
        "crisis",
        "opportunity",
        "power",
        "security",
        "privacy",
    ]
    concrete_words = [
        "company",
        "person",
        "product",
        "money",
        "computer",
        "phone",
        "car",
        "market",
    ]
    abstract_count = sum(1 for w in words if w in abstract_words)
    concrete_count = sum(1 for w in words if w in concrete_words)
    if abstract_count + concrete_count > 0:
        score.abstractness = (abstract_count / (abstract_count + concrete_count)) * 100
    else:
        score.abstractness = 50

    return score


def _get_mood_from_score(score: InterestingScore) -> str:
    """Determine mood/tone from score."""
    moods = []
    if score.surprise_novelty > 60:
        moods.append("dramatic")
    if score.conceptual_tension > 50:
        moods.append("thought-provoking")
    if score.abstractness > 60:
        moods.append("futuristic")
    if not moods:
        moods = ["professional", "engaging"]
    return " and ".join(moods[:2])


# ============================================================================
# Prompt Builders
# ============================================================================


def _build_news_thumbnail_prompt(content: ContentData) -> str:
    """Build prompt for subtle news thumbnail."""
    summary = content.metadata.get("summary", {})
    title = summary.get("title") or content.display_title
    overview = (
        summary.get("summary")
        or summary.get("overview")
        or summary.get("hook")
        or summary.get("takeaway")
        or ""
    )

    bullet_points = []
    for bp in (summary.get("key_points") or summary.get("bullet_points", []))[:3]:
        text = bp.get("text") if isinstance(bp, dict) else bp
        if text:
            bullet_points.append(text)
    if not bullet_points:
        for insight in summary.get("insights", [])[:3]:
            if isinstance(insight, dict) and insight.get("insight"):
                bullet_points.append(insight["insight"])

    score = _analyze_content_interestingness(title, overview, bullet_points)

    # Style based on abstractness
    if score.abstractness > 60:
        style_direction = """
- Abstract, conceptual representation
- Simple geometric shapes
- Plenty of negative space
- Minimalist composition"""
    elif score.abstractness > 30:
        style_direction = """
- Stylized, understated illustration
- Simple shapes and forms
- Subtle metaphorical imagery
- Balanced, calm composition"""
    else:
        style_direction = """
- Clean, simple illustration style
- Recognizable subjects, minimal detail
- Quiet visual hierarchy
- Refined editorial aesthetic"""

    tension_instruction = ""
    if score.contrast_pairs:
        tension = score.contrast_pairs[0]
        tension_instruction = f"\n- Visual tension between {tension[0]} and {tension[1]}"

    return f"""Create a subtle editorial thumbnail illustration.

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


def _build_infographic_prompt(content: ContentData) -> str:
    """Build a compact no-text editorial brief for long-form images."""
    summary = content.metadata.get("summary", {})
    title = _clamp_text(str(summary.get("title") or content.display_title).strip(), max_chars=180)
    overview = (
        summary.get("summary")
        or summary.get("overview")
        or summary.get("hook")
        or summary.get("takeaway")
        or ""
    )

    key_points: list[str] = []
    for item in (summary.get("key_points") or summary.get("bullet_points") or [])[:4]:
        if isinstance(item, dict):
            value = item.get("text") or item.get("point") or item.get("insight")
        else:
            value = item
        if not value:
            continue
        cleaned = _clamp_text(" ".join(str(value).split()).strip(), max_chars=220)
        if cleaned:
            key_points.append(cleaned)

    overview_text = _clamp_text(" ".join(str(overview).split()).strip(), max_chars=240)
    if not key_points and overview_text:
        key_points.append(overview_text)

    visual_brief = _build_infographic_visual_brief(
        title=title,
        overview=overview_text,
        key_points=key_points,
    )

    return (
        "Create a premium no-text editorial illustration for Newsly.\n\n"
        "Hard constraints:\n"
        "- No readable text, letters, numbers, labels, captions, logos, or watermarks\n"
        "- No poster layout, newspaper layout, document pages, magazine spreads, "
        "screenshots, dashboards, or UI chrome\n"
        "- 16:9 aspect ratio optimized for mobile display\n"
        "- One dominant visual metaphor or one coherent scene, never a collage\n"
        "- One focal subject with strong negative space and clear "
        "foreground/background separation\n"
        "- Bold, graphic, and immediately legible at thumbnail size\n"
        "- Refined editorial palette with 2 to 4 dominant colors\n\n"
        "Visual brief:\n"
        f"- Story context: {visual_brief['story_context']}\n"
        f"- Primary subject: {visual_brief['primary_subject']}\n"
        f"- Visual metaphor: {visual_brief['visual_metaphor']}\n"
        f"- Scene direction: {visual_brief['scene_direction']}\n"
        f"- Supporting cues: {visual_brief['supporting_cues']}\n\n"
        "Output goal:\n"
        "Create a distinctive editorial image that communicates the story instantly "
        "without rendering any words."
    )


def _build_infographic_visual_brief(
    *,
    title: str,
    overview: str,
    key_points: list[str],
) -> dict[str, str]:
    context_clues = [point for point in key_points[:3] if point]
    if overview:
        context_clues.insert(0, overview)
    clue_text = _clamp_text(" ; ".join(context_clues) or title, max_chars=280)
    subject_seed = key_points[0] if key_points else overview or title
    subject = _clamp_text(subject_seed, max_chars=120)
    if _is_tech_or_ai_story(" ".join([title, overview, *key_points])):
        metaphor = "a tangible system of signals, tools, and pressure rather than a literal UI"
        scene_direction = (
            "an editorial still life or physical scene that implies software, "
            "networks, or automation without screens"
        )
    else:
        metaphor = "a single symbolic scene that turns the story theme into a physical moment"
        scene_direction = (
            "a calm but high-contrast editorial composition with one hero "
            "subject and a few supporting elements"
        )
    return {
        "story_context": clue_text,
        "primary_subject": subject,
        "visual_metaphor": metaphor,
        "scene_direction": scene_direction,
        "supporting_cues": clue_text,
    }


def _tighten_infographic_prompt_for_text_retry(prompt: str, *, reason: str) -> str:
    retry_reason = _clamp_text(
        reason or "readable text was visible in the previous attempt",
        max_chars=160,
    )
    return (
        f"{prompt}\n\n"
        "Regeneration note:\n"
        f"- Previous attempt failed quality review because {retry_reason}\n"
        "- Regenerate with zero readable text anywhere in the image\n"
        "- Avoid posters, signs, documents, labels, captions, book covers, screens, or UI panels\n"
        "- If typography would normally appear in the scene, replace it with "
        "abstract shapes or blank surfaces"
    )


def _clamp_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    truncated = normalized[: max_chars - 1].rstrip(" ,;:-")
    return f"{truncated}…"


def _is_tech_or_ai_story(text: str) -> bool:
    normalized = text.lower()
    keywords = (
        "ai",
        "artificial intelligence",
        "software",
        "automation",
        "agent",
        "agents",
        "tool",
        "tools",
        "mcp",
        "token",
        "tokens",
        "notion",
        "future",
        "workflow",
        "system",
        "factory",
        "compute",
    )
    return any(keyword in normalized for keyword in keywords)


# ============================================================================
# Skip Logic
# ============================================================================


def _should_skip_image_generation(content: ContentData) -> tuple[bool, str]:
    """Check if image generation should be skipped."""
    if content.content_type == ContentType.NEWS:
        return True, "News thumbnails are disabled"

    if not content.metadata.get("summary"):
        return True, "No summary available for prompt generation"

    return False, ""


# ============================================================================
# Image Generation Service
# ============================================================================


class ImageGenerationService:
    """Service for generating images from content summaries."""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.news_thumbnail_models = _resolve_image_models(
            settings.image_generation_model,
            settings.image_generation_fallback_model,
        )
        self.google_infographic_models = _resolve_image_models(
            settings.image_generation_model,
            settings.image_generation_fallback_model,
        )
        self.infographic_provider = settings.infographic_generation_provider
        infographic_primary_model = settings.infographic_generation_model or (
            DEFAULT_RUNWARE_INFOGRAPHIC_MODEL
            if self.infographic_provider == "runware"
            else settings.image_generation_model
        )
        infographic_fallback_model = settings.infographic_generation_fallback_model or (
            None
            if self.infographic_provider == "runware"
            else settings.image_generation_fallback_model
        )
        self.infographic_models = _resolve_image_models(
            infographic_primary_model,
            infographic_fallback_model,
        )
        self.runware_api_key = settings.runware_api_key
        self.google_cloud_project = settings.google_cloud_project
        self.google_cloud_location = settings.google_cloud_location
        self.google_api_key = settings.google_api_key
        self._google_client: genai.Client | None = None
        # Ensure output directories exist
        get_news_thumbnails_dir().mkdir(parents=True, exist_ok=True)
        get_content_images_dir().mkdir(parents=True, exist_ok=True)
        get_thumbnails_dir().mkdir(parents=True, exist_ok=True)

        logger.info(
            "Initialized image generation service "
            "with news_models=%s infographic_provider=%s infographic_models=%s "
            "google_infographic_models=%s size=%s",
            ",".join(self.news_thumbnail_models),
            self.infographic_provider,
            ",".join(self.infographic_models),
            ",".join(self.google_infographic_models),
            INFOGRAPHIC_IMAGE_SIZE,
        )

    def _get_google_client(self) -> genai.Client:
        if self._google_client is not None:
            return self._google_client
        if self.google_cloud_project:
            self._google_client = genai.Client(
                vertexai=True,
                project=self.google_cloud_project,
                location=self.google_cloud_location,
            )
            return self._google_client
        if self.google_api_key:
            self._google_client = genai.Client(vertexai=True, api_key=self.google_api_key)
            return self._google_client
        raise ValueError("Google image generation client is not configured.")
        return self._google_client

    def generate_thumbnail(self, source_path: Path, content_id: int) -> Path | None:
        """Generate a thumbnail from a full-size image using Pillow.

        Args:
            source_path: Path to the full-size image.
            content_id: Content ID for naming the thumbnail.

        Returns:
            Path to the generated thumbnail, or None if generation failed.
        """
        try:
            thumbnail_path = get_thumbnails_dir() / f"{content_id}.png"

            with Image.open(source_path) as img:
                working_img: Image.Image = img
                # Convert to RGB if necessary (for PNG with transparency)
                if working_img.mode in ("RGBA", "P"):
                    working_img = working_img.convert("RGB")

                # Use LANCZOS resampling for high-quality downscaling
                working_img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

                # Save with optimization
                working_img.save(thumbnail_path, "PNG", optimize=True)

            logger.debug(
                "Generated thumbnail for content %s: %s",
                content_id,
                thumbnail_path,
            )
            return thumbnail_path

        except Exception as e:
            logger.warning(
                "Failed to generate thumbnail for content %s: %s",
                content_id,
                e,
                extra={
                    "component": "image_generation",
                    "operation": "generate_thumbnail",
                    "item_id": content_id,
                },
            )
            return None

    def get_image_url(self, content_id: int, content_type: str = "article") -> str | None:
        """Get the URL for a content's image if it exists."""
        if content_type == "news":
            path = get_news_thumbnails_dir() / f"{content_id}.png"
            if path.exists():
                return f"/static/images/news_thumbnails/{content_id}.png"
        else:
            path = get_content_images_dir() / f"{content_id}.png"
            if path.exists():
                return f"/static/images/content/{content_id}.png"
        return None

    def get_thumbnail_url(self, content_id: int) -> str | None:
        """Get the URL for a content's thumbnail if it exists."""
        path = get_thumbnails_dir() / f"{content_id}.png"
        if path.exists():
            return f"/static/images/thumbnails/{content_id}.png"
        return None

    def generate_image(self, content: ContentData) -> ImageGenerationResult:
        """Generate an image for content, dispatching by content type."""
        content_id = content.id or 0

        should_skip, reason = _should_skip_image_generation(content)
        if should_skip:
            logger.info("Skipping image generation for content %s: %s", content_id, reason)
            return ImageGenerationResult(
                content_id=content_id,
                image_path="",
                success=False,
                error_message=f"Skipped: {reason}",
            )

        if content.content_type == ContentType.NEWS:
            return self._generate_news_thumbnail(content)
        else:
            return self._generate_infographic(content)

    def _generate_news_thumbnail(self, content: ContentData) -> ImageGenerationResult:
        """Generate a subtle 1:1 thumbnail for news content."""
        content_id = content.id or 0

        try:
            prompt = _build_news_thumbnail_prompt(content)
            logger.debug("News thumbnail prompt for %s: %s", content_id, prompt[:200])

            response, resolved_model = self._generate_image_response(
                models=self.news_thumbnail_models,
                prompt=prompt,
                image_config=ImageConfig(aspect_ratio="1:1"),
                trace_name="queue.image_generation.news_thumbnail",
                usage_operation="image_generation.news_thumbnail",
                usage_metadata={"image_type": "news_thumbnail"},
                content_id=content_id,
            )

            image_path = get_news_thumbnails_dir() / f"{content_id}.png"
            self._write_generated_image(response, image_path)

            # Generate thumbnail from the full-size image
            thumbnail_path = self.generate_thumbnail(image_path, content_id)

            logger.info(
                "Generated news thumbnail for %s at %s using %s",
                content_id,
                image_path,
                resolved_model,
            )

            return ImageGenerationResult(
                content_id=content_id,
                image_path=str(image_path),
                success=True,
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
            )

        except Exception as e:
            logger.exception(
                "News thumbnail generation failed for %s: %s",
                content_id,
                e,
                extra={
                    "component": "image_generation",
                    "operation": "generate_news_thumbnail",
                    "item_id": content_id,
                },
            )
            return ImageGenerationResult(
                content_id=content_id,
                image_path="",
                success=False,
                error_message=str(e),
            )

    def _generate_infographic(self, content: ContentData) -> ImageGenerationResult:
        """Generate a complex 16:9 infographic for articles/podcasts."""
        content_id = content.id or 0

        try:
            prompt = _build_infographic_prompt(content)
            logger.debug("Infographic prompt for %s: %s", content_id, prompt[:200])

            image_path = get_content_images_dir() / f"{content_id}.png"
            active_prompt = prompt
            provider_used = self.infographic_provider
            resolved_model = ""
            image_bytes = b""

            for quality_attempt in range(INFOGRAPHIC_TEXT_RETRY_ATTEMPTS + 1):
                try:
                    image_bytes, resolved_model = self._generate_infographic_image_bytes(
                        prompt=active_prompt,
                        content_id=content_id,
                        provider=provider_used,
                    )
                except RunwareGenerationError as exc:
                    if not self._can_fallback_to_google(exc):
                        raise
                    logger.warning(
                        "Runware failed for content %s; falling back to Google",
                        content_id,
                        extra={
                            "component": "image_generation",
                            "operation": "generate_infographic",
                            "item_id": content_id,
                            "context_data": {
                                "runware_status_code": exc.status_code,
                                "runware_code": exc.code,
                                "runware_parameter": exc.parameter,
                                "runware_task_uuid": exc.task_uuid,
                            },
                        },
                    )
                    provider_used = "google"
                    image_bytes, resolved_model = self._generate_infographic_image_bytes(
                        prompt=active_prompt,
                        content_id=content_id,
                        provider=provider_used,
                    )

                text_check = self._detect_readable_text_in_image(
                    image_bytes=image_bytes,
                    content_id=content_id,
                    provider=provider_used,
                    provider_model=resolved_model,
                )
                if text_check is None or not text_check.has_readable_text:
                    break
                if quality_attempt >= INFOGRAPHIC_TEXT_RETRY_ATTEMPTS:
                    raise RuntimeError(
                        "Generated image contains readable text; "
                        f"reason={text_check.reason or 'quality check failed'}"
                    )
                logger.warning(
                    "Generated infographic failed readable-text check for content %s; retrying",
                    content_id,
                    extra={
                        "component": "image_generation",
                        "operation": "generate_infographic",
                        "item_id": content_id,
                        "context_data": {
                            "provider": provider_used,
                            "provider_model": resolved_model,
                            "text_check_reason": text_check.reason,
                            "text_check_confidence": text_check.confidence,
                            "quality_attempt": quality_attempt + 1,
                        },
                    },
                )
                active_prompt = _tighten_infographic_prompt_for_text_retry(
                    prompt,
                    reason=text_check.reason,
                )

            image_path.write_bytes(image_bytes)

            # Generate thumbnail from the full-size image
            thumbnail_path = self.generate_thumbnail(image_path, content_id)

            logger.info(
                "Generated infographic for %s at %s using %s via %s",
                content_id,
                image_path,
                resolved_model,
                provider_used,
            )

            return ImageGenerationResult(
                content_id=content_id,
                image_path=str(image_path),
                success=True,
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
            )

        except Exception as e:
            logger.exception(
                "Infographic generation failed for %s: %s",
                content_id,
                e,
                extra={
                    "component": "image_generation",
                    "operation": "generate_infographic",
                    "item_id": content_id,
                },
            )
            return ImageGenerationResult(
                content_id=content_id,
                image_path="",
                success=False,
                error_message=str(e),
            )

    def _generate_infographic_image_bytes(
        self,
        *,
        prompt: str,
        content_id: int,
        provider: str,
    ) -> tuple[bytes, str]:
        if provider == "runware":
            return self._generate_runware_infographic(
                prompt=prompt,
                content_id=content_id,
            )
        return self._generate_google_infographic_bytes(
            prompt=prompt,
            content_id=content_id,
            fallback_from_runware=provider != self.infographic_provider,
        )

    def _generate_google_infographic_bytes(
        self,
        *,
        prompt: str,
        content_id: int,
        fallback_from_runware: bool,
    ) -> tuple[bytes, str]:
        response, resolved_model = self._generate_image_response(
            models=self.google_infographic_models,
            prompt=prompt,
            image_config=ImageConfig(
                aspect_ratio="16:9",
                image_size=INFOGRAPHIC_IMAGE_SIZE,
            ),
            trace_name="queue.image_generation.infographic",
            usage_operation="image_generation.infographic",
            usage_metadata={
                "image_type": "infographic",
                "image_size": INFOGRAPHIC_IMAGE_SIZE,
                "provider": "google",
                "fallback_from_runware": fallback_from_runware,
            },
            content_id=content_id,
        )
        return self._extract_generated_image_bytes(response), resolved_model

    def _generate_image_response(
        self,
        *,
        models: list[str],
        prompt: str,
        image_config: ImageConfig,
        trace_name: str,
        usage_operation: str,
        usage_metadata: dict[str, object],
        content_id: int,
    ) -> tuple[object, str]:
        client = self._get_google_client()
        for index, model in enumerate(models):
            try:
                with langfuse_generation_context(
                    name=trace_name,
                    model=model,
                    input_data=prompt,
                    metadata={"source": "queue", "content_id": content_id, "attempt": index + 1},
                ) as generation:
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            image_config=image_config,
                        ),
                    )
                    usage_details = extract_google_usage_details(response)
                    if generation is not None:
                        generation.update(
                            output="generated_image",
                            usage_details=usage_details,
                        )
                    if usage_details:
                        record_vendor_usage_out_of_band(
                            provider="google",
                            model=model,
                            feature="image_generation",
                            operation=usage_operation,
                            source="queue",
                            usage=cast(dict[str, int | None], usage_details),
                            content_id=content_id,
                            metadata=usage_metadata,
                        )
                return response, model
            except Exception as exc:
                fallback_model = models[index + 1] if index + 1 < len(models) else None
                if fallback_model and _is_model_unavailable_error(exc):
                    logger.warning(
                        "Image generation model %s unavailable for content %s; retrying with %s",
                        model,
                        content_id,
                        fallback_model,
                        extra={
                            "component": "image_generation",
                            "operation": usage_operation,
                            "item_id": content_id,
                            "context_data": {
                                "failed_model": model,
                                "fallback_model": fallback_model,
                            },
                        },
                    )
                    continue
                raise

        raise RuntimeError("No image generation models configured")

    def _generate_runware_infographic(
        self,
        *,
        prompt: str,
        content_id: int,
    ) -> tuple[bytes, str]:
        if not self.runware_api_key:
            raise ValueError("RUNWARE_API_KEY not configured for infographic generation.")

        last_error: RunwareGenerationError | None = None
        for model in self.infographic_models:
            for attempt in range(RUNWARE_INLINE_RETRY_ATTEMPTS):
                task_uuid = str(uuid4())
                try:
                    payload = self._post_runware_inference(
                        prompt=prompt,
                        model=model,
                        task_uuid=task_uuid,
                    )
                    result = self._extract_runware_result(payload, task_uuid=task_uuid)
                    image_url = (
                        result.get("imageURL") or result.get("imageUrl") or result.get("image_url")
                    )
                    if not isinstance(image_url, str) or not image_url:
                        raise RunwareGenerationError(
                            "Runware did not return an image URL.",
                            task_uuid=task_uuid,
                            retryable=False,
                            fallback_allowed=True,
                        )

                    image_bytes = self._download_file(image_url)
                    record_vendor_usage_out_of_band(
                        provider="runware",
                        model=model,
                        feature="image_generation",
                        operation="image_generation.infographic",
                        source="queue",
                        usage={"request_count": 1},
                        content_id=content_id,
                        metadata={
                            "image_type": "infographic",
                            "provider": "runware",
                            "response_cost_usd": result.get("cost"),
                            "image_url": image_url,
                            "task_uuid": task_uuid,
                            "inline_attempt": attempt + 1,
                        },
                    )
                    return image_bytes, model
                except RunwareGenerationError as exc:
                    last_error = exc
                    logger.warning(
                        "Runware infographic attempt failed for content %s",
                        content_id,
                        extra={
                            "component": "image_generation",
                            "operation": "generate_infographic.runware",
                            "item_id": content_id,
                            "context_data": {
                                "model": model,
                                "attempt": attempt + 1,
                                "task_uuid": exc.task_uuid or task_uuid,
                                "status_code": exc.status_code,
                                "code": exc.code,
                                "parameter": exc.parameter,
                                "retryable": exc.retryable,
                                "error_message": str(exc),
                            },
                        },
                    )
                    if exc.retryable and attempt + 1 < RUNWARE_INLINE_RETRY_ATTEMPTS:
                        continue
                    break

        if last_error is not None:
            raise last_error
        raise RuntimeError("No infographic generation models configured.")

    def _post_runware_inference(
        self,
        *,
        prompt: str,
        model: str,
        task_uuid: str,
    ) -> dict[str, Any]:
        try:
            response = requests.post(
                RUNWARE_API_URL,
                headers={
                    "Authorization": f"Bearer {self.runware_api_key}",
                    "Content-Type": "application/json",
                },
                json=[
                    self._build_runware_inference_payload(
                        prompt=prompt,
                        model=model,
                        task_uuid=task_uuid,
                    )
                ],
                timeout=180,
            )
        except requests.RequestException as exc:
            raise RunwareGenerationError(
                f"Runware request failed: {exc}",
                task_uuid=task_uuid,
                retryable=True,
                fallback_allowed=True,
            ) from exc

        try:
            payload = cast(dict[str, Any], response.json())
        except ValueError as exc:
            raise RunwareGenerationError(
                "Runware returned a non-JSON response.",
                status_code=response.status_code,
                task_uuid=task_uuid,
                retryable=response.status_code >= 500,
                fallback_allowed=response.status_code >= 400,
            ) from exc

        errors = payload.get("errors") or []
        if response.status_code >= 400 or errors:
            raise _build_runware_generation_error(
                errors[0] if errors else None,
                status_code=response.status_code,
                task_uuid=task_uuid,
            )

        return payload

    def _build_runware_inference_payload(
        self,
        *,
        prompt: str,
        model: str,
        task_uuid: str,
    ) -> dict[str, object]:
        return {
            "taskType": "imageInference",
            "taskUUID": task_uuid,
            "includeCost": True,
            "outputType": "URL",
            "outputFormat": "PNG",
            "positivePrompt": prompt,
            "negativePrompt": RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT,
            "model": model,
            "numberResults": 1,
            "width": RUNWARE_INFOGRAPHIC_WIDTH,
            "height": RUNWARE_INFOGRAPHIC_HEIGHT,
        }

    def _extract_runware_result(
        self,
        payload: dict[str, Any],
        *,
        task_uuid: str,
    ) -> dict[str, Any]:
        data = payload.get("data") or []
        if not data:
            raise RunwareGenerationError(
                "Runware did not return inference data.",
                task_uuid=task_uuid,
                retryable=False,
                fallback_allowed=True,
            )
        return cast(dict[str, Any], data[0])

    def _can_fallback_to_google(self, exc: RunwareGenerationError) -> bool:
        if not exc.fallback_allowed:
            return False
        return self._google_is_configured()

    def _google_is_configured(self) -> bool:
        return bool(self.google_cloud_project or self.google_api_key)

    def _detect_readable_text_in_image(
        self,
        *,
        image_bytes: bytes,
        content_id: int,
        provider: str,
        provider_model: str,
    ) -> ImageTextCheck | None:
        if not self._google_is_configured():
            return None

        prompt = (
            "Inspect this generated editorial illustration for readable text. "
            "Return JSON with keys has_readable_text (boolean), reason (string), and "
            "confidence (number from 0 to 1). Mark has_readable_text true if you can see "
            "any readable words, letters, numbers, labels, captions, signs, poster text, "
            "document text, or UI text."
        )
        try:
            response = self._get_google_client().models.generate_content(
                model=IMAGE_TEXT_DETECTION_MODEL,
                contents=cast(
                    Any,
                    [Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
                ),
                config=GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    max_output_tokens=200,
                ),
            )
            payload = json.loads(getattr(response, "text", "") or "{}")
            return ImageTextCheck(
                has_readable_text=bool(payload.get("has_readable_text")),
                reason=str(payload.get("reason") or ""),
                confidence=_coerce_optional_float(payload.get("confidence")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Readable-text quality check failed for content %s: %s",
                content_id,
                exc,
                extra={
                    "component": "image_generation",
                    "operation": "detect_generated_text",
                    "item_id": content_id,
                    "context_data": {
                        "provider": provider,
                        "provider_model": provider_model,
                    },
                },
            )
            return None

    def _write_generated_image(self, response: object, image_path: Path) -> None:
        image_path.write_bytes(self._extract_generated_image_bytes(response))

    def _extract_generated_image_bytes(self, response: object) -> bytes:
        candidates = getattr(response, "candidates", None) or []
        if candidates and getattr(candidates[0], "content", None):
            for part in getattr(candidates[0].content, "parts", None) or []:
                inline_data = getattr(part, "inline_data", None)
                mime_type = getattr(inline_data, "mime_type", None)
                if inline_data and mime_type and mime_type.startswith("image/"):
                    image_bytes = getattr(inline_data, "data", None)
                    if image_bytes is None:
                        continue
                    return cast(bytes, image_bytes)

        raise ValueError("No image generated in response")

    def _download_file(self, url: str) -> bytes:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        image_bytes = response.content
        with Image.open(BytesIO(image_bytes)) as img:
            img.verify()
        return image_bytes


def _resolve_image_models(primary_model: str, fallback_model: str | None) -> list[str]:
    models: list[str] = []
    for model in (primary_model, fallback_model):
        normalized = _normalize_model_name(model)
        if normalized and normalized not in models:
            models.append(normalized)

    if not models:
        raise ValueError("At least one image generation model must be configured.")

    return models


def _normalize_model_name(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    return normalized or None


def _build_runware_generation_error(
    error: dict[str, Any] | None,
    *,
    status_code: int | None,
    task_uuid: str,
) -> RunwareGenerationError:
    error = error or {}
    message = str(error.get("message") or "Runware request failed.")
    code = error.get("code")
    parameter = error.get("parameter")
    retryable = bool(
        (status_code or 0) >= 500
        or status_code == 429
        or parameter == "taskUUID"
        or "taskuuid" in message.lower()
    )
    fallback_allowed = bool((status_code or 0) >= 400 or parameter == "taskUUID")
    return RunwareGenerationError(
        f"Runware error: {message}",
        code=str(code) if code is not None else None,
        parameter=str(parameter) if parameter is not None else None,
        status_code=status_code,
        task_uuid=task_uuid,
        retryable=retryable,
        fallback_allowed=fallback_allowed,
    )


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, str)):
            return float(value)
        return None
    except (TypeError, ValueError):
        return None


def _is_model_unavailable_error(exc: Exception) -> bool:
    message = str(exc).upper()
    return "404" in message or "NOT_FOUND" in message or "REQUESTED ENTITY WAS NOT FOUND" in message


# Module-level singleton
_service_instance: ImageGenerationService | None = None


def get_image_generation_service() -> ImageGenerationService:
    """Get or create the ImageGenerationService singleton."""
    global _service_instance
    if _service_instance is None:
        _service_instance = ImageGenerationService()
    return _service_instance
