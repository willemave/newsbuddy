from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models.contracts import ContentStatus, ContentType
from app.models.metadata import ContentData
from app.services import image_generation


def _build_article_content() -> ContentData:
    return ContentData(
        id=123,
        content_type=ContentType.ARTICLE,
        url="https://example.com/article",
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
    assert captured["model"] == image_generation.INFOGRAPHIC_MODEL
    assert captured["config"].image_config.image_size == image_generation.INFOGRAPHIC_IMAGE_SIZE
    assert captured["config"].image_config.aspect_ratio == "16:9"
    assert image_generation.INFOGRAPHIC_IMAGE_SIZE == "512"
