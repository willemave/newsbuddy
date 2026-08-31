"""Optional local and hosted embedding encoders for offline evals."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray


class SentenceTransformerEncoder:
    def __init__(self, model: str) -> None:
        if not model.strip():
            raise ValueError("model must not be blank")
        from sentence_transformers import SentenceTransformer

        self.model = model
        self._model = SentenceTransformer(model)

    def encode(self, texts: Sequence[str]) -> NDArray[np.floating[Any]]:
        return np.asarray(
            self._model.encode(
                list(texts),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float64,
        )


class OpenAICompatibleEncoder:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        batch_size: int = 64,
        timeout_seconds: float = 120.0,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if not model.strip() or not api_key.strip() or not base_url.strip():
            raise ValueError("model, api_key, and base_url are required")
        if batch_size < 1 or timeout_seconds <= 0:
            raise ValueError("batch_size and timeout_seconds must be positive")
        from openai import OpenAI

        self.model = model
        self._batch_size = batch_size
        self._extra_body = extra_body
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    def encode(self, texts: Sequence[str]) -> NDArray[np.floating[Any]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = self._client.embeddings.create(
                model=self.model,
                input=batch,
                encoding_format="float",
                extra_body=self._extra_body,
            )
            vectors.extend(
                list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)
            )
        return np.asarray(vectors, dtype=np.float64)
