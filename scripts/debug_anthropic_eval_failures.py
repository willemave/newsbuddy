#!/usr/bin/env python3
"""Debug Anthropic eval failures for specific content rows."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Literal

# Add project root to import path when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import get_db, init_db
from app.core.logging import get_logger, setup_logging
from app.models.schema import Content
from app.services.admin_eval import (
    MAX_EVAL_INPUT_CHARS,
    build_eval_source_payload,
    select_eval_samples,
)
from app.services.llm_agents import get_basic_agent
from app.services.llm_prompts import generate_summary_prompt
from app.services.llm_summarization import resolve_summarization_output_type

logger = get_logger(__name__)

EvalContentType = Literal["article", "podcast", "news"]
LongformTemplate = Literal[
    "long_bullets_v1",
    "interleaved_v2",
    "structured_v1",
    "editorial_narrative_v1",
]
PromptType = Literal[
    "structured",
    "interleaved",
    "long_bullets",
    "news",
    "editorial_narrative",
]
ESTIMATED_CHARS_PER_TOKEN = 4


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed command-line namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Replay Anthropic summarization calls for specific content rows and print "
            "per-ID failure diagnostics."
        )
    )
    parser.add_argument(
        "--content-ids",
        type=str,
        default=None,
        help="Comma-separated content IDs (e.g. 123,456,789).",
    )
    parser.add_argument(
        "--content-types",
        type=str,
        default="article,podcast,news",
        help="Used only when --content-ids is omitted.",
    )
    parser.add_argument("--recent-pool-size", type=int, default=200)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--longform-template",
        type=str,
        choices=["long_bullets_v1", "interleaved_v2", "structured_v1", "editorial_narrative_v1"],
        default="editorial_narrative_v1",
    )
    parser.add_argument(
        "--model-spec",
        type=str,
        default="anthropic:claude-haiku-4-5-20251001",
        help="Anthropic model spec to test.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Extra script-level retries per content row after a failed call.",
    )
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=MAX_EVAL_INPUT_CHARS,
        help="Clip source text before prompt construction.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Optional path to write JSON results.",
    )
    parser.add_argument(
        "--show-success-output",
        action="store_true",
        help="Include returned summary payload for successful rows.",
    )
    return parser.parse_args()


def parse_content_ids(raw_ids: str | None) -> list[int]:
    """Parse comma-separated IDs into integers.

    Args:
        raw_ids: Raw comma-separated ID string.

    Returns:
        List of content IDs.
    """
    if not raw_ids:
        return []

    parsed: list[int] = []
    for chunk in raw_ids.split(","):
        value = chunk.strip()
        if not value:
            continue
        parsed.append(int(value))
    return parsed


def parse_content_types(raw_types: str) -> list[EvalContentType]:
    """Parse selected content types.

    Args:
        raw_types: Comma-separated content type names.

    Returns:
        Valid content type list.

    Raises:
        ValueError: When any type is invalid.
    """
    allowed = {"article", "podcast", "news"}
    parsed = [value.strip() for value in raw_types.split(",") if value.strip()]
    if not parsed:
        raise ValueError("At least one content type is required")

    invalid = [value for value in parsed if value not in allowed]
    if invalid:
        raise ValueError(f"Invalid content types: {', '.join(invalid)}")

    deduped = list(dict.fromkeys(parsed))
    return deduped  # type: ignore[return-value]


def resolve_prompt_settings(
    content_type: EvalContentType,
    longform_template: LongformTemplate,
) -> tuple[PromptType, int, int]:
    """Resolve prompt type and limits using eval rules.

    Args:
        content_type: Current content row type.
        longform_template: Long-form template selector.

    Returns:
        Tuple of (prompt_type, max_bullet_points, max_quotes).
    """
    if content_type == "news":
        return "news", 4, 0

    if longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if longform_template == "structured_v1":
        return "structured", 12, 8
    if longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def clip_eval_input(text: str, max_input_chars: int) -> str:
    """Clip very large content while preserving start/end context.

    Args:
        text: Source content string.
        max_input_chars: Maximum allowed characters.

    Returns:
        Possibly clipped content.
    """
    if len(text) <= max_input_chars:
        return text

    marker = "\n\n[... CONTENT TRUNCATED FOR DEBUG ...]\n\n"
    remaining = max_input_chars - len(marker)
    if remaining <= 0:
        return text[:max_input_chars]

    head_size = remaining // 2
    tail_size = remaining - head_size
    return f"{text[:head_size].rstrip()}{marker}{text[-tail_size:].lstrip()}"


def estimate_tokens_from_chars(char_count: int) -> int:
    """Estimate token count from character length.

    Args:
        char_count: Character count.

    Returns:
        Approximate token count.
    """
    if char_count <= 0:
        return 0
    return math.ceil(char_count / ESTIMATED_CHARS_PER_TOKEN)


def extract_usage(result: Any) -> dict[str, int | None]:
    """Extract token usage from pydantic-ai result object.

    Args:
        result: Pydantic-ai result object.

    Returns:
        Usage dict with token counts.
    """
    try:
        usage = result.usage()
    except Exception:  # noqa: BLE001
        usage = None

    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    def _to_int(value: object | None) -> int | None:
        if value is None:
            return None
        if not isinstance(value, (int, float, str, bytes, bytearray)):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    input_tokens = _to_int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
    )
    output_tokens = _to_int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
    )
    total_tokens = _to_int(getattr(usage, "total_tokens", None))

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def classify_error(exc: Exception) -> str:
    """Classify an exception into coarse buckets.

    Args:
        exc: Raised exception.

    Returns:
        Error category string.
    """
    message = str(exc).lower()
    name = exc.__class__.__name__.lower()

    if "timeout" in message or "timeout" in name:
        return "timeout"
    if "rate" in message and "limit" in message:
        return "rate_limit"
    if "status_code" in message and "429" in message:
        return "rate_limit"
    if "status_code" in message and any(code in message for code in ["400", "401", "403", "404"]):
        return "client_error"
    if "status_code" in message and any(code in message for code in ["500", "502", "503", "504"]):
        return "server_error"
    return "unknown"


def build_run_sources(
    *,
    content_ids: list[int],
    content_types: list[EvalContentType],
    recent_pool_size: int,
    sample_size: int,
    seed: int | None,
) -> tuple[list[Any], list[int]]:
    """Build source payloads either from explicit IDs or random sampling.

    Args:
        content_ids: Explicit content IDs.
        content_types: Content types used for sampling mode.
        recent_pool_size: Sampling pool size.
        sample_size: Number of sampled rows.
        seed: Optional random seed.

    Returns:
        Tuple of (source payload list, missing_id list).
    """
    with get_db() as db:
        if content_ids:
            rows = db.query(Content).filter(Content.id.in_(content_ids)).all()
            row_by_id = {row.id: row for row in rows}

            sources: list[Any] = []
            missing_ids: list[int] = []
            for content_id in content_ids:
                row = row_by_id.get(content_id)
                if row is None:
                    missing_ids.append(content_id)
                    continue

                payload = build_eval_source_payload(row)
                if payload is None:
                    logger.warning(
                        "Skipping content_id=%s because no usable input text was found",
                        content_id,
                    )
                    continue
                sources.append(payload)
            return sources, missing_ids

        sample_map = select_eval_samples(
            db,
            content_types=content_types,
            recent_pool_size=recent_pool_size,
            sample_size=sample_size,
            seed=seed,
        )

    flattened: list[Any] = []
    for content_type in content_types:
        flattened.extend(sample_map.get(content_type, []))
    return flattened, []


def run_probe_for_source(
    *,
    source: Any,
    model_spec: str,
    timeout_seconds: int,
    retries: int,
    longform_template: LongformTemplate,
    max_input_chars: int,
    show_success_output: bool,
) -> dict[str, Any]:
    """Run one Anthropic probe call for a source row.

    Args:
        source: EvalSourcePayload-compatible object.
        model_spec: Full model spec.
        timeout_seconds: Request timeout in seconds.
        retries: Script-level retries after a failed attempt.
        longform_template: Long-form template selector.
        max_input_chars: Input clipping limit.
        show_success_output: Whether to include model output payload on success.

    Returns:
        Diagnostic payload for the row.
    """
    prompt_type, max_bullet_points, max_quotes = resolve_prompt_settings(
        source.content_type,
        longform_template,
    )
    system_prompt, user_template = generate_summary_prompt(
        prompt_type,
        max_bullet_points=max_bullet_points,
        max_quotes=max_quotes,
    )

    clipped = clip_eval_input(source.input_text, max_input_chars)
    title_prefix = f"Title: {source.source_title}\n\n" if source.source_title else ""
    user_message = user_template.format(content=f"{title_prefix}{clipped}")

    request_chars = len(system_prompt) + len(user_message)
    request_tokens_estimate = estimate_tokens_from_chars(request_chars)

    output_type = resolve_summarization_output_type(prompt_type)
    agent = get_basic_agent(model_spec, output_type, system_prompt)

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        started = time.perf_counter()
        try:
            result = agent.run_sync(
                user_message,
                model_settings={"timeout": timeout_seconds},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = extract_usage(result)
            payload = getattr(result, "output", None) or getattr(result, "data", None)
            output_chars = len(json.dumps(payload, ensure_ascii=False, default=str))

            logger.info(
                "SUCCESS content_id=%s content_type=%s model=%s attempt=%s latency_ms=%s "
                "req_chars=%s req_tokens_est=%s req_tokens_actual=%s out_chars=%s",
                source.content_id,
                source.content_type,
                model_spec,
                attempt,
                latency_ms,
                request_chars,
                request_tokens_estimate,
                usage.get("input_tokens"),
                output_chars,
            )

            return {
                "content_id": source.content_id,
                "content_type": source.content_type,
                "url": source.url,
                "title": source.source_title,
                "created_at": source.created_at,
                "status": "ok",
                "attempts": attempt,
                "prompt_type": prompt_type,
                "timeout_seconds": timeout_seconds,
                "input_chars": source.input_chars,
                "clipped_input_chars": len(clipped),
                "request_chars": request_chars,
                "request_tokens_estimate": request_tokens_estimate,
                "usage": usage,
                "latency_ms": latency_ms,
                "output_chars": output_chars,
                "error_type": None,
                "error": None,
                "output": payload if show_success_output else None,
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            error_type = classify_error(exc)
            last_error = exc
            logger.error(
                "FAIL content_id=%s content_type=%s model=%s attempt=%s/%s latency_ms=%s "
                "req_chars=%s req_tokens_est=%s error_type=%s error=%s",
                source.content_id,
                source.content_type,
                model_spec,
                attempt,
                retries + 1,
                latency_ms,
                request_chars,
                request_tokens_estimate,
                error_type,
                str(exc),
            )

    assert last_error is not None
    return {
        "content_id": source.content_id,
        "content_type": source.content_type,
        "url": source.url,
        "title": source.source_title,
        "created_at": source.created_at,
        "status": "error",
        "attempts": retries + 1,
        "prompt_type": prompt_type,
        "timeout_seconds": timeout_seconds,
        "input_chars": source.input_chars,
        "clipped_input_chars": len(clipped),
        "request_chars": request_chars,
        "request_tokens_estimate": request_tokens_estimate,
        "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
        "latency_ms": None,
        "output_chars": 0,
        "error_type": classify_error(last_error),
        "error": str(last_error),
        "output": None,
    }


def run() -> int:
    """Run the Anthropic eval failure probe.

    Returns:
        Process exit code.
    """
    args = parse_args()
    setup_logging(name="anthropic_eval_debug", level="INFO")
    init_db()

    try:
        content_ids = parse_content_ids(args.content_ids)
        content_types = parse_content_types(args.content_types)
    except ValueError as exc:
        logger.error("Invalid arguments: %s", str(exc))
        return 2

    if not args.model_spec.startswith("anthropic:"):
        logger.error("--model-spec must be an anthropic model spec (anthropic:...) for this script")
        return 2

    sources, missing_ids = build_run_sources(
        content_ids=content_ids,
        content_types=content_types,
        recent_pool_size=args.recent_pool_size,
        sample_size=args.sample_size,
        seed=args.seed,
    )

    if missing_ids:
        logger.warning("Missing content IDs: %s", ", ".join(str(value) for value in missing_ids))

    if not sources:
        logger.error("No valid sources to probe")
        return 1

    logger.info(
        "Running Anthropic probe for %s rows with model=%s timeout=%ss retries=%s",
        len(sources),
        args.model_spec,
        args.timeout_seconds,
        args.retries,
    )

    results: list[dict[str, Any]] = []
    for source in sources:
        results.append(
            run_probe_for_source(
                source=source,
                model_spec=args.model_spec,
                timeout_seconds=args.timeout_seconds,
                retries=args.retries,
                longform_template=args.longform_template,
                max_input_chars=args.max_input_chars,
                show_success_output=args.show_success_output,
            )
        )

    failed = [row for row in results if row["status"] == "error"]
    timed_out = [row for row in failed if row.get("error_type") == "timeout"]

    logger.info(
        "Probe complete: total=%s ok=%s failed=%s timeouts=%s",
        len(results),
        len(results) - len(failed),
        len(failed),
        len(timed_out),
    )

    if failed:
        logger.info("Failed content IDs: %s", ", ".join(str(row["content_id"]) for row in failed))

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "config": {
                        "content_ids": content_ids,
                        "content_types": content_types,
                        "model_spec": args.model_spec,
                        "timeout_seconds": args.timeout_seconds,
                        "retries": args.retries,
                        "sample_size": args.sample_size,
                        "recent_pool_size": args.recent_pool_size,
                        "seed": args.seed,
                        "longform_template": args.longform_template,
                    },
                    "missing_ids": missing_ids,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote diagnostic JSON: %s", str(output_path))

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(run())
