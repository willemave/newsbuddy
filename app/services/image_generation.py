"""
AI image generation service using Google Gemini and Runware.

Generates two types of images:
- News thumbnails: Simple 1:1 images using a configured Gemini image model
- Infographics: Complex 16:9 editorial images using a configured provider
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import cast

import requests
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig
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
    "desktop monitor, laptop, computer, office workstation, car, vehicle, factory machine"
)

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
    """Build prompt for no-text editorial infographic explainer."""
    summary = content.metadata.get("summary", {})
    title = str(summary.get("title") or content.display_title).strip()
    overview = (
        summary.get("summary")
        or summary.get("overview")
        or summary.get("hook")
        or summary.get("takeaway")
        or ""
    )
    overview_text = " ".join(str(overview).split()).strip()

    key_points: list[str] = []
    for item in (summary.get("key_points") or summary.get("bullet_points") or [])[:4]:
        if isinstance(item, dict):
            value = item.get("text") or item.get("point") or item.get("insight")
        else:
            value = item
        if not value:
            continue
        cleaned = " ".join(str(value).split()).strip()
        if cleaned:
            key_points.append(cleaned)

    if not key_points and overview_text:
        key_points.append(overview_text[:240])

    story_lines = "\n".join(f"- {point}" for point in key_points) or "- Use the title as context."
    tech_story = _is_tech_or_ai_story(" ".join([title, overview_text, *key_points]))
    tech_instruction = (
        "- For AI, software, or automation stories, lean slightly near-future and systems-"
        "oriented, but explain the story through physical artifacts and spatial flow rather "
        "than screens.\n"
        if tech_story
        else ""
    )

    return (
        "Create a no-text editorial infographic that explains the article content through "
        "image alone.\n\n"
        "Visual requirements:\n"
        "- Modern, clean editorial illustration style.\n"
        "- 16:9 aspect ratio optimized for mobile display.\n"
        "- No readable text, letters, labels, captions, logos, or watermarks.\n"
        "- No screenshots, app interfaces, dashboards, or literal UI.\n"
        "- Use connected artifacts, shelves, packages, books, envelopes, sketch tools, "
        "tokens, plinths, and symbolic objects.\n"
        "- Make the story understandable at a glance through clear hierarchy, grouping, "
        "connectors, and cause-and-effect flow.\n"
        "- Keep it information-dense but organized, with 3 to 5 major elements.\n"
        "- Prefer editorial object systems and cutaway structure over generic office scenes.\n"
        f"{tech_instruction}"
        "- The story context below is reference only and must not appear as rendered words "
        "in the image.\n\n"
        "Preferred composition:\n"
        "- Explain the article as a no-text process chain using editorial objects only.\n"
        "- Show how one object leads to the next through left-to-right or circular flow.\n"
        "- Avoid machines, vehicles, and generic factory imagery.\n\n"
        f"Story title: {title}\n"
        f"Overview: {overview_text or 'N/A'}\n"
        "Key facts to encode visually:\n"
        f"{story_lines}\n"
    )


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
        self.news_thumbnail_models = _resolve_image_models(
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
            "with news_models=%s infographic_provider=%s infographic_models=%s size=%s",
            ",".join(self.news_thumbnail_models),
            self.infographic_provider,
            ",".join(self.infographic_models),
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
            if self.infographic_provider == "runware":
                image_bytes, resolved_model = self._generate_runware_infographic(
                    prompt=prompt,
                    content_id=content_id,
                )
                image_path.write_bytes(image_bytes)
            else:
                response, resolved_model = self._generate_image_response(
                    models=self.infographic_models,
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
                    },
                    content_id=content_id,
                )
                self._write_generated_image(response, image_path)

            # Generate thumbnail from the full-size image
            thumbnail_path = self.generate_thumbnail(image_path, content_id)

            logger.info(
                "Generated infographic for %s at %s using %s",
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

        for model in self.infographic_models:
            response = requests.post(
                RUNWARE_API_URL,
                headers={
                    "Authorization": f"Bearer {self.runware_api_key}",
                    "Content-Type": "application/json",
                },
                json=[
                    {
                        "taskType": "imageInference",
                        "taskUUID": f"newsly-infographic-{content_id}",
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
                ],
                timeout=180,
            )
            payload = response.json()
            if response.status_code >= 400:
                errors = payload.get("errors") or []
                if errors:
                    message = errors[0].get("message") or str(errors[0])
                    raise RuntimeError(f"Runware error: {message}")
                response.raise_for_status()

            errors = payload.get("errors") or []
            if errors:
                message = errors[0].get("message") or str(errors[0])
                raise RuntimeError(f"Runware error: {message}")

            data = payload.get("data") or []
            if not data:
                raise RuntimeError("Runware did not return inference data.")

            result = data[0]
            image_url = result.get("imageURL") or result.get("imageUrl") or result.get("image_url")
            if not isinstance(image_url, str) or not image_url:
                raise RuntimeError("Runware did not return an image URL.")

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
                },
            )
            return image_bytes, model

        raise RuntimeError("No infographic generation models configured.")

    def _write_generated_image(self, response: object, image_path: Path) -> None:
        candidates = getattr(response, "candidates", None) or []
        if candidates and getattr(candidates[0], "content", None):
            for part in getattr(candidates[0].content, "parts", None) or []:
                inline_data = getattr(part, "inline_data", None)
                mime_type = getattr(inline_data, "mime_type", None)
                if inline_data and mime_type and mime_type.startswith("image/"):
                    image_bytes = getattr(inline_data, "data", None)
                    if image_bytes is None:
                        continue
                    image_path.write_bytes(image_bytes)
                    return

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
