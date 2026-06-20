"""Compare local and OpenRouter embeddings on the news title-clustering eval."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.services.news_relations as news_relations
from app.core.db import get_session_factory
from app.core.settings import get_settings
from app.services.news_embeddings import (
    encode_news_texts as encode_news_texts_locally,
)
from app.services.news_embeddings import get_news_embedding_model
from scripts.run_title_clustering_eval import (
    _aggregate,
    _case_groups,
    _evaluate_case,
    _make_item,
    _parse_thresholds,
    _select_cases,
    _temporary_thresholds,
)

DEFAULT_OPENROUTER_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class EmbeddingEncoder(Protocol):
    stats: EmbeddingStats

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts into normalized vectors."""

    def prefetch(self, texts: list[str]) -> None:
        """Preload normalized vectors for texts."""

    def reset_stats(self) -> None:
        """Reset per-run timing counters."""


@dataclass
class EmbeddingStats:
    calls: int = 0
    texts: int = 0
    latency_ms: float = 0.0
    provider_requests: int = 0
    provider_texts: int = 0
    provider_latency_ms: float = 0.0
    prefetch_texts: int = 0
    prefetch_latency_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def average_call_ms(self) -> float:
        return self.latency_ms / self.calls if self.calls else 0.0

    @property
    def average_text_ms(self) -> float:
        return self.latency_ms / self.texts if self.texts else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "texts": self.texts,
            "latency_ms": self.latency_ms,
            "average_call_ms": self.average_call_ms,
            "average_text_ms": self.average_text_ms,
            "provider_requests": self.provider_requests,
            "provider_texts": self.provider_texts,
            "provider_latency_ms": self.provider_latency_ms,
            "prefetch_texts": self.prefetch_texts,
            "prefetch_latency_ms": self.prefetch_latency_ms,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


def _unique_missing_texts(cache: dict[str, np.ndarray], texts: list[str]) -> list[str]:
    missing: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if text in cache or text in seen:
            continue
        seen.add(text)
        missing.append(text)
    return missing


def _store_vectors(
    cache: dict[str, np.ndarray],
    texts: list[str],
    vectors: np.ndarray,
) -> None:
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise ValueError(
            "Embedding response shape did not match input text count: "
            f"expected {len(texts)}, got {vectors.shape}"
        )
    for text, vector in zip(texts, vectors, strict=True):
        cache[text] = np.asarray(vector, dtype=np.float32)


def _stack_cached_vectors(cache: dict[str, np.ndarray], texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack([cache[text] for text in texts]).astype(np.float32)


@dataclass
class LocalEmbeddingEncoder:
    stats: EmbeddingStats = field(default_factory=EmbeddingStats)
    _cache: dict[str, np.ndarray] = field(default_factory=dict)

    def encode(self, texts: list[str]) -> np.ndarray:
        started_at = perf_counter()
        cache_hits = sum(1 for text in texts if text in self._cache)
        self._embed_missing(texts)
        vectors = _stack_cached_vectors(self._cache, texts)
        self.stats.calls += 1
        self.stats.texts += len(texts)
        self.stats.cache_hits += cache_hits
        self.stats.latency_ms += (perf_counter() - started_at) * 1000
        return vectors

    def prefetch(self, texts: list[str]) -> None:
        started_at = perf_counter()
        missing_count = self._embed_missing(texts)
        self.stats.prefetch_texts += missing_count
        self.stats.prefetch_latency_ms += (perf_counter() - started_at) * 1000

    def reset_stats(self) -> None:
        self.stats = EmbeddingStats()

    def _embed_missing(self, texts: list[str]) -> int:
        missing = _unique_missing_texts(self._cache, texts)
        if not missing:
            return 0
        started_at = perf_counter()
        vectors = encode_news_texts_locally(missing)
        self.stats.provider_requests += 1
        self.stats.provider_texts += len(missing)
        self.stats.provider_latency_ms += (perf_counter() - started_at) * 1000
        self.stats.cache_misses += len(missing)
        _store_vectors(self._cache, missing, vectors)
        return len(missing)


@dataclass
class OpenRouterEmbeddingEncoder:
    model: str
    api_key: str
    base_url: str
    batch_size: int
    timeout_seconds: float
    extra_body: dict[str, Any] | None
    stats: EmbeddingStats = field(default_factory=EmbeddingStats)
    _cache: dict[str, np.ndarray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        started_at = perf_counter()
        cache_hits = sum(1 for text in texts if text in self._cache)
        self._embed_missing(texts)
        vectors = _stack_cached_vectors(self._cache, texts)
        self.stats.calls += 1
        self.stats.texts += len(texts)
        self.stats.cache_hits += cache_hits
        self.stats.latency_ms += (perf_counter() - started_at) * 1000
        return vectors

    def prefetch(self, texts: list[str]) -> None:
        started_at = perf_counter()
        missing_count = self._embed_missing(texts)
        self.stats.prefetch_texts += missing_count
        self.stats.prefetch_latency_ms += (perf_counter() - started_at) * 1000

    def reset_stats(self) -> None:
        self.stats = EmbeddingStats()

    def _embed_missing(self, texts: list[str]) -> int:
        missing = _unique_missing_texts(self._cache, texts)
        if not missing:
            return 0

        embeddings: list[list[float]] = []
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            request_started_at = perf_counter()
            response = self._client.embeddings.create(
                model=self.model,
                input=batch,
                encoding_format="float",
                extra_body=self.extra_body,
                timeout=self.timeout_seconds,
            )
            self.stats.provider_requests += 1
            self.stats.provider_texts += len(batch)
            self.stats.provider_latency_ms += (perf_counter() - request_started_at) * 1000
            embeddings.extend(
                list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)
            )

        vectors = np.asarray(embeddings, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(missing):
            raise ValueError(
                "OpenRouter embedding response shape did not match input text count: "
                f"expected {len(missing)}, got {vectors.shape}"
            )
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        normalized_vectors = vectors / np.maximum(norms, 1e-12)
        self.stats.cache_misses += len(missing)
        _store_vectors(self._cache, missing, normalized_vectors.astype(np.float32))
        return len(missing)


@dataclass(frozen=True)
class EmbeddingVariant:
    label: str
    backend: str
    model: str
    encoder: EmbeddingEncoder


def _parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Compare the local news embedding model with OpenRouter's "
            "qwen/qwen3-embedding-8b on the curated title-clustering eval."
        )
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Optional case id(s) to run. Defaults to the full curated eval set.",
    )
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        help="Threshold spec in label:primary:secondary[:reranker] format.",
    )
    parser.add_argument(
        "--use-reranker",
        action="store_true",
        help="Enable the configured reranker for both embedding variants.",
    )
    parser.add_argument(
        "--reranker-model",
        default=None,
        help="Optional reranker model override when --use-reranker is set.",
    )
    parser.add_argument(
        "--reranker-max-candidates",
        type=int,
        default=None,
        help="Optional reranker candidate cap override when --use-reranker is set.",
    )
    parser.add_argument(
        "--local-model",
        default=settings.news_embedding_model,
        help="Local sentence-transformers embedding model id.",
    )
    parser.add_argument(
        "--local-label",
        default="local",
        help="Label for the local embedding run.",
    )
    parser.add_argument(
        "--skip-local",
        action="store_true",
        help="Skip the local embedding baseline.",
    )
    parser.add_argument(
        "--openrouter-model",
        default=DEFAULT_OPENROUTER_EMBEDDING_MODEL,
        help="OpenRouter embedding model slug.",
    )
    parser.add_argument(
        "--openrouter-label",
        default="openrouter-qwen3-embedding-8b",
        help="Label for the OpenRouter embedding run.",
    )
    parser.add_argument(
        "--openrouter-base-url",
        default=DEFAULT_OPENROUTER_BASE_URL,
        help="OpenRouter OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--openrouter-api-key-env",
        default="OPENROUTER_API_KEY",
        help="Environment variable that contains the OpenRouter API key.",
    )
    parser.add_argument(
        "--openrouter-batch-size",
        type=int,
        default=32,
        help="Texts per OpenRouter embeddings request.",
    )
    parser.add_argument(
        "--openrouter-timeout-seconds",
        type=float,
        default=60.0,
        help="OpenRouter request timeout.",
    )
    parser.add_argument(
        "--openrouter-provider-order",
        action="append",
        default=None,
        help="Optional OpenRouter provider preference. Repeat to set ordered providers.",
    )
    parser.add_argument(
        "--openrouter-provider-sort",
        default=None,
        help="Optional OpenRouter provider sort, for example throughput or price.",
    )
    parser.add_argument(
        "--allow-provider-data-collection",
        action="store_true",
        help="Do not send OpenRouter provider.data_collection='deny'.",
    )
    parser.add_argument(
        "--skip-openrouter",
        action="store_true",
        help="Skip the OpenRouter embedding run.",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Run one small embedding call before timing each selected variant.",
    )
    parser.add_argument(
        "--prefetch-embeddings",
        action="store_true",
        help="Preload unique eval embedding texts before each backend run.",
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Print only failed cases after each summary.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for JSON results.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text.",
    )
    parser.add_argument(
        "--fail-on-failed-cases",
        action="store_true",
        help="Exit nonzero when any evaluated variant has failed cases.",
    )
    return parser.parse_args()


def _openrouter_extra_body(args: argparse.Namespace) -> dict[str, Any] | None:
    provider: dict[str, Any] = {}
    if args.openrouter_provider_order:
        provider["order"] = args.openrouter_provider_order
    if args.openrouter_provider_sort:
        provider["sort"] = args.openrouter_provider_sort
    if not args.allow_provider_data_collection:
        provider["data_collection"] = "deny"
    return {"provider": provider} if provider else None


def _resolve_openrouter_api_key(env_name: str) -> str:
    key = os.getenv(env_name)
    if key:
        return key
    settings = get_settings()
    if env_name == "OPENROUTER_API_KEY" and settings.openrouter_api_key:
        return settings.openrouter_api_key
    raise ValueError(f"{env_name} is not configured.")


def _build_variants(args: argparse.Namespace) -> list[EmbeddingVariant]:
    variants: list[EmbeddingVariant] = []
    if not args.skip_local:
        variants.append(
            EmbeddingVariant(
                label=args.local_label,
                backend="local",
                model=args.local_model,
                encoder=LocalEmbeddingEncoder(),
            )
        )
    if not args.skip_openrouter:
        variants.append(
            EmbeddingVariant(
                label=args.openrouter_label,
                backend="openrouter",
                model=args.openrouter_model,
                encoder=OpenRouterEmbeddingEncoder(
                    model=args.openrouter_model,
                    api_key=_resolve_openrouter_api_key(args.openrouter_api_key_env),
                    base_url=args.openrouter_base_url,
                    batch_size=args.openrouter_batch_size,
                    timeout_seconds=args.openrouter_timeout_seconds,
                    extra_body=_openrouter_extra_body(args),
                ),
            )
        )
    if not variants:
        raise ValueError("At least one embedding variant must be selected.")
    return variants


@contextmanager
def _patched_news_encoder(encoder: EmbeddingEncoder) -> Iterator[None]:
    original_encoder = news_relations.encode_news_texts
    news_relations.encode_news_texts = encoder.encode
    try:
        yield
    finally:
        news_relations.encode_news_texts = original_encoder


@contextmanager
def _temporary_local_embedding_model(model: str) -> Iterator[None]:
    previous_model = os.environ.get("NEWS_EMBEDDING_MODEL")
    os.environ["NEWS_EMBEDDING_MODEL"] = model
    get_settings.cache_clear()
    get_news_embedding_model.cache_clear()
    try:
        yield
    finally:
        if previous_model is None:
            os.environ.pop("NEWS_EMBEDDING_MODEL", None)
        else:
            os.environ["NEWS_EMBEDDING_MODEL"] = previous_model
        get_settings.cache_clear()
        get_news_embedding_model.cache_clear()


@contextmanager
def _variant_settings(variant: EmbeddingVariant) -> Iterator[None]:
    if variant.backend == "local":
        with _temporary_local_embedding_model(variant.model):
            yield
        return
    with nullcontext():
        yield


def _collect_prefetch_texts(cases: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    base_time = datetime.now(UTC).replace(tzinfo=None)

    def add_text(text: str) -> None:
        if not text or text in seen:
            return
        seen.add(text)
        texts.append(text)

    for case in cases:
        case_id = str(case["case_id"])
        idx = 0
        for group in _case_groups(case):
            for title in group:
                item = _make_item(
                    idx=idx,
                    title=title,
                    case_id=case_id,
                    ingested_at=base_time,
                )
                add_text(news_relations.title_matching_text(item))
                add_text(news_relations.content_matching_text(item))
                add_text(news_relations.provenance_matching_text(item))
                idx += 1
    return texts


def _run_variant(
    *,
    variant: EmbeddingVariant,
    cases: list[dict[str, Any]],
    thresholds: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    runs: list[dict[str, Any]] = []
    prefetch_texts = _collect_prefetch_texts(cases) if args.prefetch_embeddings else []
    with _variant_settings(variant):
        if args.warmup:
            variant.encoder.encode(["warmup"])
        for threshold in thresholds:
            variant.encoder.reset_stats()
            if prefetch_texts:
                variant.encoder.prefetch(prefetch_texts)
            results: list[dict[str, Any]] = []
            started_at = perf_counter()
            with (
                _patched_news_encoder(variant.encoder),
                _temporary_thresholds(
                    primary=float(threshold["primary"]),
                    secondary=float(threshold["secondary"]),
                    use_reranker=bool(threshold["use_reranker"]),
                    reranker_threshold=float(threshold["reranker"]),
                    reranker_model=args.reranker_model,
                    reranker_max_candidates=args.reranker_max_candidates,
                ),
            ):
                for case in cases:
                    with session_factory() as db:
                        results.append(_evaluate_case(db, case))
                        db.rollback()
            wall_time_ms = (perf_counter() - started_at) * 1000
            summary = _aggregate(results)
            summary.update(
                {
                    "wall_time_ms": wall_time_ms,
                    "embedding": variant.encoder.stats.as_dict(),
                }
            )
            runs.append(
                {
                    "variant": {
                        "label": variant.label,
                        "backend": variant.backend,
                        "model": variant.model,
                    },
                    "threshold": threshold,
                    "summary": summary,
                    "results": results,
                }
            )
    return runs


def _print_text(runs: list[dict[str, Any]], *, failures_only: bool) -> None:
    for run in runs:
        variant = run["variant"]
        threshold = run["threshold"]
        summary = run["summary"]
        embedding = summary["embedding"]
        print(
            f"[{variant['label']} | {threshold['label']}] "
            f"{summary['passed_count']}/{summary['case_count']} passed "
            f"macro_f1={summary['macro_f1']:.3f} "
            f"precision={summary['macro_precision']:.3f} "
            f"recall={summary['macro_recall']:.3f} "
            f"wall={summary['wall_time_ms']:.0f}ms "
            f"encode={embedding['latency_ms']:.0f}ms/"
            f"{embedding['calls']} calls/{embedding['texts']} texts "
            f"provider={embedding['provider_latency_ms']:.0f}ms/"
            f"{embedding['provider_requests']} requests/{embedding['provider_texts']} texts "
            f"prefetch={embedding['prefetch_latency_ms']:.0f}ms/"
            f"{embedding['prefetch_texts']} texts"
        )
        rows = [result for result in run["results"] if not failures_only or not result["passed"]]
        for result in rows:
            status = "PASS" if result["passed"] else "FAIL"
            print(
                f"{status} {result['case_id']} "
                f"f1={result['f1']:.3f} "
                f"precision={result['precision']:.3f} "
                f"recall={result['recall']:.3f} "
                f"{result['label']}"
            )
        print()


def main() -> int:
    args = _parse_args()
    case_ids = set(args.case_ids or [])
    cases = _select_cases(case_ids or None)
    thresholds = _parse_thresholds(args.thresholds, use_reranker=args.use_reranker)
    variants = _build_variants(args)

    runs: list[dict[str, Any]] = []
    for variant in variants:
        runs.extend(
            _run_variant(
                variant=variant,
                cases=cases,
                thresholds=thresholds,
                args=args,
            )
        )

    payload = {
        "case_count": len(cases),
        "variants": [
            {"label": variant.label, "backend": variant.backend, "model": variant.model}
            for variant in variants
        ],
        "runs": runs,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_text(runs, failures_only=args.failures_only)
        if args.output is not None:
            print(f"Wrote {args.output.resolve()}")

    if args.fail_on_failed_cases:
        return 0 if all(run["summary"]["failed_count"] == 0 for run in runs) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
