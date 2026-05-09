"""Embedding helpers for short-form news ranking."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)


def resolve_transformer_device(preferred: str) -> str:
    candidate = preferred.strip().lower()
    if candidate and candidate != "auto":
        return candidate

    try:
        import torch
    except Exception:  # noqa: BLE001
        return "cpu"

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=1)
def get_news_embedding_model() -> Any:
    """Return the lazily loaded sentence-transformers model."""
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    device = resolve_transformer_device(settings.news_embedding_device)
    logger.info(
        "Loading news embedding model",
        extra={
            "component": "news_embeddings",
            "operation": "load_model",
            "context_data": {
                "model": settings.news_embedding_model,
                "device": device,
            },
        },
    )
    return SentenceTransformer(settings.news_embedding_model, device=device)


def warm_news_embedding_model() -> None:
    """Warm the embedding model to avoid first-request latency."""
    model = get_news_embedding_model()
    model.encode(["warmup"], normalize_embeddings=True, convert_to_numpy=True)


def encode_news_texts(texts: list[str]) -> np.ndarray:
    """Encode matching texts into normalized vectors."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    model = get_news_embedding_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def clear_news_embedding_cache() -> None:
    get_news_embedding_model.cache_clear()
