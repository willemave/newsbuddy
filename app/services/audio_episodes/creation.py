"""Audio episode source collection, creation, reuse, and queueing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.api.audio_episodes import AudioEpisodeKind
from app.models.contracts import ContentType, TaskType
from app.models.db import AudioEpisode, Content, NewsItem
from app.repositories.content_detail_repository import get_visible_content
from app.services.audio_episode_kinds import (
    CONTENT_COUNCIL_DISCUSSION_KIND,
    CUSTOM_NARRATION_KIND,
    FAST_NEWS_DIGEST_KIND,
    NEWS_ITEM_DISCUSSION_KIND,
)
from app.services.audio_episode_sources import build_content_source_payload
from app.services.audio_episodes.shared import (
    PROMPT_VERSION,
    int_list_from_snapshot_values,
    required_int,
)
from app.services.content_bodies import get_content_body_resolver
from app.services.custom_narrations import (
    build_custom_narration_source_snapshot,
    custom_narration_title,
)
from app.services.news_feed import (
    get_visible_news_item,
    list_unread_visible_news_items,
)
from app.services.queue import TaskEnqueueRequest, get_queue_service
from app.utils.news_titles import resolve_news_display_title

logger = get_logger(__name__)

FAST_NEWS_LIMIT = 200


def create_fast_news_digest_episode(db: Session, *, user_id: int) -> AudioEpisode:
    """Create or reuse an on-demand digest for the user's unread Fast Reads."""

    items, total_unread = list_unread_visible_news_items(
        db,
        user_id=user_id,
        limit=FAST_NEWS_LIMIT,
    )
    if not items:
        raise HTTPException(status_code=400, detail="No unread Fast Reads are available")

    source_items = [_news_item_source_snapshot(item) for item in items]
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=AudioEpisodeKind.FAST_NEWS_DIGEST,
        title="Fast Reads Brief",
        source_content_id=None,
        source_item_ids=[int(item.id) for item in items if item.id is not None],
        source_snapshot={
            "kind": FAST_NEWS_DIGEST_KIND,
            "total_unread": total_unread,
            "included_count": len(source_items),
            "items": source_items,
        },
    )


def create_content_council_episode(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> AudioEpisode:
    """Create or reuse a council-style discussion episode for one long-form item."""

    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Content not found")
    content_type = str(content.content_type or "")
    if content_type not in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
        raise HTTPException(status_code=400, detail="Audio discussions are only for long form")

    body_text = get_content_body_resolver().resolve_text(db, content=content)
    if not body_text:
        raise HTTPException(status_code=400, detail="No article or transcript text is available")

    source_snapshot = _content_source_snapshot(content, body_text=body_text)
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=AudioEpisodeKind.CONTENT_COUNCIL_DISCUSSION,
        title=f"Expert discussion: {source_snapshot['title']}"[:255],
        source_content_id=content_id,
        source_item_ids=[],
        source_snapshot=source_snapshot,
    )


def create_custom_narration_episode(
    db: Session,
    *,
    user_id: int,
    content_ids: list[int],
    news_item_ids: list[int] | None = None,
    title: str | None = None,
    mark_source_content_read_on_play: bool = False,
) -> AudioEpisode:
    """Create or reuse one combined narration from selected sources."""

    source_snapshot = build_custom_narration_source_snapshot(
        db,
        user_id=user_id,
        content_ids=content_ids,
        news_item_ids=news_item_ids,
        mark_source_content_read_on_play=mark_source_content_read_on_play,
    )
    raw_news_item_ids = source_snapshot.get("news_item_ids")
    source_item_ids = (
        int_list_from_snapshot_values(raw_news_item_ids)
        if isinstance(raw_news_item_ids, list)
        else []
    )
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=AudioEpisodeKind.CUSTOM_NARRATION,
        title=custom_narration_title(source_snapshot, title=title)[:255],
        source_content_id=None,
        source_item_ids=source_item_ids,
        source_snapshot=source_snapshot,
    )


def create_news_item_discussion_episode(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
) -> AudioEpisode:
    """Create or reuse a podcast-style discussion for one Fast Read."""

    item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="News item not found")
    source_item = _news_item_source_snapshot(item)
    if not source_item.get("summary") and not source_item.get("key_points"):
        raise HTTPException(status_code=400, detail="No Fast Read summary is available")
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=AudioEpisodeKind.NEWS_ITEM_DISCUSSION,
        title=f"News discussion: {source_item['title']}"[:255],
        source_content_id=None,
        source_item_ids=[required_int(item.id, "news item id")],
        source_snapshot={"kind": NEWS_ITEM_DISCUSSION_KIND, "item": source_item},
    )


def list_custom_narration_episodes(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> list[AudioEpisode]:
    """Return recent custom narrations for a user."""

    return (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == CUSTOM_NARRATION_KIND,
        )
        .order_by(AudioEpisode.created_at.desc(), AudioEpisode.id.desc())
        .limit(min(max(limit, 1), 50))
        .all()
    )


def enqueue_audio_episode_generation(
    db: Session,
    *,
    audio_episode_id: int,
    user_id: int,
) -> int:
    """Stage owned background generation in the caller's transaction."""

    task_id = get_queue_service().enqueue_many_in_session(
        db,
        [
            TaskEnqueueRequest(
                TaskType.GENERATE_AUDIO_EPISODE,
                payload={"audio_episode_id": audio_episode_id, "user_id": user_id},
                dedupe_key=f"audio_episode:{audio_episode_id}",
                owner_user_id=user_id,
            )
        ],
    )[0]
    logger.info(
        "Audio episode generation enqueued",
        extra={
            "component": "audio_episodes",
            "operation": "enqueue_generation",
            "item_id": audio_episode_id,
            "task_id": task_id,
            "task_type": TaskType.GENERATE_AUDIO_EPISODE.value,
        },
    )
    return task_id


def get_user_audio_episode(
    db: Session,
    *,
    user_id: int,
    audio_episode_id: int,
) -> AudioEpisode | None:
    """Return one audio episode owned by a user."""

    return (
        db.query(AudioEpisode)
        .filter(AudioEpisode.id == audio_episode_id, AudioEpisode.user_id == user_id)
        .first()
    )


def _create_or_reuse_episode(
    db: Session,
    *,
    user_id: int,
    kind: AudioEpisodeKind,
    title: str,
    source_content_id: int | None,
    source_item_ids: list[int],
    source_snapshot: dict[str, Any],
) -> AudioEpisode:
    input_hash = _source_snapshot_hash(source_snapshot)
    existing = (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == kind,
            AudioEpisode.input_hash == input_hash,
        )
        .first()
    )
    if existing is not None:
        _reset_failed_episode(existing)
        return existing

    episode = AudioEpisode(
        user_id=user_id,
        kind=kind,
        status="pending",
        title=title,
        source_content_id=source_content_id,
        input_hash=input_hash,
        source_item_ids=source_item_ids,
        source_snapshot=source_snapshot,
        prompt_version=PROMPT_VERSION,
    )
    try:
        with db.begin_nested():
            db.add(episode)
            db.flush()
    except IntegrityError:
        episode = (
            db.query(AudioEpisode)
            .filter(
                AudioEpisode.user_id == user_id,
                AudioEpisode.kind == kind,
                AudioEpisode.input_hash == input_hash,
            )
            .one()
        )
        _reset_failed_episode(episode)
    return episode


def _reset_failed_episode(episode: AudioEpisode) -> None:
    if episode.status != "failed":
        return
    episode.status = "pending"
    episode.error_message = None
    episode.script = None
    episode.script_text = None
    episode.audio_storage_path = None
    episode.duration_seconds = None
    episode.started_at = None
    episode.completed_at = None


def _news_item_source_snapshot(item: NewsItem) -> dict[str, Any]:
    item_id = required_int(item.id, "news item id")
    return {
        "id": item_id,
        "title": resolve_news_display_title(
            item.raw_metadata,
            summary_text=item.summary_text,
            fallback=f"News item {item_id}",
        ),
        "source": item.source_label,
        "platform": item.platform,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "summary": item.summary_text,
        "key_points": list(item.summary_key_points or []),
        "article_url": item.article_url or item.canonical_story_url,
        "discussion_url": item.discussion_url or item.canonical_item_url,
    }


def _content_source_snapshot(content: Content, *, body_text: str) -> dict[str, Any]:
    source_payload = build_content_source_payload(content, body_text=body_text)
    source_payload.pop("source_text_chars", None)
    source_payload.pop("source_text_included_chars", None)
    return {"kind": CONTENT_COUNCIL_DISCUSSION_KIND, **source_payload}


def _source_snapshot_hash(source_snapshot: dict[str, Any]) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "source_snapshot": source_snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "FAST_NEWS_LIMIT",
    "create_content_council_episode",
    "create_custom_narration_episode",
    "create_fast_news_digest_episode",
    "create_news_item_discussion_episode",
    "enqueue_audio_episode_generation",
    "get_user_audio_episode",
    "list_custom_narration_episodes",
]
