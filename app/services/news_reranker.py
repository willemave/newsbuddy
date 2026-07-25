"""Qwen reranker helpers for title-aware news relation matching."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import torch

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services.news_embeddings import resolve_transformer_device
from app.services.prompt_library import load_prompt

logger = get_logger(__name__)

RERANKER_SYSTEM_PROMPT = load_prompt("content/news_pipeline#reranker_system")
DEFAULT_NEWS_RERANKER_INSTRUCTION = (
    "Given a news headline and a candidate cluster, determine whether they describe "
    "the same underlying news event. Treat different product launches, policy "
    "updates, lawsuits, leaks, stock moves, or follow-up developments as different "
    "unless the query is clearly about the same event."
)
RERANKER_PREFIX = f"<|im_start|>system\n{RERANKER_SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n"
RERANKER_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


@dataclass(frozen=True)
class _NewsRerankerRuntime:
    device: str
    tokenizer: Any
    model: Any
    prefix_tokens: list[int]
    suffix_tokens: list[int]
    true_token_id: int
    false_token_id: int


def _resolve_dtype(device: str) -> torch.dtype:
    import torch

    if device == "cpu":
        return torch.float32
    return torch.float16


@lru_cache(maxsize=1)
def _get_news_reranker_runtime() -> _NewsRerankerRuntime:
    # Imported lazily so importing this module (for warm_news_reranker_model) does
    # not drag torch into every worker process.
    from transformers import AutoModelForCausalLM, AutoTokenizer

    settings = get_settings()
    device = resolve_transformer_device(settings.news_list_reranker_device)
    model_id = settings.news_list_reranker_model
    logger.info(
        "Loading news reranker model",
        extra={
            "component": "news_reranker",
            "operation": "load_model",
            "context_data": {
                "model": model_id,
                "device": device,
            },
        },
    )
    tokenizer: Any = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model: Any = AutoModelForCausalLM.from_pretrained(model_id, dtype=_resolve_dtype(device))
    model = model.to(device).eval()
    return _NewsRerankerRuntime(
        device=device,
        tokenizer=tokenizer,
        model=model,
        prefix_tokens=tokenizer.encode(RERANKER_PREFIX, add_special_tokens=False),
        suffix_tokens=tokenizer.encode(RERANKER_SUFFIX, add_special_tokens=False),
        true_token_id=tokenizer("yes", add_special_tokens=False).input_ids[0],
        false_token_id=tokenizer("no", add_special_tokens=False).input_ids[0],
    )


def _format_reranker_pair(*, instruction: str, query: str, document: str) -> str:
    return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"


def rerank_news_documents(
    *,
    query: str,
    documents: list[str],
    instruction: str = DEFAULT_NEWS_RERANKER_INSTRUCTION,
) -> list[float]:
    """Return yes-probabilities for one query against candidate cluster documents."""
    cleaned_query = query.strip()
    if not cleaned_query or not documents:
        return [0.0] * len(documents)

    import torch
    import torch.nn.functional as functional

    settings = get_settings()
    runtime = _get_news_reranker_runtime()
    tokenizer = runtime.tokenizer
    model = runtime.model
    prefix_tokens = runtime.prefix_tokens
    suffix_tokens = runtime.suffix_tokens
    true_token_id = runtime.true_token_id
    false_token_id = runtime.false_token_id
    max_body_length = (
        settings.news_list_reranker_max_length - len(prefix_tokens) - len(suffix_tokens)
    )
    pairs = [
        _format_reranker_pair(
            instruction=instruction,
            query=cleaned_query,
            document=document.strip(),
        )
        for document in documents
    ]
    scores: list[float] = []
    batch_size = settings.news_list_reranker_batch_size
    with torch.no_grad():
        for start in range(0, len(pairs), batch_size):
            batch_pairs = pairs[start : start + batch_size]
            inputs = cast(
                dict[str, list[list[int]]],
                tokenizer(
                    batch_pairs,
                    padding=False,
                    truncation="longest_first",
                    return_attention_mask=False,
                    max_length=max_body_length,
                ),
            )
            for index, token_ids in enumerate(inputs["input_ids"]):
                inputs["input_ids"][index] = [*prefix_tokens, *token_ids, *suffix_tokens]
            padded = cast(
                dict[str, torch.Tensor],
                tokenizer.pad(inputs, padding=True, return_tensors="pt"),
            )
            model_inputs: dict[str, torch.Tensor] = {}
            for key, value in padded.items():
                model_inputs[key] = value.to(runtime.device)
            batch_logits = model(**model_inputs).logits[:, -1, :]
            score_logits = torch.stack(
                [batch_logits[:, false_token_id], batch_logits[:, true_token_id]],
                dim=1,
            )
            batch_scores = functional.softmax(score_logits, dim=1)[:, 1]
            scores.extend(float(score) for score in batch_scores.tolist())
    return scores


def warm_news_reranker_model() -> None:
    """Warm the reranker model to avoid first-match latency."""
    _get_news_reranker_runtime()


def clear_news_reranker_cache() -> None:
    _get_news_reranker_runtime.cache_clear()
