from __future__ import annotations

from typing import Any

from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.news import NewsArticleMetadata, NewsMetadata
from app.models.metadata.source import SourceMetadataEnvelope
from app.services.source_metadata import (
    SOURCE_METADATA_KEY,
    attach_source_metadata,
    dump_source_metadata,
    normalize_source_metadata,
)


def test_dump_source_metadata_validates_and_normalizes_payload() -> None:
    payload = dump_source_metadata(
        {
            "schema_version": 1,
            "kind": "research_paper",
            "provider": "arxiv",
            "source_id": "  2509.15194v2  ",
            "authors": [{"name": " Ada Lovelace ", "affiliation": " Example Lab "}],
        }
    )

    assert payload is not None
    assert payload["source_id"] == "2509.15194v2"
    assert payload["authors"][0]["name"] == "Ada Lovelace"
    assert payload["authors"][0]["affiliation"] == "Example Lab"


def test_dump_source_metadata_rejects_invalid_payload() -> None:
    assert dump_source_metadata({"authors": [{"name": ""}]}) is None
    assert normalize_source_metadata("not metadata") is None


def test_attach_source_metadata_keeps_target_unchanged_for_invalid_payload() -> None:
    target: dict[str, Any] = {"source": "example.com"}

    result = attach_source_metadata(target, SourceMetadataEnvelope(source_id="2509.15194v2"))
    assert result is target
    assert target[SOURCE_METADATA_KEY]["source_id"] == "2509.15194v2"

    attach_source_metadata(target, {"authors": [{"name": ""}]})
    assert target[SOURCE_METADATA_KEY]["source_id"] == "2509.15194v2"


def test_content_metadata_models_validate_source_metadata() -> None:
    source_metadata = SourceMetadataEnvelope(source_id="2509.15194v2")
    article = ArticleMetadata(source_metadata=source_metadata)
    news = NewsMetadata(
        article=NewsArticleMetadata.model_validate({"url": "https://example.com/story"}),
        source_metadata=source_metadata,
    )

    assert article.source_metadata is not None
    assert article.source_metadata.source_id == "2509.15194v2"
    assert news.source_metadata is not None
    assert news.source_metadata.source_id == "2509.15194v2"
