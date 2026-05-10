"""Helpers for summarization payload extraction and fingerprinting."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.contracts import ContentType
from app.utils.title_utils import clean_title

WHITESPACE_PATTERN = re.compile(r"\s+")
VIDEO_TRANSCRIPT_MARKER = "[Embedded video transcript]"


def build_news_context(metadata: dict[str, Any]) -> str:
    """Build aggregator context string for news items."""
    article = metadata.get("article", {})
    aggregator = metadata.get("aggregator", {})
    lines: list[str] = []

    article_title = clean_title(article.get("title")) or ""
    article_url = article.get("url") or ""

    if article_title:
        lines.append(f"Article Title: {article_title}")
    if article_url:
        lines.append(f"Article URL: {article_url}")

    if aggregator:
        name = aggregator.get("name") or metadata.get("platform")
        agg_title = clean_title(aggregator.get("title"))
        agg_url = metadata.get("discussion_url") or aggregator.get("url")
        author = aggregator.get("author")

        context_bits = []
        if name:
            context_bits.append(name)
        if author:
            context_bits.append(f"by {author}")
        if agg_title and agg_title != article_title:
            lines.append(f"Aggregator Headline: {agg_title}")
        if context_bits:
            lines.append("Aggregator Context: " + ", ".join(context_bits))
        if agg_url:
            lines.append(f"Discussion URL: {agg_url}")

        extra = aggregator.get("metadata") or {}
        highlights = []
        for field in ["score", "comments_count", "likes", "retweets", "replies"]:
            value = extra.get(field)
            if value is not None:
                highlights.append(f"{field}={value}")
        if highlights:
            lines.append("Signals: " + ", ".join(highlights))

    summary_payload = metadata.get("summary") if isinstance(metadata, dict) else {}
    excerpt = metadata.get("excerpt")
    if not excerpt and isinstance(summary_payload, dict):
        excerpt = (
            summary_payload.get("overview")
            or summary_payload.get("summary")
            or summary_payload.get("hook")
            or summary_payload.get("takeaway")
        )
    if excerpt:
        lines.append(f"Aggregator Summary: {excerpt}")

    return "\n".join(lines)


def build_summarization_payload(
    content_type: str | ContentType,
    metadata: dict[str, Any],
    *,
    source_text: str | None = None,
) -> str:
    """Build the exact payload that summarization should consume."""
    resolved_type = (
        content_type.value if isinstance(content_type, ContentType) else str(content_type)
    )

    def _append_video_transcript(text: str) -> str:
        video_transcript = metadata.get("video_transcript")
        if not isinstance(video_transcript, str) or not video_transcript.strip():
            return text
        transcript_block = f"{VIDEO_TRANSCRIPT_MARKER}\n{video_transcript.strip()}"
        return f"{text}\n\n{transcript_block}" if text.strip() else transcript_block

    if resolved_type == ContentType.ARTICLE.value:
        article_content = str(
            source_text or metadata.get("content") or metadata.get("content_to_summarize") or ""
        )
        return _append_video_transcript(article_content)

    if resolved_type == ContentType.NEWS.value:
        article_content = str(
            source_text or metadata.get("content") or metadata.get("content_to_summarize") or ""
        )
        article_content = _append_video_transcript(article_content)
        aggregator_context = build_news_context(metadata)
        if aggregator_context and article_content:
            return f"Context:\n{aggregator_context}\n\nArticle Content:\n{article_content}"
        return article_content

    if resolved_type == ContentType.PODCAST.value:
        return str(
            source_text or metadata.get("transcript") or metadata.get("content_to_summarize") or ""
        )

    return ""


def normalize_summarization_payload(payload: str) -> str:
    """Normalize payload text for stable fingerprinting."""
    return WHITESPACE_PATTERN.sub(" ", payload).strip()


def compute_summarization_input_fingerprint(
    content_type: str | ContentType,
    payload: str,
) -> str:
    """Return a stable fingerprint for the summarization input payload."""
    resolved_type = (
        content_type.value if isinstance(content_type, ContentType) else str(content_type)
    )
    normalized_payload = normalize_summarization_payload(payload)
    hash_source = f"{resolved_type}\n{normalized_payload}"
    return hashlib.sha256(hash_source.encode("utf-8")).hexdigest()
