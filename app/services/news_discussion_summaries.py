"""Discussion summary input, planning, and summarization helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import NewsItem, NewsItemDiscussion
from app.models.metadata.summaries import DiscussionSummary
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer

logger = get_logger(__name__)

MAX_SUMMARY_COMMENTS = 200
MAX_SUMMARY_LINKS = 50
DISCUSSION_SUMMARY_MATERIAL_COMMENT_THRESHOLD = 25
DISCUSSION_SUMMARY_MIN_INTERVAL = timedelta(hours=6)
DISCUSSION_SUMMARY_MAX_INTERVAL = timedelta(hours=24)
MAX_INCREMENTAL_SUMMARY_UPDATES = 4


@dataclass(frozen=True)
class SummaryPromptComment:
    comment_id: str
    author: str
    depth: int
    text: str
    fingerprint: str


@dataclass(frozen=True)
class SummaryPromptLink:
    url: str
    title: str | None
    comment_id: str | None


@dataclass(frozen=True)
class DiscussionSummaryInput:
    prompt: str
    input_sha256: str
    comment_count: int
    comment_fingerprints: dict[str, str]
    comments: list[SummaryPromptComment]
    links: list[SummaryPromptLink]


class DiscussionSummaryPlanMode(StrEnum):
    NONE = "none"
    TRACK_SUMMARIZED = "track_summarized"
    TRACK_SEEN = "track_seen"
    FULL = "full"
    MERGE = "merge"


@dataclass(frozen=True)
class DiscussionSummaryPlan:
    mode: DiscussionSummaryPlanMode
    changed_comments: tuple[SummaryPromptComment, ...] = ()


@dataclass(frozen=True)
class DiscussionSummaryExecution:
    summary: DiscussionSummary
    mode: DiscussionSummaryPlanMode


def build_discussion_summary_input(
    *,
    row: NewsItemDiscussion,
    raw_payload: dict[str, Any],
) -> DiscussionSummaryInput:
    raw_comments = raw_payload.get("comments")
    comment_items = raw_comments if isinstance(raw_comments, list) else []
    comments: list[SummaryPromptComment] = []
    for index, comment in enumerate(comment_items):
        if not isinstance(comment, dict):
            continue
        summary_comment = _build_summary_comment(comment=comment, index=index)
        if summary_comment is None:
            continue
        comments.append(summary_comment)
        if len(comments) >= MAX_SUMMARY_COMMENTS:
            break

    raw_links = raw_payload.get("links")
    link_items = raw_links if isinstance(raw_links, list) else []
    links: list[SummaryPromptLink] = []
    for link in link_items:
        if not isinstance(link, dict):
            continue
        summary_link = _build_summary_link(link)
        if summary_link is None:
            continue
        links.append(summary_link)
        if len(links) >= MAX_SUMMARY_LINKS:
            break

    prompt = _format_full_summary_prompt(row=row, comments=comments, links=links)
    return DiscussionSummaryInput(
        prompt=prompt,
        input_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        comment_count=len(comments),
        comment_fingerprints={comment.comment_id: comment.fingerprint for comment in comments},
        comments=comments,
        links=links,
    )


def plan_discussion_summary(
    *,
    row: NewsItemDiscussion,
    summary_input: DiscussionSummaryInput,
    previous_raw_sha: str | None,
    current_raw_sha: str,
    now: datetime,
) -> DiscussionSummaryPlan:
    if summary_input.comment_count == 0:
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.NONE,
        )

    if row.summary is None or row.summary_status != "completed":
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.FULL,
        )

    if row.summary_input_sha256 == summary_input.input_sha256:
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.NONE,
        )

    summary_age = now - row.summary_generated_at if row.summary_generated_at is not None else None
    if row.summary_seen_input_sha256 == summary_input.input_sha256 and (
        summary_age is None or summary_age < DISCUSSION_SUMMARY_MAX_INTERVAL
    ):
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.NONE,
        )

    if previous_raw_sha == current_raw_sha and row.summary_input_sha256 is None:
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.TRACK_SUMMARIZED,
        )

    previous_fingerprints = _normalize_summary_comment_fingerprints(
        row.summary_comment_fingerprints
    )
    if previous_fingerprints is None:
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.FULL,
        )

    changed_comments = _changed_summary_comments(
        previous_fingerprints=previous_fingerprints,
        summary_input=summary_input,
    )
    minimum_interval_elapsed = summary_age is None or summary_age >= DISCUSSION_SUMMARY_MIN_INTERVAL
    maximum_interval_elapsed = (
        summary_age is not None and summary_age >= DISCUSSION_SUMMARY_MAX_INTERVAL
    )
    materially_changed = len(changed_comments) > DISCUSSION_SUMMARY_MATERIAL_COMMENT_THRESHOLD
    force_stale_update = bool(changed_comments) and maximum_interval_elapsed
    if not force_stale_update and not (materially_changed and minimum_interval_elapsed):
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.TRACK_SEEN,
            changed_comments=changed_comments,
        )

    if (row.summary_incremental_update_count or 0) >= MAX_INCREMENTAL_SUMMARY_UPDATES:
        return DiscussionSummaryPlan(
            mode=DiscussionSummaryPlanMode.FULL,
            changed_comments=changed_comments,
        )

    return DiscussionSummaryPlan(
        mode=DiscussionSummaryPlanMode.MERGE,
        changed_comments=changed_comments,
    )


def execute_discussion_summary_plan(
    db: Session,
    *,
    row: NewsItemDiscussion,
    news_item: NewsItem,
    summary_input: DiscussionSummaryInput,
    plan: DiscussionSummaryPlan,
    summarizer: ContentSummarizer | None,
) -> DiscussionSummaryExecution:
    if plan.mode == DiscussionSummaryPlanMode.MERGE:
        try:
            return DiscussionSummaryExecution(
                summary=_merge_discussion_summary(
                    db,
                    row=row,
                    news_item=news_item,
                    summary_input=summary_input,
                    changed_comments=plan.changed_comments,
                    summarizer=summarizer,
                ),
                mode=DiscussionSummaryPlanMode.MERGE,
            )
        except Exception as exc:
            logger.warning(
                "Discussion summary merge failed; falling back to full summary",
                extra={
                    "component": "news_discussions",
                    "operation": "merge_summary.fallback_full",
                    "item_id": str(row.news_item_id),
                    "context_data": {
                        "news_item_discussion_id": row.id,
                        "platform": row.platform,
                        "error": str(exc),
                    },
                },
            )
            return DiscussionSummaryExecution(
                summary=_summarize_discussion(
                    db,
                    row=row,
                    news_item=news_item,
                    summary_input=summary_input,
                    summarizer=summarizer,
                ),
                mode=DiscussionSummaryPlanMode.FULL,
            )

    if plan.mode == DiscussionSummaryPlanMode.FULL:
        return DiscussionSummaryExecution(
            summary=_summarize_discussion(
                db,
                row=row,
                news_item=news_item,
                summary_input=summary_input,
                summarizer=summarizer,
            ),
            mode=DiscussionSummaryPlanMode.FULL,
        )

    raise ValueError(f"Discussion summary plan is not executable: {plan.mode.value}")


def store_summarized_summary_tracking(
    *,
    row: NewsItemDiscussion,
    summary_input: DiscussionSummaryInput,
    incremental_update_count: int,
) -> None:
    row.summary_input_sha256 = summary_input.input_sha256
    row.summary_comment_count = summary_input.comment_count
    row.summary_comment_fingerprints = dict(summary_input.comment_fingerprints)
    row.summary_incremental_update_count = incremental_update_count


def store_seen_summary_tracking(
    *,
    row: NewsItemDiscussion,
    summary_input: DiscussionSummaryInput,
) -> None:
    row.summary_seen_input_sha256 = summary_input.input_sha256
    row.summary_seen_comment_count = summary_input.comment_count
    row.summary_seen_comment_fingerprints = dict(summary_input.comment_fingerprints)


def _summarize_discussion(
    db: Session,
    *,
    row: NewsItemDiscussion,
    news_item: NewsItem,
    summary_input: DiscussionSummaryInput,
    summarizer: ContentSummarizer | None,
) -> DiscussionSummary:
    content_summarizer = summarizer or get_content_summarizer()
    summary = content_summarizer.summarize(
        summary_input.prompt,
        content_type="discussion_summary",
        title=row.title,
        content_id=f"news_item_discussion:{row.news_item_id}",
        db=db,
        usage_persist={
            "feature": "news_discussions",
            "operation": "news_discussions.summarize",
            "source": "discussion_scraper",
            "user_id": news_item.owner_user_id,
            "metadata": {
                "news_item_id": row.news_item_id,
                "news_item_discussion_id": row.id,
                "platform": row.platform,
                "summary_mode": "full",
                "summary_input_sha256": summary_input.input_sha256,
                "summary_comment_count": summary_input.comment_count,
            },
        },
    )
    if not isinstance(summary, DiscussionSummary):
        raise TypeError(
            "Discussion summarizer returned an invalid payload: "
            f"{type(summary).__name__ if summary is not None else 'None'}"
        )
    return summary


def _merge_discussion_summary(
    db: Session,
    *,
    row: NewsItemDiscussion,
    news_item: NewsItem,
    summary_input: DiscussionSummaryInput,
    changed_comments: Sequence[SummaryPromptComment],
    summarizer: ContentSummarizer | None,
) -> DiscussionSummary:
    content_summarizer = summarizer or get_content_summarizer()
    existing_summary = row.summary if isinstance(row.summary, dict) else {}
    summary = content_summarizer.summarize(
        _build_summary_merge_prompt(
            row=row,
            existing_summary=existing_summary,
            changed_comments=changed_comments,
            links=summary_input.links,
        ),
        content_type="discussion_summary_merge",
        title=row.title,
        content_id=f"news_item_discussion:{row.news_item_id}",
        db=db,
        usage_persist={
            "feature": "news_discussions",
            "operation": "news_discussions.merge_summary",
            "source": "discussion_scraper",
            "user_id": news_item.owner_user_id,
            "metadata": {
                "news_item_id": row.news_item_id,
                "news_item_discussion_id": row.id,
                "platform": row.platform,
                "summary_mode": "merge",
                "summary_input_sha256": summary_input.input_sha256,
                "summary_comment_count": summary_input.comment_count,
                "changed_comment_count": len(changed_comments),
            },
        },
    )
    if not isinstance(summary, DiscussionSummary):
        raise TypeError(
            "Discussion summary merge returned an invalid payload: "
            f"{type(summary).__name__ if summary is not None else 'None'}"
        )
    return summary


def _build_summary_comment(
    *,
    comment: dict[str, Any],
    index: int,
) -> SummaryPromptComment | None:
    text = _clean_string(comment.get("compact_text") or comment.get("text"))
    if not text:
        return None

    raw_comment_id = _clean_string(comment.get("comment_id"))
    fallback_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    comment_id = raw_comment_id or f"comment:{index}:{fallback_hash}"
    author = _clean_string(comment.get("author")) or "unknown"
    depth = _coerce_non_negative_int(comment.get("depth")) or 0
    fingerprint_payload = {
        "comment_id": comment_id,
        "author": author,
        "depth": depth,
        "text": text,
    }
    return SummaryPromptComment(
        comment_id=comment_id,
        author=author,
        depth=depth,
        text=text,
        fingerprint=_hash_json(fingerprint_payload),
    )


def _build_summary_link(link: dict[str, Any]) -> SummaryPromptLink | None:
    url = _clean_string(link.get("url"))
    if not url:
        return None
    return SummaryPromptLink(
        url=url,
        title=_clean_string(link.get("title")),
        comment_id=_clean_string(link.get("comment_id")),
    )


def _format_full_summary_prompt(
    *,
    row: NewsItemDiscussion,
    comments: list[SummaryPromptComment],
    links: list[SummaryPromptLink],
) -> str:
    lines = [
        f"Platform: {row.platform}",
        f"Discussion URL: {row.discussion_url or ''}",
    ]
    if row.title:
        lines.append(f"Thread title: {row.title}")

    lines.append("")
    lines.append("Comments:")
    lines.extend(_format_comment_line(comment) for comment in comments)

    if links:
        lines.append("")
        lines.append("Extracted links:")
        lines.extend(_format_link_line(link) for link in links)

    return "\n".join(lines)


def _build_summary_merge_prompt(
    *,
    row: NewsItemDiscussion,
    existing_summary: dict[str, Any],
    changed_comments: Sequence[SummaryPromptComment],
    links: Sequence[SummaryPromptLink],
) -> str:
    changed_comment_ids = {comment.comment_id for comment in changed_comments}
    changed_links = [
        link
        for link in links
        if link.comment_id is not None and link.comment_id in changed_comment_ids
    ]
    lines = [
        f"Platform: {row.platform}",
        f"Discussion URL: {row.discussion_url or ''}",
    ]
    if row.title:
        lines.append(f"Thread title: {row.title}")

    lines.extend(
        [
            "",
            "Existing summary JSON:",
            json.dumps(
                _summary_for_merge_payload(existing_summary),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "",
            "New or changed comments:",
        ]
    )
    lines.extend(_format_comment_line(comment) for comment in changed_comments)

    if changed_links:
        lines.append("")
        lines.append("New or changed links:")
        lines.extend(_format_link_line(link) for link in changed_links)

    return "\n".join(lines)


def _summary_for_merge_payload(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    allowed_keys = {
        "overview",
        "topics",
        "notable_links",
        "representative_comments",
        "external_discussion_url",
    }
    return {key: value for key, value in summary.items() if key in allowed_keys}


def _changed_summary_comments(
    *,
    previous_fingerprints: dict[str, str],
    summary_input: DiscussionSummaryInput,
) -> tuple[SummaryPromptComment, ...]:
    return tuple(
        comment
        for comment in summary_input.comments
        if previous_fingerprints.get(comment.comment_id) != comment.fingerprint
    )


def _normalize_summary_comment_fingerprints(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    fingerprints: dict[str, str] = {}
    for raw_comment_id, raw_fingerprint in value.items():
        comment_id = _clean_string(raw_comment_id)
        fingerprint = _clean_string(raw_fingerprint)
        if comment_id and fingerprint:
            fingerprints[comment_id] = fingerprint
    return fingerprints


def _format_comment_line(comment: SummaryPromptComment) -> str:
    return f"- [{comment.comment_id}] {comment.author} depth={comment.depth}: {comment.text}"


def _format_link_line(link: SummaryPromptLink) -> str:
    label = f" ({link.title})" if link.title else ""
    source = f" from comment {link.comment_id}" if link.comment_id else ""
    return f"- {link.url}{label}{source}"


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None
