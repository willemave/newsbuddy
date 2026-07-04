"""Embedding helpers for short-form news ranking."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import numpy as np

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
OPENROUTER_EMBEDDING_PREFIX = "openrouter:"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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
    settings = get_settings()
    return encode_texts_with_embedding_model(
        texts,
        model_spec=settings.news_embedding_model,
        batch_size=32,
        timeout_seconds=30,
    )


def encode_texts_with_embedding_model(
    texts: list[str],
    *,
    model_spec: str,
    batch_size: int,
    timeout_seconds: int,
) -> np.ndarray:
    """Encode texts with either the local news model or an OpenRouter embedding model."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    if model_spec.startswith(OPENROUTER_EMBEDDING_PREFIX):
        return _encode_texts_with_openrouter(
            texts,
            model=model_spec.removeprefix(OPENROUTER_EMBEDDING_PREFIX),
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
        )
    model = get_news_embedding_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def _encode_texts_with_openrouter(
    texts: list[str],
    *,
    model: str,
    batch_size: int,
    timeout_seconds: int,
) -> np.ndarray:
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured in settings.")
    if not model.strip():
        raise ValueError("OpenRouter embedding model must be configured.")

    from openai import OpenAI

    request_timeout = httpx.Timeout(
        timeout_seconds,
        connect=10.0,
        read=float(timeout_seconds),
        write=10.0,
        pool=10.0,
    )
    client = OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=request_timeout,
        max_retries=0,
        http_client=httpx.Client(timeout=request_timeout),
    )
    embeddings: list[list[float]] = []
    try:
        for start in range(0, len(texts), max(batch_size, 1)):
            batch = texts[start : start + max(batch_size, 1)]
            response = client.embeddings.create(
                model=model,
                input=batch,
                encoding_format="float",
                timeout=request_timeout,
            )
            embeddings.extend(
                list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)
            )
    finally:
        client.close()

    vectors = np.asarray(embeddings, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise ValueError(
            "OpenRouter embedding response shape did not match input text count: "
            f"expected {len(texts)}, got {vectors.shape}"
        )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)
