"""Shared helpers for admin and fixture-backed eval harnesses."""

from __future__ import annotations

from typing import Any


def resolve_summary_prompt_settings(
    content_type: str,
    *,
    longform_template: str,
) -> tuple[str, int, int]:
    """Resolve summary prompt settings shared by eval surfaces."""
    if content_type == "news":
        return "news", 4, 0
    if longform_template == "interleaved_v2":
        return "interleaved", 8, 8
    if longform_template == "structured_v1":
        return "structured", 12, 8
    if longform_template == "editorial_narrative_v1":
        return "editorial_narrative", 10, 4
    return "long_bullets", 30, 3


def extract_result_payload(result: Any) -> dict[str, Any]:
    """Normalize a current pydantic-ai result into a JSON-shaped mapping."""
    output = getattr(result, "output", None)
    if output is None:
        raise ValueError("Model result did not include output payload")
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json", exclude_none=True)
    if isinstance(output, dict):
        return output
    raise ValueError("Model result payload is not JSON serializable")


def extract_result_usage(result: Any) -> dict[str, int | None]:
    """Normalize token usage from a current pydantic-ai result."""
    try:
        usage = result.usage
    except Exception:  # noqa: BLE001
        usage = None

    if not usage:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    input_tokens = _coerce_int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
    )
    output_tokens = _coerce_int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
    )
    total_tokens = _coerce_int(getattr(usage, "total_tokens", None))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def build_news_context(metadata: dict[str, Any]) -> str:
    """Build aggregator context shared by news eval and prompt-debug inputs."""
    article = metadata.get("article")
    article_data = article if isinstance(article, dict) else {}
    aggregator = metadata.get("aggregator")
    aggregator_data = aggregator if isinstance(aggregator, dict) else {}
    lines: list[str] = []

    article_title = _extract_str(article_data.get("title"))
    article_url = _extract_str(article_data.get("url"))
    if article_title:
        lines.append(f"Article Title: {article_title}")
    if article_url:
        lines.append(f"Article URL: {article_url}")

    if aggregator_data:
        name = _extract_str(aggregator_data.get("name")) or _extract_str(metadata.get("platform"))
        aggregator_title = _extract_str(aggregator_data.get("title"))
        aggregator_url = _extract_str(metadata.get("discussion_url")) or _extract_str(
            aggregator_data.get("url")
        )
        author = _extract_str(aggregator_data.get("author"))

        context_bits: list[str] = []
        if name:
            context_bits.append(name)
        if author:
            context_bits.append(f"by {author}")
        if aggregator_title and aggregator_title != article_title:
            lines.append(f"Aggregator Headline: {aggregator_title}")
        if context_bits:
            lines.append("Aggregator Context: " + ", ".join(context_bits))
        if aggregator_url:
            lines.append(f"Discussion URL: {aggregator_url}")

        extra = aggregator_data.get("metadata")
        if isinstance(extra, dict):
            highlights = [
                f"{field}={extra[field]}"
                for field in ("score", "comments_count", "likes", "retweets", "replies")
                if extra.get(field) is not None
            ]
            if highlights:
                lines.append("Signals: " + ", ".join(highlights))

    summary_payload = metadata.get("summary")
    excerpt = _extract_str(metadata.get("excerpt"))
    if not excerpt and isinstance(summary_payload, dict):
        excerpt = (
            _extract_str(summary_payload.get("overview"))
            or _extract_str(summary_payload.get("summary"))
            or _extract_str(summary_payload.get("hook"))
            or _extract_str(summary_payload.get("takeaway"))
        )
    if excerpt:
        lines.append(f"Aggregator Summary: {excerpt}")
    return "\n".join(lines)


def _extract_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_int(value: object | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
