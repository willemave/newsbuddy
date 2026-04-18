from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic import HttpUrl, TypeAdapter

from app.models.contracts import ContentStatus, ContentType
from app.models.metadata import ContentData
from app.services import image_generation


def _build_article_content() -> ContentData:
    return ContentData(
        id=123,
        content_type=ContentType.ARTICLE,
        url=TypeAdapter(HttpUrl).validate_python("https://example.com/article"),
        status=ContentStatus.COMPLETED,
        metadata={
            "summary_kind": "long_structured",
            "summary_version": 1,
            "summary": {
                "title": "Example article",
                "overview": (
                    "This overview is intentionally long enough to satisfy structured "
                    "summary validation requirements for image generation tests."
                ),
                "bullet_points": [
                    {"text": "Key point one with enough detail.", "category": "key_finding"},
                    {"text": "Key point two with enough detail.", "category": "methodology"},
                    {"text": "Key point three with enough detail.", "category": "conclusion"},
                ],
                "quotes": [],
                "topics": ["Testing"],
            },
        },
    )


def test_generate_infographic_uses_lowest_supported_image_size(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class DummyModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return SimpleNamespace(
                usage_metadata=None,
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        mime_type="image/png",
                                        data=b"fake-png",
                                    )
                                )
                            ]
                        )
                    )
                ],
            )

    class DummyClient:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs
            self.models = DummyModels()

    monkeypatch.setattr(
        image_generation,
        "get_settings",
        lambda: SimpleNamespace(
            google_cloud_project=None,
            google_cloud_location="us-central1",
            google_api_key="test-key",
            image_generation_model=image_generation.DEFAULT_IMAGE_GENERATION_MODEL,
            image_generation_fallback_model=None,
            infographic_generation_provider="google",
            infographic_generation_model=None,
            infographic_generation_fallback_model=None,
            runware_api_key=None,
        ),
    )
    monkeypatch.setattr(image_generation.genai, "Client", DummyClient)
    monkeypatch.setattr(
        image_generation,
        "get_news_thumbnails_dir",
        lambda: tmp_path / "news_thumbnails",
    )
    monkeypatch.setattr(
        image_generation,
        "get_content_images_dir",
        lambda: tmp_path / "content",
    )
    monkeypatch.setattr(
        image_generation,
        "get_thumbnails_dir",
        lambda: tmp_path / "thumbnails",
    )

    service = image_generation.ImageGenerationService()
    monkeypatch.setattr(service, "generate_thumbnail", lambda source_path, content_id: None)

    result = service.generate_image(_build_article_content())
    config = cast(Any, captured["config"])

    assert result.success is True
    assert captured["model"] == image_generation.DEFAULT_IMAGE_GENERATION_MODEL
    assert config.image_config.image_size == image_generation.INFOGRAPHIC_IMAGE_SIZE
    assert config.image_config.aspect_ratio == "16:9"
    assert image_generation.INFOGRAPHIC_IMAGE_SIZE == "512"


def test_generate_infographic_retries_with_fallback_model_on_not_found(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_models: list[str] = []

    class DummyModels:
        def generate_content(self, *, model, contents, config):
            captured_models.append(model)
            if model == "missing-model":
                raise RuntimeError("404 NOT_FOUND: Requested entity was not found.")
            return SimpleNamespace(
                usage_metadata=None,
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        mime_type="image/png",
                                        data=b"fallback-png",
                                    )
                                )
                            ]
                        )
                    )
                ],
            )

    class DummyClient:
        def __init__(self, **kwargs) -> None:
            self.models = DummyModels()

    monkeypatch.setattr(
        image_generation,
        "get_settings",
        lambda: SimpleNamespace(
            google_cloud_project=None,
            google_cloud_location="global",
            google_api_key="test-key",
            image_generation_model="missing-model",
            image_generation_fallback_model="gemini-2.5-flash-image",
            infographic_generation_provider="google",
            infographic_generation_model=None,
            infographic_generation_fallback_model=None,
            runware_api_key=None,
        ),
    )
    monkeypatch.setattr(image_generation.genai, "Client", DummyClient)
    monkeypatch.setattr(
        image_generation,
        "get_news_thumbnails_dir",
        lambda: tmp_path / "news_thumbnails",
    )
    monkeypatch.setattr(
        image_generation,
        "get_content_images_dir",
        lambda: tmp_path / "content",
    )
    monkeypatch.setattr(
        image_generation,
        "get_thumbnails_dir",
        lambda: tmp_path / "thumbnails",
    )

    service = image_generation.ImageGenerationService()
    monkeypatch.setattr(service, "generate_thumbnail", lambda source_path, content_id: None)

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert captured_models == ["missing-model", "gemini-2.5-flash-image"]


def test_build_infographic_prompt_builds_no_text_explainer() -> None:
    prompt = image_generation._build_infographic_prompt(_build_article_content())

    assert "Create a no-text editorial infographic" in prompt
    assert "Story title: Example article" in prompt
    assert "Key facts to encode visually:" in prompt
    assert "Use connected artifacts" in prompt
    assert "Preferred composition:" in prompt
    assert "The story context below is reference only" in prompt


def test_generate_infographic_uses_runware_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "imageURL": "https://example.com/generated.png",
                        "cost": 0.0038,
                    }
                ]
            }

    def fake_post(url, *, headers, json, timeout):
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(
        image_generation,
        "get_settings",
        lambda: SimpleNamespace(
            google_cloud_project=None,
            google_cloud_location="global",
            google_api_key=None,
            image_generation_model=image_generation.DEFAULT_IMAGE_GENERATION_MODEL,
            image_generation_fallback_model=None,
            infographic_generation_provider="runware",
            infographic_generation_model="runware:101@1",
            infographic_generation_fallback_model=None,
            runware_api_key="runware-key",
        ),
    )
    monkeypatch.setattr(image_generation.requests, "post", fake_post)
    monkeypatch.setattr(
        image_generation.ImageGenerationService,
        "_download_file",
        lambda self, url: b"runware-png",
    )
    usage_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        image_generation,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: usage_calls.append(kwargs),
    )
    monkeypatch.setattr(
        image_generation,
        "get_news_thumbnails_dir",
        lambda: tmp_path / "news_thumbnails",
    )
    monkeypatch.setattr(
        image_generation,
        "get_content_images_dir",
        lambda: tmp_path / "content",
    )
    monkeypatch.setattr(
        image_generation,
        "get_thumbnails_dir",
        lambda: tmp_path / "thumbnails",
    )

    service = image_generation.ImageGenerationService()
    monkeypatch.setattr(service, "generate_thumbnail", lambda source_path, content_id: None)

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert usage_calls and usage_calls[0]["provider"] == "runware"
    assert usage_calls[0]["usage"] == {"request_count": 1}
    payload = cast(list[dict[str, object]], captured["json"])
    assert payload[0]["model"] == "runware:101@1"
    assert payload[0]["width"] == image_generation.RUNWARE_INFOGRAPHIC_WIDTH
    assert payload[0]["height"] == image_generation.RUNWARE_INFOGRAPHIC_HEIGHT
    assert payload[0]["negativePrompt"] == image_generation.RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT
