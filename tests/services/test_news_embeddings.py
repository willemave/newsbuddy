"""Tests for news embedding model resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.core.settings import Settings
from app.services.news_embeddings import is_api_embedding_model, warm_news_embedding_model


def test_default_embedding_model_is_hosted() -> None:
    """Workers should not load a local embedding model by default."""
    assert is_api_embedding_model(Settings.model_fields["news_embedding_model"].default)


def test_warm_is_a_no_op_for_hosted_models() -> None:
    """Warming a hosted model would load the local model it exists to avoid."""
    settings = SimpleNamespace(news_embedding_model="openrouter:qwen/qwen3-embedding-8b")

    with (
        patch("app.services.news_embeddings.get_settings", return_value=settings),
        patch("app.services.news_embeddings.get_news_embedding_model") as load_model,
    ):
        warm_news_embedding_model()

    load_model.assert_not_called()


def test_warm_loads_local_models() -> None:
    """A locally configured model still gets warmed to hide first-request latency."""
    settings = SimpleNamespace(news_embedding_model="Qwen/Qwen3-Embedding-0.6B")

    with (
        patch("app.services.news_embeddings.get_settings", return_value=settings),
        patch("app.services.news_embeddings.get_news_embedding_model") as load_model,
    ):
        warm_news_embedding_model()

    load_model.return_value.encode.assert_called_once()
