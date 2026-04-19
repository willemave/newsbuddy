from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

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
    monkeypatch.setattr(service, "_detect_readable_text_in_image", lambda **_: None)

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
    monkeypatch.setattr(service, "_detect_readable_text_in_image", lambda **_: None)

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert captured_models == ["missing-model", "gemini-2.5-flash-image"]


def test_build_infographic_prompt_builds_long_gemini_editorial_style() -> None:
    prompt = image_generation._build_infographic_prompt(_build_article_content())

    assert "Create a premium no-text editorial illustration for Newsly." in prompt
    assert "Hard constraints:" in prompt
    assert "Visual brief:" in prompt
    assert "Primary subject:" in prompt
    assert "Visual metaphor:" in prompt
    assert "Scene direction:" in prompt
    assert "Supporting cues:" in prompt
    assert "Story title:" not in prompt
    assert "Description:" not in prompt


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
    monkeypatch.setattr(service, "_detect_readable_text_in_image", lambda **_: None)

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert usage_calls and usage_calls[0]["provider"] == "runware"
    assert usage_calls[0]["usage"] == {"request_count": 1}
    payload = cast(list[dict[str, object]], captured["json"])
    assert payload[0]["model"] == "runware:101@1"
    assert payload[0]["width"] == image_generation.RUNWARE_INFOGRAPHIC_WIDTH
    assert payload[0]["height"] == image_generation.RUNWARE_INFOGRAPHIC_HEIGHT
    assert payload[0]["negativePrompt"] == image_generation.RUNWARE_INFOGRAPHIC_NEGATIVE_PROMPT
    UUID(str(payload[0]["taskUUID"]))


def test_generate_infographic_falls_back_to_google_when_runware_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runware_calls: list[list[dict[str, object]]] = []
    google_calls: list[str] = []

    class RejectedResponse:
        status_code = 400

        def json(self) -> dict[str, object]:
            return {
                "errors": [
                    {
                        "message": "Invalid value for 'taskUUID' parameter.",
                        "parameter": "taskUUID",
                        "code": "validation_error",
                    }
                ]
            }

    class DummyModels:
        def generate_content(self, *, model, contents, config):
            del config
            google_calls.append(model)
            return SimpleNamespace(
                usage_metadata=None,
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        mime_type="image/png",
                                        data=b"google-fallback-png",
                                    )
                                )
                            ]
                        )
                    )
                ],
            )

    class DummyClient:
        def __init__(self, **_kwargs) -> None:
            self.models = DummyModels()

    def fake_post(url, *, headers, json, timeout):
        del url, headers, timeout
        runware_calls.append(cast(list[dict[str, object]], json))
        return RejectedResponse()

    monkeypatch.setattr(
        image_generation,
        "get_settings",
        lambda: SimpleNamespace(
            google_cloud_project=None,
            google_cloud_location="global",
            google_api_key="test-key",
            image_generation_model="gemini-2.5-flash-image",
            image_generation_fallback_model=None,
            infographic_generation_provider="runware",
            infographic_generation_model="runware:101@1",
            infographic_generation_fallback_model=None,
            runware_api_key="runware-key",
        ),
    )
    monkeypatch.setattr(image_generation.genai, "Client", DummyClient)
    monkeypatch.setattr(image_generation.requests, "post", fake_post)
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
    monkeypatch.setattr(service, "_detect_readable_text_in_image", lambda **_: None)

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert len(runware_calls) == image_generation.RUNWARE_INLINE_RETRY_ATTEMPTS
    assert google_calls == ["gemini-2.5-flash-image"]
    assert all(payload[0]["taskUUID"] for payload in runware_calls)


def test_generate_infographic_retries_when_quality_check_detects_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    prompts: list[str] = []
    quality_checks = iter(
        [
            image_generation.ImageTextCheck(
                has_readable_text=True,
                reason="a poster headline was visible",
                confidence=0.92,
            ),
            image_generation.ImageTextCheck(has_readable_text=False),
        ]
    )

    class DummyModels:
        def generate_content(self, *, model, contents, config):
            del model, config
            prompts.append(cast(str, contents))
            return SimpleNamespace(
                usage_metadata=None,
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        mime_type="image/png",
                                        data=b"retry-png",
                                    )
                                )
                            ]
                        )
                    )
                ],
            )

    class DummyClient:
        def __init__(self, **_kwargs) -> None:
            self.models = DummyModels()

    monkeypatch.setattr(
        image_generation,
        "get_settings",
        lambda: SimpleNamespace(
            google_cloud_project=None,
            google_cloud_location="global",
            google_api_key="test-key",
            image_generation_model="gemini-2.5-flash-image",
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
    monkeypatch.setattr(
        service,
        "_detect_readable_text_in_image",
        lambda **_: next(quality_checks),
    )

    result = service.generate_image(_build_article_content())

    assert result.success is True
    assert len(prompts) == 2
    assert "Regeneration note:" in prompts[1]
    assert "poster headline was visible" in prompts[1]
