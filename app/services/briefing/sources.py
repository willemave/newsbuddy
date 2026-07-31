from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.core.settings import get_settings
from app.models.contracts import (
    ContentClassification,
    ContentStatus,
    ContentType,
    NewsItemStatus,
)
from app.models.db import (
    Content,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemDiscussion,
    NewsItemReadStatus,
)
from app.models.metadata.summaries import DiscussionSummary
from app.services.briefing.source_keys import build_source_key, parse_source_key
from app.services.news_feed import build_visible_news_item_filter, list_unread_visible_news_items
from app.utils.image_urls import (
    build_content_image_url,
    build_news_thumbnail_url,
    build_thumbnail_url,
)
from app.utils.news_titles import resolve_news_display_title
from app.utils.summary_utils import extract_short_summary

BRIEFING_CONTEXT_MAX_CHARS = 2400
BRIEFING_SOURCE_EXCERPT_MAX_CHARS = 900
BRIEFING_CONTEXT_LIST_MAX_ITEMS = 6
TERMINAL_DISCUSSION_REFRESH_STATUSES = frozenset({"gone", "unsupported"})


@dataclass(frozen=True)
class BriefingSourceDiscussion:
    platform: str
    comment_count: int | None
    summary_status: str
    overview: str | None
    top_comment_author: str | None
    top_comment_text: str | None
    external_url: str | None
    updated_at: datetime | None

    def dto(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "comment_count": self.comment_count,
            "summary_status": self.summary_status,
            "overview": self.overview,
            "top_comment_author": self.top_comment_author,
            "top_comment_text": self.top_comment_text,
            "external_url": self.external_url,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class BriefingSource:
    source_key: str
    kind: str
    id: int
    tier: str
    lens_key: str | None
    title: str
    summary: str | None
    key_points: list[str]
    url: str | None
    image_url: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    content_type: ContentType | None
    topic_slug: str | None = None
    topic_title: str | None = None
    briefing_context: str | None = None
    discussion: BriefingSourceDiscussion | None = None

    def dto(self, *, read: bool) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "key_points": self.key_points,
            "url": self.url,
            "image_url": self.image_url,
            "thumbnail_url": self.thumbnail_url,
            "published_at": self.published_at,
            "content_type": self.content_type,
            "read": read,
            "discussion": self.discussion.dto() if self.discussion else None,
        }


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _key_points_from_metadata(metadata: dict[str, Any]) -> list[str]:
    candidates = metadata.get("key_points")
    if candidates is None and isinstance(metadata.get("summary"), dict):
        candidates = metadata["summary"].get("key_points") or metadata["summary"].get("points")
    if not isinstance(candidates, list):
        return []
    points: list[str] = []
    for item in candidates:
        text = item.get("text") if isinstance(item, dict) else item
        cleaned = _clean_string(text)
        if cleaned:
            points.append(cleaned)
    return points[:6]


def _content_is_read_clause(*, user_id: int):
    return exists(
        select(ContentReadStatus.id).where(
            ContentReadStatus.user_id == user_id,
            ContentReadStatus.content_id == Content.id,
        )
    )


def _briefing_longform_classification_clause():
    return or_(
        Content.classification.is_(None),
        Content.classification != ContentClassification.SKIP.value,
    )


def list_unread_longform_sources(
    db: Session,
    *,
    user_id: int,
    content_type: ContentType,
    limit: int | None,
) -> list[BriefingSource]:
    """Return unread completed non-skipped content rows for one long-form tier."""

    read_clause = _content_is_read_clause(user_id=user_id)
    query = (
        db.query(Content)
        .join(
            ContentStatusEntry,
            ContentStatusEntry.content_id == Content.id,
        )
        .filter(ContentStatusEntry.user_id == user_id)
        .filter(ContentStatusEntry.status == "inbox")
        .filter(Content.status == ContentStatus.COMPLETED.value)
        .filter(_briefing_longform_classification_clause())
        .filter(Content.content_type == content_type.value)
        .filter(~read_clause)
        .order_by(
            Content.publication_date.desc().nullslast(),
            Content.created_at.desc(),
            Content.id.desc(),
        )
    )
    if limit is not None:
        query = query.limit(max(1, limit))
    rows = query.all()
    return [_source_from_content(row) for row in rows]


def list_unread_news_sources(
    db: Session,
    *,
    user_id: int,
    limit: int | None,
) -> list[BriefingSource]:
    rows, _total = list_unread_visible_news_items(db, user_id=user_id, limit=limit)
    return [_source_from_news_item(item) for item in rows]


def list_bootstrap_sources(
    db: Session,
    *,
    user_id: int,
    audio_limit: int | None,
    longform_limit: int | None,
    news_limit: int | None,
) -> list[BriefingSource]:
    sources: list[BriefingSource] = []
    sources.extend(
        list_unread_longform_sources(
            db,
            user_id=user_id,
            content_type=ContentType.PODCAST,
            limit=audio_limit,
        )
    )
    sources.extend(
        list_unread_longform_sources(
            db,
            user_id=user_id,
            content_type=ContentType.ARTICLE,
            limit=longform_limit,
        )
    )
    sources.extend(list_unread_news_sources(db, user_id=user_id, limit=news_limit))
    return sources


def sources_for_keys(
    db: Session,
    *,
    user_id: int,
    source_keys: list[str],
    include_briefing_context: bool = True,
    require_current_news_representative: bool = False,
) -> dict[str, BriefingSource]:
    """Resolve source keys, optionally enforcing current feed eligibility for news.

    Historical Briefing segments keep immutable source keys, so presentation must
    still resolve a row that later became a duplicate. Pending composition opts
    into the stricter representative-only behavior instead.
    """
    parsed = [parse_source_key(key) for key in source_keys]
    content_ids = [key.source_id for key in parsed if key and key.kind == "content"]
    news_ids = [key.source_id for key in parsed if key and key.kind == "news"]

    found: dict[str, BriefingSource] = {}
    if content_ids:
        content_rows = (
            db.query(Content)
            .options(
                load_only(
                    Content.id,
                    Content.content_type,
                    Content.url,
                    Content.source_url,
                    Content.title,
                    Content.content_metadata,
                    Content.created_at,
                    Content.publication_date,
                )
            )
            .join(ContentStatusEntry, ContentStatusEntry.content_id == Content.id)
            .filter(ContentStatusEntry.user_id == user_id)
            .filter(Content.id.in_(content_ids))
            .all()
        )
        for content_row in content_rows:
            source = _source_from_content(
                content_row,
                include_briefing_context=include_briefing_context,
            )
            found[source.source_key] = source

    if news_ids:
        visible = build_visible_news_item_filter(db, user_id=user_id)
        news_query = (
            db.query(NewsItem)
            .options(
                load_only(
                    NewsItem.id,
                    NewsItem.raw_metadata,
                    NewsItem.summary_text,
                    NewsItem.summary_key_points,
                    NewsItem.article_url,
                    NewsItem.canonical_story_url,
                    NewsItem.canonical_item_url,
                    NewsItem.published_at,
                    NewsItem.processed_at,
                    NewsItem.ingested_at,
                    NewsItem.created_at,
                )
            )
            .filter(NewsItem.id.in_(news_ids))
            .filter(visible)
        )
        if require_current_news_representative:
            news_query = news_query.filter(
                NewsItem.status == NewsItemStatus.READY.value,
                NewsItem.representative_news_item_id.is_(None),
            )
        news_rows = news_query.all()
        discussions_by_news_id = _briefing_discussions_for_news_ids(db, news_ids=news_ids)
        for news_row in news_rows:
            source = _source_from_news_item(news_row)
            source = replace(source, discussion=discussions_by_news_id.get(int(news_row.id or 0)))
            found[source.source_key] = source
    return found


def _briefing_discussions_for_news_ids(
    db: Session,
    *,
    news_ids: list[int],
) -> dict[int, BriefingSourceDiscussion]:
    if not get_settings().briefing_discussion_strip_enabled:
        return {}

    unique_ids = sorted({int(news_id) for news_id in news_ids})
    if not unique_ids:
        return {}

    rows = (
        db.query(NewsItemDiscussion).filter(NewsItemDiscussion.news_item_id.in_(unique_ids)).all()
    )
    discussions: dict[int, BriefingSourceDiscussion] = {}
    for row in rows:
        discussion = _briefing_discussion_from_row(row)
        if discussion is None or row.news_item_id is None:
            continue
        discussions[int(row.news_item_id)] = discussion
    return discussions


def _briefing_discussion_from_row(
    row: NewsItemDiscussion,
) -> BriefingSourceDiscussion | None:
    if row.last_refresh_status in TERMINAL_DISCUSSION_REFRESH_STATUSES:
        return None
    if row.summary is None and not (row.comment_count and row.comment_count > 0):
        return None

    summary = _parse_discussion_summary(row.summary)
    status = _briefing_discussion_status(row, summary=summary)
    overview = None
    top_comment_author = None
    top_comment_text = None
    external_url = row.discussion_url
    if status == "completed" and summary is not None:
        overview = _truncate_discussion_overview(
            summary.overview,
            max_chars=get_settings().briefing_discussion_overview_max_chars,
        )
        if summary.representative_comments:
            top_comment = summary.representative_comments[0]
            top_comment_author = top_comment.author
            top_comment_text = top_comment.text
        external_url = summary.external_discussion_url or external_url

    return BriefingSourceDiscussion(
        platform=str(row.platform),
        comment_count=row.comment_count,
        summary_status=status,
        overview=overview,
        top_comment_author=top_comment_author,
        top_comment_text=top_comment_text,
        external_url=external_url,
        updated_at=(
            row.summary_generated_at or row.last_comments_fetched_at or row.last_count_checked_at
        ),
    )


def _parse_discussion_summary(value: Any) -> DiscussionSummary | None:
    if not isinstance(value, dict):
        return None
    try:
        return DiscussionSummary.model_validate(value)
    except ValidationError:
        return None


def _briefing_discussion_status(
    row: NewsItemDiscussion,
    *,
    summary: DiscussionSummary | None,
) -> str:
    if row.summary_status == "completed" and summary is not None:
        return "completed"
    if row.summary_status == "failed" or row.last_refresh_status == "failed":
        return "failed"
    return "not_ready"


def _truncate_discussion_overview(value: str, *, max_chars: int) -> str:
    cleaned = " ".join(value.split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]

    hard_limit = max_chars - 3
    sentence_end = max(
        cleaned.rfind(".", 0, hard_limit + 1),
        cleaned.rfind("!", 0, hard_limit + 1),
        cleaned.rfind("?", 0, hard_limit + 1),
    )
    if sentence_end >= max(24, int(max_chars * 0.45)):
        return cleaned[: sentence_end + 1]

    truncated = cleaned[:hard_limit].rsplit(" ", 1)[0].strip()
    if not truncated:
        truncated = cleaned[:hard_limit].strip()
    return (truncated.rstrip(".,;:") + "...")[:max_chars]


def read_source_keys(db: Session, *, user_id: int) -> set[str]:
    content_ids = db.execute(
        select(ContentReadStatus.content_id).where(ContentReadStatus.user_id == user_id)
    ).scalars()
    exact_read_news_ids = {
        int(news_id)
        for news_id in db.execute(
            select(NewsItemReadStatus.news_item_id).where(NewsItemReadStatus.user_id == user_id)
        ).scalars()
        if news_id is not None
    }
    keys = {
        build_source_key("content", int(content_id))
        for content_id in content_ids
        if content_id is not None
    }
    read_news_ids = exact_read_news_ids | _news_cluster_member_ids(
        db,
        news_item_ids=exact_read_news_ids,
    )
    keys.update(build_source_key("news", news_id) for news_id in read_news_ids)
    return keys


def read_source_keys_for(
    db: Session,
    *,
    user_id: int,
    source_keys: list[str],
) -> set[str]:
    """Return requested keys whose underlying content or news cluster is read."""
    parsed = [parse_source_key(key) for key in source_keys]
    content_ids = sorted({key.source_id for key in parsed if key and key.kind == "content"})
    news_ids = sorted({key.source_id for key in parsed if key and key.kind == "news"})

    keys: set[str] = set()
    if content_ids:
        read_content_ids = db.execute(
            select(ContentReadStatus.content_id).where(
                ContentReadStatus.user_id == user_id,
                ContentReadStatus.content_id.in_(content_ids),
            )
        ).scalars()
        keys.update(
            build_source_key("content", int(content_id))
            for content_id in read_content_ids
            if content_id is not None
        )
    if news_ids:
        exact_read_news_ids = {
            int(news_id)
            for news_id in db.execute(
                select(NewsItemReadStatus.news_item_id).where(
                    NewsItemReadStatus.user_id == user_id,
                    NewsItemReadStatus.news_item_id.in_(news_ids),
                )
            ).scalars()
            if news_id is not None
        }
        canonical_by_news_id = _canonical_news_ids(db, news_item_ids=set(news_ids))
        canonical_ids = set(canonical_by_news_id.values())
        read_canonical_ids = {
            int(canonical_id)
            for canonical_id in db.execute(
                select(
                    func.coalesce(
                        NewsItem.representative_news_item_id,
                        NewsItem.id,
                    )
                )
                .join(
                    NewsItemReadStatus,
                    NewsItemReadStatus.news_item_id == NewsItem.id,
                )
                .where(
                    NewsItemReadStatus.user_id == user_id,
                    func.coalesce(
                        NewsItem.representative_news_item_id,
                        NewsItem.id,
                    ).in_(canonical_ids),
                )
                .distinct()
            ).scalars()
            if canonical_id is not None
        }
        read_news_ids = exact_read_news_ids | {
            news_id
            for news_id, canonical_id in canonical_by_news_id.items()
            if canonical_id in read_canonical_ids
        }
        keys.update(build_source_key("news", news_id) for news_id in read_news_ids)
    return keys


def _canonical_news_ids(
    db: Session,
    *,
    news_item_ids: set[int],
) -> dict[int, int]:
    if not news_item_ids:
        return {}
    rows = db.execute(
        select(NewsItem.id, NewsItem.representative_news_item_id).where(
            NewsItem.id.in_(news_item_ids)
        )
    ).all()
    return {
        int(news_item_id): int(representative_id or news_item_id)
        for news_item_id, representative_id in rows
    }


def _news_cluster_member_ids(
    db: Session,
    *,
    news_item_ids: set[int],
) -> set[int]:
    canonical_ids = set(_canonical_news_ids(db, news_item_ids=news_item_ids).values())
    if not canonical_ids:
        return set()
    return {
        int(news_item_id)
        for news_item_id in db.execute(
            select(NewsItem.id).where(
                or_(
                    NewsItem.id.in_(canonical_ids),
                    NewsItem.representative_news_item_id.in_(canonical_ids),
                )
            )
        ).scalars()
        if news_item_id is not None
    }


def _source_from_content(
    content: Content,
    *,
    include_briefing_context: bool = True,
) -> BriefingSource:
    content_id = _require_id(content.id, "content.id")
    content_type = ContentType(str(content.content_type))
    metadata = dict(content.content_metadata or {})
    summary = extract_short_summary(metadata.get("summary")) or _clean_string(
        metadata.get("excerpt")
    )
    key_points = _key_points_from_metadata(metadata)
    briefing_context = (
        _briefing_context_from_metadata(metadata) if include_briefing_context else None
    )
    tier = "audio" if content_type == ContentType.PODCAST else "longform"
    lens_key = "podcasts" if content_type == ContentType.PODCAST else "articles"
    image_version = metadata.get("image_version") or metadata.get("thumbnail_version")
    return BriefingSource(
        source_key=build_source_key("content", content_id),
        kind="content",
        id=content_id,
        tier=tier,
        lens_key=lens_key,
        title=content.title or f"Content {content_id}",
        summary=summary,
        key_points=key_points,
        url=content.source_url or content.url,
        image_url=build_content_image_url(content_id, version=image_version),
        thumbnail_url=build_thumbnail_url(content_id, version=image_version),
        published_at=content.publication_date or content.created_at,
        content_type=content_type,
        briefing_context=briefing_context,
    )


def _briefing_context_from_metadata(metadata: dict[str, Any]) -> str | None:
    parts: list[str] = []
    summary = metadata.get("summary")

    if isinstance(summary, dict):
        _append_labeled_text(parts, "Overview", summary.get("overview"))
        _append_labeled_text(parts, "Summary", summary.get("summary"))
        _append_labeled_text(parts, "Hook", summary.get("hook"))
        _append_labeled_text(parts, "One line", summary.get("one_line"))
        _append_labeled_text(parts, "Narrative", summary.get("editorial_narrative"))
        _append_items(
            parts,
            "Key points",
            summary.get("key_points") or summary.get("bullet_points") or summary.get("points"),
        )
        _append_items(parts, "Insights", summary.get("insights"))
        _append_items(parts, "Topics", summary.get("topics"))
        _append_items(parts, "Quotes", summary.get("quotes"), max_items=3)
        _append_items(parts, "Questions", summary.get("questions"), max_items=3)
        _append_items(parts, "Counterarguments", summary.get("counter_arguments"), max_items=3)
        _append_labeled_text(parts, "Takeaway", summary.get("takeaway"))
        _append_source_details(parts, summary.get("source_details"))
        _append_artifact(parts, summary.get("artifact"))
        _append_feed_preview(parts, summary.get("feed_preview"))
        _append_source_excerpt(parts, summary.get("full_markdown"))
    elif isinstance(summary, str):
        _append_labeled_text(parts, "Summary", summary)

    _append_labeled_text(parts, "Excerpt", metadata.get("excerpt"))
    _append_source_excerpt(parts, metadata.get("content_to_summarize"))
    _append_source_excerpt(parts, metadata.get("content"))
    _append_source_excerpt(parts, metadata.get("transcript"), label="Transcript excerpt")

    context = "\n\n".join(part for part in parts if part).strip()
    if not context:
        return None
    return _truncate_text(context, BRIEFING_CONTEXT_MAX_CHARS)


def _append_labeled_text(parts: list[str], label: str, value: Any) -> None:
    text = _text_from_value(value)
    if text:
        parts.append(f"{label}: {text}")


def _append_items(
    parts: list[str],
    label: str,
    value: Any,
    *,
    max_items: int = BRIEFING_CONTEXT_LIST_MAX_ITEMS,
) -> None:
    if not isinstance(value, list):
        return
    items = [_text_from_value(item) for item in value[:max_items]]
    lines = [f"- {item}" for item in items if item]
    if lines:
        parts.append(f"{label}:\n" + "\n".join(lines))


def _append_source_details(parts: list[str], value: Any) -> None:
    if not isinstance(value, dict):
        return
    detail_parts: list[str] = []
    for key, detail_value in value.items():
        if key == "template":
            continue
        label = key.replace("_", " ").capitalize()
        if isinstance(detail_value, list):
            items = [
                _text_from_value(item) for item in detail_value[:BRIEFING_CONTEXT_LIST_MAX_ITEMS]
            ]
            cleaned = [item for item in items if item]
            if cleaned:
                detail_parts.append(f"{label}: " + "; ".join(cleaned))
            continue
        text = _text_from_value(detail_value)
        if text:
            detail_parts.append(f"{label}: {text}")
    if detail_parts:
        parts.append("Source details:\n" + "\n".join(f"- {part}" for part in detail_parts))


def _append_artifact(parts: list[str], value: Any) -> None:
    if not isinstance(value, dict):
        return
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return
    artifact_parts: list[str] = []
    for key in (
        "overview",
        "thesis",
        "primary_claim",
        "counterpoint",
        "takeaway",
        "reason_to_read",
    ):
        text = _text_from_value(payload.get(key))
        if text:
            artifact_parts.append(f"{key.replace('_', ' ').capitalize()}: {text}")
    for key in ("key_points", "supporting_arguments", "evidence", "implications", "quotes"):
        value_items = payload.get(key)
        if not isinstance(value_items, list):
            continue
        items = [_text_from_value(item) for item in value_items[:BRIEFING_CONTEXT_LIST_MAX_ITEMS]]
        cleaned = [item for item in items if item]
        if cleaned:
            artifact_parts.append(f"{key.replace('_', ' ').capitalize()}: " + "; ".join(cleaned))
    extras = payload.get("extras")
    if isinstance(extras, dict):
        for key, extra_value in extras.items():
            text = _text_from_value(extra_value)
            if text:
                artifact_parts.append(f"{key.replace('_', ' ').capitalize()}: {text}")
    if artifact_parts:
        parts.append("Artifact context:\n" + "\n".join(f"- {part}" for part in artifact_parts))


def _append_feed_preview(parts: list[str], value: Any) -> None:
    if not isinstance(value, dict):
        return
    preview_parts: list[str] = []
    for key in ("one_line", "reason_to_read"):
        text = _text_from_value(value.get(key))
        if text:
            preview_parts.append(f"{key.replace('_', ' ').capitalize()}: {text}")
    bullets = value.get("preview_bullets")
    if isinstance(bullets, list):
        items = [_text_from_value(item) for item in bullets[:3]]
        cleaned = [item for item in items if item]
        if cleaned:
            preview_parts.append("Preview bullets: " + "; ".join(cleaned))
    if preview_parts:
        parts.append("Feed preview:\n" + "\n".join(f"- {part}" for part in preview_parts))


def _append_source_excerpt(
    parts: list[str],
    value: Any,
    *,
    label: str = "Source excerpt",
) -> None:
    text = _text_from_value(value)
    if text:
        parts.append(f"{label}: {_truncate_text(text, BRIEFING_SOURCE_EXCERPT_MAX_CHARS)}")


def _text_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean_string(value)
    if not isinstance(value, dict):
        return None

    heading = _clean_string(
        value.get("heading")
        or value.get("topic")
        or value.get("title")
        or value.get("category")
        or value.get("attribution")
        or value.get("context")
    )
    body = _clean_string(
        value.get("text")
        or value.get("point")
        or value.get("detail")
        or value.get("content")
        or value.get("summary")
        or value.get("insight")
        or value.get("overview")
        or value.get("thesis")
        or value.get("primary_claim")
        or value.get("takeaway")
    )

    nested_lines: list[str] = []
    bullets = value.get("bullets")
    if isinstance(bullets, list):
        nested_lines = [
            nested for nested in (_text_from_value(item) for item in bullets[:3]) if nested
        ]

    text: str | None
    if heading and body and heading.lower() not in body.lower():
        text = f"{heading}: {body}"
    else:
        text = body or heading
    if nested_lines:
        nested_text = "; ".join(nested_lines)
        text = f"{text} ({nested_text})" if text else nested_text
    return _truncate_text(text, 700) if text else None


def _truncate_text(value: str, max_chars: int) -> str:
    cleaned = " ".join(value.split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    suffix = "..."
    available_chars = max(max_chars - len(suffix), 0)
    truncated = cleaned[:available_chars].rsplit(" ", 1)[0].strip()
    return (truncated.rstrip(".,;:") + suffix)[:max_chars]


def _source_from_news_item(item: NewsItem) -> BriefingSource:
    item_id = _require_id(item.id, "news_item.id")
    raw_metadata = dict(item.raw_metadata or {})
    topic_slug, topic_title = _topic_from_news_metadata(raw_metadata)
    image_version = raw_metadata.get("image_version") or raw_metadata.get("thumbnail_version")
    # Only advertise a thumbnail when the image pipeline actually generated one
    # (mirrors content_display's has_generated_image gate). News items without a
    # generated image stay imageless so no figure is composed for them.
    has_generated_image = bool(raw_metadata.get("image_generated_at"))
    thumbnail_url = (
        build_news_thumbnail_url(item_id, version=image_version) if has_generated_image else None
    )
    return BriefingSource(
        source_key=build_source_key("news", item_id),
        kind="news",
        id=item_id,
        tier="news",
        lens_key=None,
        title=resolve_news_display_title(
            raw_metadata,
            summary_text=item.summary_text,
            fallback=f"News item {item_id}",
        ),
        summary=_clean_string(item.summary_text),
        key_points=[point for point in (item.summary_key_points or []) if isinstance(point, str)],
        url=item.article_url or item.canonical_story_url or item.canonical_item_url,
        image_url=None,
        thumbnail_url=thumbnail_url,
        published_at=item.published_at or item.processed_at or item.ingested_at or item.created_at,
        content_type=ContentType.NEWS,
        topic_slug=topic_slug,
        topic_title=topic_title,
    )


def _topic_from_news_metadata(raw_metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    aggregator = raw_metadata.get("aggregator")
    if not isinstance(aggregator, dict):
        return None, None
    raw_topic = _clean_string(aggregator.get("topic"))
    if raw_topic is None:
        return None, None
    slug = _slugify(raw_topic)
    if not slug:
        return None, None
    title = _clean_string(aggregator.get("topic_title")) or raw_topic.replace("-", " ").title()
    return slug, title


def _slugify(value: str) -> str:
    chars: list[str] = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-")[:48]


def _require_id(value: int | None, field: str) -> int:
    if value is None:
        raise ValueError(f"Missing persisted {field}")
    return int(value)
