"""On-demand podcast-style audio episode generation."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.model_defaults import ARTICLE_PODCAST_SUMMARY_MODEL_SPEC, CHEAP_GOOGLE_MODEL_SPEC
from app.core.settings import get_settings
from app.models.api.audio_episodes import (
    CUSTOM_NARRATION_MAX_CONTENT_IDS,
    AudioEpisodeDelivery,
    AudioEpisodeKind,
    AudioEpisodeResponse,
    AudioEpisodeStatus,
)
from app.models.contracts import ContentType, TaskType
from app.models.db import AudioEpisode, Content, NewsItem
from app.models.domain.content_mapper import content_to_domain
from app.repositories.content_detail_repository import get_visible_content
from app.repositories.content_repository import build_visibility_context
from app.services.content_bodies import get_content_body_resolver
from app.services.llm_agents import get_basic_agent
from app.services.news_feed import (
    get_visible_news_item,
    list_unread_visible_news_items,
)
from app.services.queue import get_queue_service
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band
from app.services.voice.narration_tts import get_content_narration_tts_service
from app.utils.news_titles import resolve_news_display_title

logger = get_logger(__name__)

FAST_NEWS_DIGEST_KIND: Literal["fast_news_digest"] = "fast_news_digest"
CONTENT_COUNCIL_DISCUSSION_KIND: Literal["content_council_discussion"] = (
    "content_council_discussion"
)
NEWS_ITEM_DISCUSSION_KIND: Literal["news_item_discussion"] = "news_item_discussion"
CUSTOM_NARRATION_KIND: Literal["custom_narration"] = "custom_narration"
PROMPT_VERSION = 4
FAST_NEWS_LIMIT = 200
CUSTOM_NARRATION_MAX_SOURCES = CUSTOM_NARRATION_MAX_CONTENT_IDS
LONGFORM_BODY_MAX_CHARS = 16_000
LONGFORM_BODY_HEAD_CHARS = 7_000
LONGFORM_BODY_MIDDLE_CHARS = 4_000
LONGFORM_BODY_TAIL_CHARS = 5_000
AUDIO_EPISODE_MODEL = ARTICLE_PODCAST_SUMMARY_MODEL_SPEC
CUSTOM_NARRATION_MODEL = CHEAP_GOOGLE_MODEL_SPEC
SCRIPT_TIMEOUT_SECONDS = 180
DIALOGUE_TEXT_CHAR_LIMIT = 1_100
CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT = 4_500
AUDIO_EPISODE_PROCESSING_STALE_AFTER = timedelta(minutes=15)
AUDIO_EPISODE_FILE_CHUNK_SIZE = 1024 * 256
AUDIO_EPISODE_FOLLOW_POLL_SECONDS = 0.25
AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS = 180

SCRIPT_SYSTEM_PROMPT = """You write concise, natural podcast scripts for Newsly.
Create spoken dialogue, not an essay. The format should feel like a smart tech/business
podcast roundtable: quick context, clear stakes, grounded analysis, and a brisk close.
Do not mention or imitate any specific real podcast, host, or brand. Do not invent facts
outside the supplied source material. No stage directions, music cues, sponsor reads, or
markdown."""


class AudioEpisodeTurn(BaseModel):
    """One spoken turn in a generated podcast script."""

    speaker: Literal["host", "cohost", "expert"] = Field(
        ...,
        description="Speaker role for this turn.",
    )
    text: str = Field(..., min_length=1, max_length=700)


class AudioEpisodeScript(BaseModel):
    """Structured output for one generated audio episode."""

    title: str = Field(..., min_length=1, max_length=120)
    estimated_duration_seconds: int = Field(..., ge=30, le=600)
    turns: list[AudioEpisodeTurn] = Field(..., min_length=6, max_length=16)


class AudioEpisodeAlreadyProcessingError(RuntimeError):
    """Raised when a live generator is already producing the episode."""


@dataclass(frozen=True)
class AudioEpisodeScriptGeneration:
    """Structured script plus the model that produced it."""

    script: AudioEpisodeScript
    model: str


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def create_fast_news_digest_episode(db: Session, *, user_id: int) -> AudioEpisode:
    """Create or reuse an on-demand digest for the user's unread Fast Reads."""

    started_at = time.perf_counter()
    logger.info(
        "Audio episode create started",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "user_id": user_id,
            "context_data": {"kind": FAST_NEWS_DIGEST_KIND},
        },
    )
    items, total_unread = list_unread_visible_news_items(
        db,
        user_id=user_id,
        limit=FAST_NEWS_LIMIT,
    )
    if not items:
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "user_id": user_id,
                "context_data": {
                    "kind": FAST_NEWS_DIGEST_KIND,
                    "reason": "no_unread_fast_reads",
                },
            },
        )
        raise HTTPException(status_code=400, detail="No unread Fast Reads are available")

    source_items = [_news_item_source_snapshot(item) for item in items]
    source_snapshot = {
        "kind": FAST_NEWS_DIGEST_KIND,
        "total_unread": total_unread,
        "included_count": len(source_items),
        "items": source_items,
    }
    title = "Fast Reads Brief"
    episode = _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=FAST_NEWS_DIGEST_KIND,
        title=title,
        source_content_id=None,
        source_item_ids=[int(item.id) for item in items if item.id is not None],
        source_snapshot=source_snapshot,
    )
    logger.info(
        "Audio episode create prepared",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "status": "prepared",
            "duration_ms": _duration_ms(started_at),
            "item_id": episode.id,
            "user_id": user_id,
            "context_data": {
                "kind": FAST_NEWS_DIGEST_KIND,
                "episode_status": episode.status,
                "included_count": len(source_items),
                "total_unread": total_unread,
            },
        },
    )
    return episode


def create_content_council_episode(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> AudioEpisode:
    """Create or reuse a council-style discussion episode for one long-form item."""

    started_at = time.perf_counter()
    logger.info(
        "Audio episode create started",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "content_id": content_id,
            "user_id": user_id,
            "context_data": {"kind": CONTENT_COUNCIL_DISCUSSION_KIND},
        },
    )
    content = get_visible_content(db, user_id=user_id, content_id=content_id)
    if content is None:
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "content_id": content_id,
                "user_id": user_id,
                "context_data": {
                    "kind": CONTENT_COUNCIL_DISCUSSION_KIND,
                    "reason": "content_not_found",
                },
            },
        )
        raise HTTPException(status_code=404, detail="Content not found")

    content_type = str(content.content_type or "")
    if content_type not in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "content_id": content_id,
                "user_id": user_id,
                "context_data": {
                    "kind": CONTENT_COUNCIL_DISCUSSION_KIND,
                    "reason": "unsupported_content_type",
                    "content_type": content_type,
                },
            },
        )
        raise HTTPException(status_code=400, detail="Audio discussions are only for long form")

    body_text = get_content_body_resolver().resolve_text(db, content=content)
    if not body_text:
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "content_id": content_id,
                "user_id": user_id,
                "context_data": {
                    "kind": CONTENT_COUNCIL_DISCUSSION_KIND,
                    "reason": "missing_body_text",
                    "content_type": content_type,
                },
            },
        )
        raise HTTPException(status_code=400, detail="No article or transcript text is available")

    source_snapshot = _content_source_snapshot(content, body_text=body_text)
    title = f"Expert discussion: {source_snapshot['title']}"
    episode = _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=CONTENT_COUNCIL_DISCUSSION_KIND,
        title=title[:255],
        source_content_id=content_id,
        source_item_ids=[],
        source_snapshot=source_snapshot,
    )
    logger.info(
        "Audio episode create prepared",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "status": "prepared",
            "duration_ms": _duration_ms(started_at),
            "item_id": episode.id,
            "content_id": content_id,
            "user_id": user_id,
            "context_data": {
                "kind": CONTENT_COUNCIL_DISCUSSION_KIND,
                "episode_status": episode.status,
                "content_type": content_type,
                "body_chars": len(body_text),
                "body_truncated": source_snapshot["source_text_truncated"],
            },
        },
    )
    return episode


def create_custom_narration_episode(
    db: Session,
    *,
    user_id: int,
    content_ids: list[int],
    title: str | None = None,
) -> AudioEpisode:
    """Create or reuse one combined narration from selected articles/podcasts."""

    started_at = time.perf_counter()
    normalized_content_ids = _normalize_custom_narration_content_ids(content_ids)
    logger.info(
        "Audio episode create started",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "user_id": user_id,
            "context_data": {
                "kind": CUSTOM_NARRATION_KIND,
                "source_count": len(normalized_content_ids),
            },
        },
    )

    source_items: list[dict[str, Any]] = []
    for content_id in normalized_content_ids:
        content = _get_visible_or_saved_content(db, user_id=user_id, content_id=content_id)
        if content is None:
            logger.info(
                "Audio episode create rejected",
                extra={
                    "component": "audio_episodes",
                    "operation": "create",
                    "status": "rejected",
                    "duration_ms": _duration_ms(started_at),
                    "content_id": content_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": CUSTOM_NARRATION_KIND,
                        "reason": "content_not_found",
                    },
                },
            )
            raise HTTPException(status_code=404, detail=f"Content {content_id} not found")

        content_type = str(content.content_type or "")
        if content_type not in {ContentType.ARTICLE.value, ContentType.PODCAST.value}:
            logger.info(
                "Audio episode create rejected",
                extra={
                    "component": "audio_episodes",
                    "operation": "create",
                    "status": "rejected",
                    "duration_ms": _duration_ms(started_at),
                    "content_id": content_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": CUSTOM_NARRATION_KIND,
                        "reason": "unsupported_content_type",
                        "content_type": content_type,
                    },
                },
            )
            raise HTTPException(
                status_code=400,
                detail="Custom narrations only support articles and podcasts",
            )

        body_text = get_content_body_resolver().resolve_text(db, content=content)
        if not body_text:
            logger.info(
                "Audio episode create rejected",
                extra={
                    "component": "audio_episodes",
                    "operation": "create",
                    "status": "rejected",
                    "duration_ms": _duration_ms(started_at),
                    "content_id": content_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": CUSTOM_NARRATION_KIND,
                        "reason": "missing_body_text",
                        "content_type": content_type,
                    },
                },
            )
            raise HTTPException(
                status_code=400,
                detail=f"No article or transcript text is available for content {content_id}",
            )

        source_items.append(_custom_narration_source_item(content, body_text=body_text))

    source_snapshot = {
        "kind": CUSTOM_NARRATION_KIND,
        "source_count": len(source_items),
        "content_ids": normalized_content_ids,
        "items": source_items,
    }
    episode_title = _custom_narration_title(source_items, title=title)
    episode = _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=CUSTOM_NARRATION_KIND,
        title=episode_title[:255],
        source_content_id=None,
        source_item_ids=[],
        source_snapshot=source_snapshot,
    )
    logger.info(
        "Audio episode create prepared",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "status": "prepared",
            "duration_ms": _duration_ms(started_at),
            "item_id": episode.id,
            "user_id": user_id,
            "context_data": {
                "kind": CUSTOM_NARRATION_KIND,
                "episode_status": episode.status,
                "source_count": len(source_items),
                "source_text_chars": sum(
                    int(item.get("source_text_chars") or 0) for item in source_items
                ),
            },
        },
    )
    return episode


def create_news_item_discussion_episode(
    db: Session,
    *,
    user_id: int,
    news_item_id: int,
) -> AudioEpisode:
    """Create or reuse a podcast-style discussion episode for one Fast Read item."""

    started_at = time.perf_counter()
    logger.info(
        "Audio episode create started",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "item_id": news_item_id,
            "user_id": user_id,
            "context_data": {"kind": NEWS_ITEM_DISCUSSION_KIND},
        },
    )
    item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
    if item is None:
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "item_id": news_item_id,
                "user_id": user_id,
                "context_data": {
                    "kind": NEWS_ITEM_DISCUSSION_KIND,
                    "reason": "news_item_not_found",
                },
            },
        )
        raise HTTPException(status_code=404, detail="News item not found")

    source_item = _news_item_source_snapshot(item)
    if not source_item.get("summary") and not source_item.get("key_points"):
        logger.info(
            "Audio episode create rejected",
            extra={
                "component": "audio_episodes",
                "operation": "create",
                "status": "rejected",
                "duration_ms": _duration_ms(started_at),
                "item_id": news_item_id,
                "user_id": user_id,
                "context_data": {
                    "kind": NEWS_ITEM_DISCUSSION_KIND,
                    "reason": "missing_fast_read_summary",
                },
            },
        )
        raise HTTPException(status_code=400, detail="No Fast Read summary is available")

    source_snapshot = {
        "kind": NEWS_ITEM_DISCUSSION_KIND,
        "item": source_item,
    }
    title = f"News discussion: {source_item['title']}"
    episode = _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=NEWS_ITEM_DISCUSSION_KIND,
        title=title[:255],
        source_content_id=None,
        source_item_ids=[_required_int(item.id, "news item id")],
        source_snapshot=source_snapshot,
    )
    logger.info(
        "Audio episode create prepared",
        extra={
            "component": "audio_episodes",
            "operation": "create",
            "status": "prepared",
            "duration_ms": _duration_ms(started_at),
            "item_id": episode.id,
            "user_id": user_id,
            "context_data": {
                "kind": NEWS_ITEM_DISCUSSION_KIND,
                "episode_status": episode.status,
                "news_item_id": news_item_id,
                "summary_chars": len(str(source_item.get("summary") or "")),
                "key_point_count": len(source_item.get("key_points") or []),
            },
        },
    )
    return episode


def list_custom_narration_episodes(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> list[AudioEpisode]:
    """Return recent custom narrations for a user."""

    bounded_limit = min(max(limit, 1), 50)
    return (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == CUSTOM_NARRATION_KIND,
        )
        .order_by(AudioEpisode.created_at.desc(), AudioEpisode.id.desc())
        .limit(bounded_limit)
        .all()
    )


def enqueue_audio_episode_generation(audio_episode_id: int) -> int:
    """Enqueue background generation for an audio episode."""

    started_at = time.perf_counter()
    task_id = get_queue_service().enqueue(
        TaskType.GENERATE_AUDIO_EPISODE,
        payload={"audio_episode_id": audio_episode_id},
        dedupe_key=f"audio_episode:{audio_episode_id}",
    )
    logger.info(
        "Audio episode generation enqueued",
        extra={
            "component": "audio_episodes",
            "operation": "enqueue_generation",
            "duration_ms": _duration_ms(started_at),
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


def present_audio_episode(episode: AudioEpisode) -> AudioEpisodeResponse:
    """Build the API response for an audio episode."""

    episode_id = _required_int(episode.id, "audio episode id")
    stream_url = f"/api/content/audio-episodes/{episode_id}/stream"
    audio_url = None
    if episode.status == "completed" and episode.audio_storage_path:
        audio_url = f"/api/content/audio-episodes/{episode_id}/audio"

    return AudioEpisodeResponse(
        id=episode_id,
        kind=_audio_episode_kind(episode.kind),
        status=_audio_episode_status(episode.status),
        title=_required_str(episode.title, "audio episode title"),
        source_content_id=episode.source_content_id,
        source_item_ids=[int(item_id) for item_id in (episode.source_item_ids or [])],
        source_content_ids=_episode_source_content_ids(episode),
        source_count=_episode_source_count(episode),
        source_titles=_episode_source_titles(episode),
        duration_seconds=episode.duration_seconds,
        audio_url=audio_url,
        stream_url=stream_url,
        script_text=episode.script_text,
        error_message=episode.error_message,
        created_at=_required_datetime(episode.created_at, "audio episode created_at"),
        updated_at=episode.updated_at,
    )


def commit_audio_episode_delivery(
    db: Session,
    episode: AudioEpisode,
    *,
    delivery: AudioEpisodeDelivery,
) -> AudioEpisodeResponse:
    """Commit an episode, enqueue background work when requested, and return it."""

    started_at = time.perf_counter()
    task_id: int | None = None
    episode_id_before_commit = episode.id
    db.commit()
    db.refresh(episode)
    if delivery == "background" and episode.status != "completed":
        episode_id = episode.id
        if episode_id is None:
            raise RuntimeError("Audio episode must be persisted before enqueue")
        task_id = enqueue_audio_episode_generation(episode_id)
    elif delivery == "inline" and episode.status != "completed":
        episode_id = episode.id
        if episode_id is None:
            raise RuntimeError("Audio episode must be persisted before inline generation")
        episode = generate_audio_episode(db, audio_episode_id=episode_id)
        db.commit()
        db.refresh(episode)
    logger.info(
        "Audio episode delivery committed",
        extra={
            "component": "audio_episodes",
            "operation": "commit_delivery",
            "status": episode.status,
            "duration_ms": _duration_ms(started_at),
            "item_id": episode.id or episode_id_before_commit,
            "task_id": task_id,
            "user_id": episode.user_id,
            "context_data": {
                "delivery": delivery,
                "kind": episode.kind,
                "has_audio": bool(episode.audio_storage_path),
            },
        },
    )
    return present_audio_episode(episode)


def audio_episode_file_path(episode: AudioEpisode) -> Path | None:
    """Return the local MP3 path for a generated episode."""

    storage_path = str(episode.audio_storage_path or "").strip()
    if not storage_path:
        return None
    return Path(storage_path)


def generate_audio_episode(db: Session, *, audio_episode_id: int) -> AudioEpisode:
    """Generate script and audio for one persisted audio episode."""

    started_at = time.perf_counter()
    logger.info(
        "Audio episode generation started",
        extra={
            "component": "audio_episodes",
            "operation": "generate",
            "item_id": audio_episode_id,
        },
    )
    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None:
        raise ValueError(f"Audio episode {audio_episode_id} not found")
    if episode.status == "completed" and episode.audio_storage_path:
        logger.info(
            "Audio episode generation skipped",
            extra={
                "component": "audio_episodes",
                "operation": "generate",
                "status": "completed",
                "duration_ms": _duration_ms(started_at),
                "item_id": audio_episode_id,
                "user_id": episode.user_id,
                "context_data": {"kind": episode.kind, "reason": "already_completed"},
            },
        )
        return episode
    if episode.status == "processing" and not is_audio_episode_processing_stale(episode):
        logger.info(
            "Audio episode generation skipped",
            extra={
                "component": "audio_episodes",
                "operation": "generate",
                "status": "processing",
                "duration_ms": _duration_ms(started_at),
                "item_id": audio_episode_id,
                "user_id": episode.user_id,
                "context_data": {"kind": episode.kind, "reason": "already_processing"},
            },
        )
        return episode

    episode_id = _required_int(episode.id, "audio episode id")
    user_id = _required_int(episode.user_id, "audio episode user_id")
    now = datetime.now(UTC).replace(tzinfo=None)
    episode.status = "processing"
    episode.error_message = None
    episode.started_at = now
    db.flush()

    script_duration_ms = 0.0
    tts_duration_ms = 0.0
    write_duration_ms = 0.0
    audio_bytes_length = 0
    try:
        script_started_at = time.perf_counter()
        script = _prepare_audio_episode_script(db, episode)
        script_duration_ms = _duration_ms(script_started_at)
        script_text = _required_str(episode.script_text, "audio episode script_text")
        tts_started_at = time.perf_counter()
        audio_bytes = get_content_narration_tts_service().synthesize_dialogue_mp3(
            turns=[turn.model_dump(mode="json") for turn in script.turns],
            item_id=episode_id,
            user_id=user_id,
        )
        tts_duration_ms = _duration_ms(tts_started_at)
        audio_bytes_length = len(audio_bytes)
        write_started_at = time.perf_counter()
        audio_path = _write_audio_episode_file(episode_id, audio_bytes)
        write_duration_ms = _duration_ms(write_started_at)
    except Exception as exc:  # noqa: BLE001
        episode.status = "failed"
        episode.error_message = str(exc)
        episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
        db.flush()
        logger.exception(
            "Audio episode generation failed",
            extra={
                "component": "audio_episodes",
                "operation": "generate",
                "duration_ms": _duration_ms(started_at),
                "item_id": audio_episode_id,
                "user_id": episode.user_id,
                "context_data": {
                    "kind": episode.kind,
                    "error": str(exc),
                    "script_duration_ms": script_duration_ms,
                    "tts_duration_ms": tts_duration_ms,
                    "write_duration_ms": write_duration_ms,
                    "audio_bytes": audio_bytes_length,
                },
            },
        )
        raise

    episode.status = "completed"
    episode.audio_storage_path = str(audio_path)
    episode.audio_content_type = "audio/mpeg"
    episode.duration_seconds = _estimate_duration_seconds(script_text)
    episode.error_message = None
    episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    logger.info(
        "Audio episode generation completed",
        extra={
            "component": "audio_episodes",
            "operation": "generate",
            "status": "completed",
            "duration_ms": _duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
            "context_data": {
                "kind": episode.kind,
                "script_duration_ms": script_duration_ms,
                "tts_duration_ms": tts_duration_ms,
                "write_duration_ms": write_duration_ms,
                "audio_bytes": audio_bytes_length,
                "turn_count": len(script.turns),
                "duration_seconds": episode.duration_seconds,
            },
        },
    )
    return episode


def is_audio_episode_processing_stale(episode: AudioEpisode) -> bool:
    """Return whether an in-flight episode is old enough for a new stream to retry."""

    if episode.status != "processing":
        return False
    started_at = episode.started_at
    if started_at is None:
        return True
    processing_age = datetime.now(UTC).replace(tzinfo=None) - started_at
    return processing_age > AUDIO_EPISODE_PROCESSING_STALE_AFTER


def stream_audio_episode_chunks(*, audio_episode_id: int, user_id: int) -> Iterator[bytes]:
    """Generate and stream an audio episode while caching the completed MP3."""

    SessionLocal = get_session_factory()
    partial_path: Path | None = None
    stream_started_at = time.perf_counter()
    chunk_count = 0
    audio_bytes = 0
    first_chunk_ms: float | None = None
    script_duration_ms = 0.0
    episode_kind: str | None = None
    script_text: str | None = None
    cached_path: Path | None = None
    script: AudioEpisodeScript | None = None
    script_input: AudioEpisode | None = None
    script_model = AUDIO_EPISODE_MODEL
    logger.info(
        "Audio episode stream generator started",
        extra={
            "component": "audio_episodes",
            "operation": "stream",
            "status": "started",
            "item_id": audio_episode_id,
            "user_id": user_id,
        },
    )
    try:
        with SessionLocal() as db:
            episode = get_user_audio_episode(
                db,
                user_id=user_id,
                audio_episode_id=audio_episode_id,
            )
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")

            episode_kind = str(episode.kind or "")
            completed_path = audio_episode_file_path(episode)
            if (
                episode.status == "completed"
                and completed_path is not None
                and completed_path.exists()
            ):
                cached_path = completed_path

            if cached_path is None:
                is_active_processing = (
                    episode.status == "processing"
                    and not is_audio_episode_processing_stale(episode)
                )
                if is_active_processing:
                    logger.info(
                        "Audio episode stream rejected",
                        extra={
                            "component": "audio_episodes",
                            "operation": "stream",
                            "status": "already_processing",
                            "duration_ms": _duration_ms(stream_started_at),
                            "item_id": audio_episode_id,
                            "user_id": user_id,
                            "context_data": {"kind": episode_kind},
                        },
                    )
                    raise AudioEpisodeAlreadyProcessingError(
                        "Audio episode is already being generated"
                    )

                episode.status = "processing"
                episode.error_message = None
                episode.started_at = datetime.now(UTC).replace(tzinfo=None)
                episode.completed_at = None
                if not completed_path or not completed_path.exists():
                    episode.audio_storage_path = None
                script = _script_from_episode(episode)
                script_model = str(episode.model or _default_script_model_for_kind(episode_kind))
                if script is None:
                    script_input = _copy_episode_for_script_generation(episode)
                db.commit()

            logger.info(
                "Audio episode stream resolved",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream",
                    "status": "resolved",
                    "duration_ms": _duration_ms(stream_started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": episode_kind,
                        "episode_status": episode.status,
                        "cached": cached_path is not None,
                        "has_script": script is not None,
                        "will_generate_script": script is None and script_input is not None,
                    },
                },
            )

        if cached_path is not None:
            for chunk in _read_audio_episode_file(cached_path):
                if first_chunk_ms is None:
                    first_chunk_ms = _duration_ms(stream_started_at)
                    logger.info(
                        "Audio episode stream first chunk",
                        extra={
                            "component": "audio_episodes",
                            "operation": "stream",
                            "status": "first_chunk",
                            "duration_ms": first_chunk_ms,
                            "item_id": audio_episode_id,
                            "user_id": user_id,
                            "context_data": {"kind": episode_kind, "cached": True},
                        },
                    )
                audio_bytes += len(chunk)
                chunk_count += 1
                yield chunk
            logger.info(
                "Audio episode cached stream completed",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream",
                    "status": "completed",
                    "duration_ms": _duration_ms(stream_started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": episode_kind,
                        "cached": True,
                        "stream_chunk_count": chunk_count,
                        "audio_bytes": audio_bytes,
                        "time_to_first_chunk_ms": first_chunk_ms or 0,
                    },
                },
            )
            return

        if script is None:
            if script_input is None:
                raise RuntimeError("Audio episode script input was not prepared")
            logger.info(
                "Audio episode stream script generation started",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream",
                    "status": "script_started",
                    "duration_ms": _duration_ms(stream_started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {"kind": episode_kind},
                },
            )
            script_started_at = time.perf_counter()
            script_generation = _generate_script(script_input)
            script = script_generation.script
            script_model = script_generation.model
            script_duration_ms = (time.perf_counter() - script_started_at) * 1000
            logger.info(
                "Audio episode stream script generation completed",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream",
                    "status": "script_completed",
                    "duration_ms": _duration_ms(stream_started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {
                        "kind": episode_kind,
                        "model": script_model,
                        "script_duration_ms": round(script_duration_ms, 2),
                        "turn_count": len(script.turns),
                    },
                },
            )

        with SessionLocal() as db:
            episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")
            script = _persist_audio_episode_script(db, episode, script, model=script_model)
            script_text = _required_str(episode.script_text, "audio episode script_text")
            episode_kind = str(episode.kind or "")
            db.commit()

        partial_path = _audio_episode_partial_file_path(audio_episode_id)
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.unlink(missing_ok=True)
        final_path = _audio_episode_final_file_path(audio_episode_id)

        tts_started_at = time.perf_counter()
        logger.info(
            "Audio episode TTS stream started",
            extra={
                "component": "audio_episodes",
                "operation": "stream",
                "status": "tts_started",
                "duration_ms": _duration_ms(stream_started_at),
                "item_id": audio_episode_id,
                "user_id": user_id,
                "context_data": {"kind": episode_kind, "turn_count": len(script.turns)},
            },
        )
        with partial_path.open("wb") as output_file:
            for chunk in get_content_narration_tts_service().stream_dialogue_mp3(
                turns=[turn.model_dump(mode="json") for turn in script.turns],
                item_id=audio_episode_id,
                user_id=user_id,
            ):
                if not chunk:
                    continue
                if first_chunk_ms is None:
                    first_chunk_ms = (time.perf_counter() - tts_started_at) * 1000
                    logger.info(
                        "Audio episode stream first chunk",
                        extra={
                            "component": "audio_episodes",
                            "operation": "stream",
                            "status": "first_chunk",
                            "duration_ms": _duration_ms(stream_started_at),
                            "item_id": audio_episode_id,
                            "user_id": user_id,
                            "context_data": {
                                "kind": episode_kind,
                                "cached": False,
                                "tts_time_to_first_chunk_ms": round(first_chunk_ms, 2),
                                "script_duration_ms": round(script_duration_ms, 2),
                            },
                        },
                    )
                output_file.write(chunk)
                audio_bytes += len(chunk)
                chunk_count += 1
                yield chunk

        if audio_bytes <= 0:
            raise RuntimeError("Audio episode stream produced no audio")

        partial_path.replace(final_path)
        partial_path = None

        with SessionLocal() as db:
            episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")
            episode.status = "completed"
            episode.audio_storage_path = str(final_path)
            episode.audio_content_type = "audio/mpeg"
            episode.duration_seconds = _estimate_duration_seconds(
                _required_str(script_text, "audio episode script_text")
            )
            episode.error_message = None
            episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()

        logger.info(
            "Audio episode streamed and cached",
            extra={
                "component": "audio_episodes",
                "operation": "stream",
                "status": "completed",
                "duration_ms": _duration_ms(stream_started_at),
                "item_id": audio_episode_id,
                "user_id": user_id,
                "context_data": {
                    "kind": episode_kind,
                    "script_duration_ms": round(script_duration_ms, 2),
                    "tts_time_to_first_chunk_ms": round(first_chunk_ms or 0, 2),
                    "stream_chunk_count": chunk_count,
                    "audio_bytes": audio_bytes,
                },
            },
        )
    except GeneratorExit:
        with SessionLocal() as db:
            _handle_stream_cancelled(
                db,
                audio_episode_id=audio_episode_id,
                partial_path=partial_path,
            )
        logger.info(
            "Audio episode stream cancelled",
            extra={
                "component": "audio_episodes",
                "operation": "stream",
                "status": "cancelled",
                "duration_ms": _duration_ms(stream_started_at),
                "item_id": audio_episode_id,
                "user_id": user_id,
                "context_data": {
                    "kind": episode_kind,
                    "chunk_count": chunk_count,
                    "audio_bytes": audio_bytes,
                    "time_to_first_chunk_ms": round(first_chunk_ms or 0, 2),
                },
            },
        )
        raise
    except AudioEpisodeAlreadyProcessingError:
        raise
    except Exception as exc:
        with SessionLocal() as db:
            _handle_stream_failed(
                db,
                audio_episode_id=audio_episode_id,
                partial_path=partial_path,
                error_message=str(exc),
            )
        logger.exception(
            "Audio episode stream failed",
            extra={
                "component": "audio_episodes",
                "operation": "stream",
                "duration_ms": _duration_ms(stream_started_at),
                "item_id": audio_episode_id,
                "user_id": user_id,
                "context_data": {
                    "kind": episode_kind,
                    "error": str(exc),
                    "chunk_count": chunk_count,
                    "audio_bytes": audio_bytes,
                    "time_to_first_chunk_ms": round(first_chunk_ms or 0, 2),
                },
            },
        )
        raise


def follow_audio_episode_stream_chunks(*, audio_episode_id: int, user_id: int) -> Iterator[bytes]:
    """Wait for an active generator to cache audio, then stream the completed MP3.

    AVPlayer/CoreMedia may open a second request while the first streaming generator is
    still producing audio. Returning 409 for that duplicate request can make playback
    fail even though the original generator is healthy, so duplicate callers wait for
    the completed cached file instead.
    """

    SessionLocal = get_session_factory()
    started_at = time.perf_counter()
    first_chunk_ms: float | None = None
    chunk_count = 0
    audio_bytes = 0
    episode_kind: str | None = None
    logger.info(
        "Audio episode stream follower started",
        extra={
            "component": "audio_episodes",
            "operation": "stream_follow",
            "status": "started",
            "item_id": audio_episode_id,
            "user_id": user_id,
        },
    )
    deadline = started_at + AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS
    while time.perf_counter() < deadline:
        should_take_over_generation = False
        with SessionLocal() as db:
            episode = get_user_audio_episode(
                db,
                user_id=user_id,
                audio_episode_id=audio_episode_id,
            )
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")

            episode_kind = str(episode.kind or "")
            path = audio_episode_file_path(episode)
            if episode.status == "completed" and path is not None and path.exists():
                for chunk in _read_audio_episode_file(path):
                    if first_chunk_ms is None:
                        first_chunk_ms = _duration_ms(started_at)
                        logger.info(
                            "Audio episode stream follower first chunk",
                            extra={
                                "component": "audio_episodes",
                                "operation": "stream_follow",
                                "status": "first_chunk",
                                "duration_ms": first_chunk_ms,
                                "item_id": audio_episode_id,
                                "user_id": user_id,
                                "context_data": {"kind": episode_kind},
                            },
                        )
                    audio_bytes += len(chunk)
                    chunk_count += 1
                    yield chunk

                logger.info(
                    "Audio episode stream follower completed",
                    extra={
                        "component": "audio_episodes",
                        "operation": "stream_follow",
                        "status": "completed",
                        "duration_ms": _duration_ms(started_at),
                        "item_id": audio_episode_id,
                        "user_id": user_id,
                        "context_data": {
                            "kind": episode_kind,
                            "stream_chunk_count": chunk_count,
                            "audio_bytes": audio_bytes,
                            "time_to_first_chunk_ms": first_chunk_ms or 0,
                        },
                    },
                )
                return

            if episode.status == "failed":
                raise RuntimeError(episode.error_message or "Audio episode generation failed")
            if episode.status != "processing":
                if episode.status == "pending" and _script_from_episode(episode) is not None:
                    should_take_over_generation = True
                else:
                    raise AudioEpisodeAlreadyProcessingError(
                        "Audio episode is not actively generating"
                    )

        if should_take_over_generation:
            logger.info(
                "Audio episode stream follower taking over generation",
                extra={
                    "component": "audio_episodes",
                    "operation": "stream_follow",
                    "status": "taking_over_generation",
                    "duration_ms": _duration_ms(started_at),
                    "item_id": audio_episode_id,
                    "user_id": user_id,
                    "context_data": {"kind": episode_kind},
                },
            )
            yield from stream_audio_episode_chunks(
                audio_episode_id=audio_episode_id,
                user_id=user_id,
            )
            return

        time.sleep(AUDIO_EPISODE_FOLLOW_POLL_SECONDS)

    logger.info(
        "Audio episode stream follower timed out",
        extra={
            "component": "audio_episodes",
            "operation": "stream_follow",
            "status": "timed_out",
            "duration_ms": _duration_ms(started_at),
            "item_id": audio_episode_id,
            "user_id": user_id,
            "context_data": {"kind": episode_kind},
        },
    )
    raise AudioEpisodeAlreadyProcessingError("Audio episode is still generating")


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
        if existing.status == "failed":
            existing.status = "pending"
            existing.error_message = None
            existing.script = None
            existing.script_text = None
            existing.audio_storage_path = None
            existing.duration_seconds = None
            existing.started_at = None
            existing.completed_at = None
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
        existing = (
            db.query(AudioEpisode)
            .filter(
                AudioEpisode.user_id == user_id,
                AudioEpisode.kind == kind,
                AudioEpisode.input_hash == input_hash,
            )
            .one()
        )
        return existing
    return episode


def _news_item_source_snapshot(item: NewsItem) -> dict[str, Any]:
    item_id = _required_int(item.id, "news item id")
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
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    title = _content_display_title(content)
    source_text, excerpt_strategy = _excerpt_longform_source_text(body_text)
    return {
        "kind": CONTENT_COUNCIL_DISCUSSION_KIND,
        "content_id": _required_int(content.id, "content id"),
        "content_type": str(content.content_type),
        "title": title,
        "source": content.source,
        "platform": content.platform,
        "url": content.url,
        "publication_date": content.publication_date.isoformat()
        if content.publication_date
        else None,
        "summary": _extract_content_summary(metadata, content=content),
        "source_text": source_text,
        "source_text_excerpt_strategy": excerpt_strategy,
        "source_text_truncated": len(body_text) > LONGFORM_BODY_MAX_CHARS,
    }


def _normalize_custom_narration_content_ids(content_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_content_id in content_ids:
        try:
            content_id = int(raw_content_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Content ids must be integers") from None
        if content_id <= 0:
            raise HTTPException(status_code=400, detail="Content ids must be positive")
        if content_id in seen:
            continue
        seen.add(content_id)
        normalized.append(content_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Select at least one article or podcast")
    if len(normalized) > CUSTOM_NARRATION_MAX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Select at most {CUSTOM_NARRATION_MAX_SOURCES} sources",
        )
    return normalized


def _get_visible_or_saved_content(
    db: Session,
    *,
    user_id: int,
    content_id: int,
) -> Content | None:
    """Return completed content visible from Long Read or saved Knowledge."""

    context = build_visibility_context(user_id)
    return (
        db.query(Content)
        .filter(
            Content.id == content_id,
            Content.status == "completed",
            or_(context.is_in_inbox, context.is_saved_to_knowledge),
            (Content.classification != "skip") | (Content.classification.is_(None)),
        )
        .first()
    )


def _custom_narration_source_item(content: Content, *, body_text: str) -> dict[str, Any]:
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    title = _content_display_title(content)
    normalized_body = body_text.strip()
    return {
        "content_id": _required_int(content.id, "content id"),
        "content_type": str(content.content_type),
        "title": title,
        "source": content.source,
        "platform": content.platform,
        "url": content.url,
        "publication_date": content.publication_date.isoformat()
        if content.publication_date
        else None,
        "summary": _extract_content_summary(metadata, content=content),
        "source_text": normalized_body,
        "source_text_chars": len(normalized_body),
    }


def _custom_narration_title(
    source_items: list[dict[str, Any]],
    *,
    title: str | None,
) -> str:
    normalized_title = (title or "").strip()
    if normalized_title:
        return normalized_title
    if not source_items:
        return "Custom narration"
    first_title = str(source_items[0].get("title") or "Selected sources").strip()
    if len(source_items) == 1:
        return f"Narration: {first_title}"
    return f"Narration: {first_title} + {len(source_items) - 1} more"


def _excerpt_longform_source_text(body_text: str) -> tuple[str, str]:
    """Keep long-form script prompts bounded while preserving source coverage."""

    normalized = body_text.strip()
    if len(normalized) <= LONGFORM_BODY_MAX_CHARS:
        return normalized, "full"

    head = normalized[:LONGFORM_BODY_HEAD_CHARS].rstrip()
    middle_start = max((len(normalized) - LONGFORM_BODY_MIDDLE_CHARS) // 2, 0)
    middle = normalized[middle_start : middle_start + LONGFORM_BODY_MIDDLE_CHARS].strip()
    tail = normalized[-LONGFORM_BODY_TAIL_CHARS:].lstrip()
    return (
        "\n\n[Source opening excerpt]\n"
        f"{head}"
        "\n\n[Source middle excerpt]\n"
        f"{middle}"
        "\n\n[Source closing excerpt]\n"
        f"{tail}",
        "head_middle_tail",
    )


def _content_display_title(content: Content) -> str:
    try:
        return content_to_domain(content).display_title
    except Exception:
        return (content.title or "").strip() or f"Content {content.id}"


def _extract_content_summary(metadata: dict[str, Any], *, content: Content) -> dict[str, Any]:
    summary = metadata.get("summary")
    if isinstance(summary, dict):
        overview = _first_text(
            summary.get("overview"),
            summary.get("short_summary"),
            summary.get("summary"),
            summary.get("narrative"),
            summary.get("text"),
        )
        return {
            "overview": overview or content.short_summary,
            "key_points": _extract_summary_points(summary),
            "raw": summary,
        }
    if isinstance(summary, str):
        return {"overview": summary.strip(), "key_points": []}
    return {"overview": content.short_summary, "key_points": []}


def _extract_summary_points(summary: dict[str, Any]) -> list[str]:
    for key in ("key_points", "bullet_points", "points", "insights"):
        raw_value = summary.get(key)
        if not isinstance(raw_value, list):
            continue
        points: list[str] = []
        for item in raw_value:
            text: str | None
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = _first_text(
                    item.get("text"),
                    item.get("point"),
                    item.get("content"),
                    item.get("insight"),
                )
            else:
                text = None
            if text:
                points.append(text)
        if points:
            return points[:10]
    return []


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _source_snapshot_hash(source_snapshot: dict[str, Any]) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "source_snapshot": source_snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _prepare_audio_episode_script(db: Session, episode: AudioEpisode) -> AudioEpisodeScript:
    """Generate or reuse the structured script for an audio episode."""

    script = _script_from_episode(episode)
    model_spec = str(episode.model or _default_script_model_for_kind(str(episode.kind or "")))
    if script is None:
        generated = _generate_script(episode)
        script = generated.script
        model_spec = generated.model
    return _persist_audio_episode_script(db, episode, script, model=model_spec)


def _persist_audio_episode_script(
    db: Session,
    episode: AudioEpisode,
    script: AudioEpisodeScript,
    *,
    model: str,
) -> AudioEpisodeScript:
    """Persist a generated script and its rendered text on an episode row."""

    script = _fit_script_to_dialogue_limit(
        script,
        limit=_dialogue_text_char_limit_for_kind(str(episode.kind or "")),
    )
    script_text = _render_script_text(script)
    fallback_title = _required_str(episode.title, "audio episode title")
    episode.title = (script.title.strip() or fallback_title)[:255]
    episode.script = script.model_dump(mode="json")
    episode.script_text = script_text
    episode.model = model
    episode.duration_seconds = _estimate_duration_seconds(script_text)
    db.flush()
    return script


def _script_from_episode(episode: AudioEpisode) -> AudioEpisodeScript | None:
    payload = episode.script
    if not isinstance(payload, dict):
        return None
    try:
        return AudioEpisodeScript.model_validate(payload)
    except ValidationError:
        return None


def _copy_episode_for_script_generation(episode: AudioEpisode) -> AudioEpisode:
    return AudioEpisode(
        id=episode.id,
        user_id=episode.user_id,
        kind=episode.kind,
        status=episode.status,
        title=episode.title,
        source_content_id=episode.source_content_id,
        input_hash=episode.input_hash,
        source_item_ids=episode.source_item_ids,
        source_snapshot=episode.source_snapshot,
        prompt_version=episode.prompt_version,
    )


def _generate_script(episode: AudioEpisode) -> AudioEpisodeScriptGeneration:
    user_message = _build_script_prompt(episode)
    last_error: Exception | None = None
    for attempt, model_spec in enumerate(_script_model_candidates(episode), start=1):
        attempt_started_at = time.perf_counter()
        logger.info(
            "Audio episode script generation started",
            extra={
                "component": "audio_episodes",
                "operation": "generate_script",
                "status": "started",
                "item_id": episode.id,
                "user_id": episode.user_id,
                "context_data": {
                    "kind": episode.kind,
                    "model": model_spec,
                    "attempt": attempt,
                },
            },
        )
        try:
            script = _generate_script_with_model(episode, user_message, model_spec)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Audio episode script generation failed",
                extra={
                    "component": "audio_episodes",
                    "operation": "generate_script",
                    "status": "failed",
                    "duration_ms": _duration_ms(attempt_started_at),
                    "item_id": episode.id,
                    "user_id": episode.user_id,
                    "context_data": {
                        "kind": episode.kind,
                        "model": model_spec,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                },
            )
            continue

        logger.info(
            "Audio episode script generation completed",
            extra={
                "component": "audio_episodes",
                "operation": "generate_script",
                "status": "completed",
                "duration_ms": _duration_ms(attempt_started_at),
                "item_id": episode.id,
                "user_id": episode.user_id,
                "context_data": {
                    "kind": episode.kind,
                    "model": model_spec,
                    "attempt": attempt,
                    "turn_count": len(script.turns),
                    "text_chars": sum(len(turn.text) for turn in script.turns),
                    "estimated_duration_seconds": script.estimated_duration_seconds,
                },
            },
        )
        return AudioEpisodeScriptGeneration(script=script, model=model_spec)

    if last_error is not None:
        raise last_error
    raise RuntimeError("No audio episode script generation models are configured")


def _generate_script_with_model(
    episode: AudioEpisode,
    user_message: str,
    model_spec: str,
) -> AudioEpisodeScript:
    agent = get_basic_agent(model_spec, AudioEpisodeScript, SCRIPT_SYSTEM_PROMPT)
    result = agent.run_sync(
        user_message,
        model_settings={"timeout": SCRIPT_TIMEOUT_SECONDS},
    )
    usage = extract_usage_from_result(result)
    if usage:
        record_vendor_usage_out_of_band(
            provider=None,
            model=model_spec,
            feature="audio_episode_script",
            operation="audio_episodes.generate_script",
            source="task",
            usage=usage,
            user_id=_required_int(episode.user_id, "audio episode user_id"),
            content_id=episode.source_content_id,
            metadata={
                "audio_episode_id": episode.id,
                "kind": episode.kind,
                "prompt_version": PROMPT_VERSION,
            },
        )
    return result.output


def _script_model_candidates(episode: AudioEpisode) -> tuple[str, ...]:
    return (_default_script_model_for_kind(str(episode.kind or "")),)


def _default_script_model_for_kind(kind: str) -> str:
    if kind == CUSTOM_NARRATION_KIND:
        return CUSTOM_NARRATION_MODEL
    return AUDIO_EPISODE_MODEL


def _build_script_prompt(episode: AudioEpisode) -> str:
    if episode.kind == FAST_NEWS_DIGEST_KIND:
        return _build_fast_news_prompt(episode.source_snapshot or {})
    if episode.kind == CONTENT_COUNCIL_DISCUSSION_KIND:
        return _build_content_council_prompt(episode.source_snapshot or {})
    if episode.kind == NEWS_ITEM_DISCUSSION_KIND:
        return _build_news_item_discussion_prompt(episode.source_snapshot or {})
    if episode.kind == CUSTOM_NARRATION_KIND:
        return _build_custom_narration_prompt(episode.source_snapshot or {})
    raise ValueError(f"Unsupported audio episode kind: {episode.kind}")


def _build_fast_news_prompt(source_snapshot: dict[str, Any]) -> str:
    return f"""Create a roughly 60 second quick-hit episode from these unread Fast Reads.

Goal:
- Curate the highest-signal highlights across the list, not a rote item-by-item readout.
- Use only summaries and key points below.
- Mention concrete companies, products, people, and numbers when present.
- Group related items into themes when that makes the briefing sharper.
- Keep it brisk, conversational, and useful for someone catching up while walking.

Shape:
- 110-150 spoken words.
- Hard cap: {DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 6-8 turns.
- Start with the top 2-3 headlines and why they matter.
- End with one short "what to watch next" close.

Unread Fast Reads JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _build_content_council_prompt(source_snapshot: dict[str, Any]) -> str:
    source_label = "transcript" if source_snapshot.get("content_type") == "podcast" else "article"
    return f"""Create a roughly 60 second council-of-experts discussion about this
long-form {source_label}.

Goal:
- Use the full supplied {source_label} plus the summary.
- Give listeners the thesis, strongest evidence, implications, and any weak spots or open questions.
- Make it feel like a compact expert roundtable, not a narration of the article.
- Keep the discussion grounded: if a point is not in the source, do not include it.

Shape:
- 110-150 spoken words.
- Hard cap: {DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 6-8 turns.
- Use speaker='host' for framing, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- End with a concise takeaway and why the piece is worth remembering.

Long-form source JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _build_news_item_discussion_prompt(source_snapshot: dict[str, Any]) -> str:
    return f"""Create a roughly 60 second podcast-style discussion about this single Fast Read.

Goal:
- Use only the supplied summary, key points, and links metadata.
- Give listeners the headline, context, stakes, and what to watch next.
- Make it a compact expert roundtable, not a read-aloud summary.
- Do not invent extra facts beyond the source material.

Shape:
- 110-150 spoken words.
- Hard cap: {DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 6-8 turns.
- Use speaker='host' for framing, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- End with a concise takeaway.

Fast Read source JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _build_custom_narration_prompt(source_snapshot: dict[str, Any]) -> str:
    return f"""Create one cohesive podcast-style narration from the selected articles and
podcast transcripts.

Goal:
- Synthesize across all selected sources as one episode, not separate mini-summaries.
- Use the full supplied article text and podcast transcripts. The source text is not chunked.
- Explain the shared themes, contradictions, evidence, and implications.
- Preserve important source-specific details when they materially support the synthesis.
- Keep the discussion grounded: if a point is not in the selected sources, do not include it.

Shape:
- 500-700 spoken words.
- Hard cap: {CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 10-14 turns.
- Use speaker='host' for setup and transitions, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- Start by framing why these sources belong together.
- End with a concise takeaway and what the listener should remember.

Selected source JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _render_script_text(script: AudioEpisodeScript) -> str:
    lines = [script.title.strip()]
    for turn in script.turns:
        label = {
            "host": "Host",
            "cohost": "Cohost",
            "expert": "Expert",
        }.get(turn.speaker, "Speaker")
        lines.append(f"{label}: {turn.text.strip()}")
    return "\n\n".join(line for line in lines if line.strip())


def _fit_script_to_dialogue_limit(
    script: AudioEpisodeScript,
    *,
    limit: int = DIALOGUE_TEXT_CHAR_LIMIT,
) -> AudioEpisodeScript:
    """Trim turn text to fit ElevenLabs dialogue character limits."""

    total_chars = sum(len(turn.text) for turn in script.turns)
    if total_chars <= limit:
        return script

    remaining_chars = limit
    fitted_turns: list[AudioEpisodeTurn] = []
    for index, turn in enumerate(script.turns):
        remaining_turns = len(script.turns) - index
        turn_budget = max(1, remaining_chars // remaining_turns)
        fitted_text = _truncate_dialogue_turn(turn.text, turn_budget)
        fitted_turns.append(turn.model_copy(update={"text": fitted_text}))
        remaining_chars = max(0, remaining_chars - len(fitted_text))

    return script.model_copy(update={"turns": fitted_turns})


def _dialogue_text_char_limit_for_kind(kind: str) -> int:
    if kind == CUSTOM_NARRATION_KIND:
        return CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT
    return DIALOGUE_TEXT_CHAR_LIMIT


def _truncate_dialogue_turn(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized

    truncated = normalized[:max_chars].rstrip()
    sentence_end = max(truncated.rfind(". "), truncated.rfind("? "), truncated.rfind("! "))
    if sentence_end >= max(80, int(max_chars * 0.55)):
        return truncated[: sentence_end + 1].strip()
    trimmed = truncated.rstrip(" ,;:-.")
    if len(trimmed) >= max_chars:
        trimmed = trimmed[: max_chars - 1].rstrip(" ,;:-.")
    return f"{trimmed}."


def _estimate_duration_seconds(script_text: str) -> int:
    word_count = len(script_text.split())
    if word_count <= 0:
        return 0
    return int(math.ceil((word_count / 145) * 60))


def _write_audio_episode_file(audio_episode_id: int, audio_bytes: bytes) -> Path:
    path = _audio_episode_final_file_path(audio_episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    return path


def _audio_episode_final_file_path(audio_episode_id: int) -> Path:
    settings = get_settings()
    return settings.media_base_dir / "audio_episodes" / f"audio-episode-{audio_episode_id}.mp3"


def _audio_episode_partial_file_path(audio_episode_id: int) -> Path:
    return _audio_episode_final_file_path(audio_episode_id).with_suffix(".mp3.part")


def _read_audio_episode_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as audio_file:
        while chunk := audio_file.read(AUDIO_EPISODE_FILE_CHUNK_SIZE):
            yield chunk


def _handle_stream_cancelled(
    db: Session,
    *,
    audio_episode_id: int,
    partial_path: Path | None,
) -> None:
    if partial_path is not None:
        partial_path.unlink(missing_ok=True)

    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None or episode.status != "processing":
        return
    episode.status = "pending"
    episode.error_message = None
    episode.audio_storage_path = None
    episode.started_at = None
    episode.completed_at = None
    db.commit()


def _handle_stream_failed(
    db: Session,
    *,
    audio_episode_id: int,
    partial_path: Path | None,
    error_message: str,
) -> None:
    if partial_path is not None:
        partial_path.unlink(missing_ok=True)

    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None:
        return
    episode.status = "failed"
    episode.error_message = error_message
    episode.audio_storage_path = None
    episode.completed_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()


def _required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"Missing {field_name}")
    return int(value)


def _required_str(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"Missing {field_name}")
    return value


def _required_datetime(value: datetime | None, field_name: str) -> datetime:
    if value is None:
        raise ValueError(f"Missing {field_name}")
    return value


def _audio_episode_kind(value: str | None) -> AudioEpisodeKind:
    if value == FAST_NEWS_DIGEST_KIND:
        return FAST_NEWS_DIGEST_KIND
    if value == CONTENT_COUNCIL_DISCUSSION_KIND:
        return CONTENT_COUNCIL_DISCUSSION_KIND
    if value == NEWS_ITEM_DISCUSSION_KIND:
        return NEWS_ITEM_DISCUSSION_KIND
    if value == CUSTOM_NARRATION_KIND:
        return CUSTOM_NARRATION_KIND
    raise ValueError(f"Unsupported audio episode kind: {value}")


def _audio_episode_status(value: str | None) -> AudioEpisodeStatus:
    if value == "pending":
        return "pending"
    if value == "processing":
        return "processing"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    raise ValueError(f"Unsupported audio episode status: {value}")


def _episode_source_content_ids(episode: AudioEpisode) -> list[int]:
    if episode.source_content_id is not None:
        return [int(episode.source_content_id)]
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    raw_content_ids = snapshot.get("content_ids")
    if isinstance(raw_content_ids, list):
        return _int_list_from_snapshot_values(raw_content_ids)
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []
    content_ids: list[int] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        content_id = item.get("content_id")
        parsed_id = _int_from_snapshot_value(content_id)
        if parsed_id is None:
            continue
        content_ids.append(parsed_id)
    return content_ids


def _episode_source_titles(episode: AudioEpisode) -> list[str]:
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    if episode.source_content_id is not None:
        title = snapshot.get("title")
        return [str(title)] if title else []
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []
    titles: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _episode_source_count(episode: AudioEpisode) -> int:
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    raw_count = snapshot.get("source_count")
    if isinstance(raw_count, int) and raw_count >= 0:
        return raw_count
    source_content_ids = _episode_source_content_ids(episode)
    if source_content_ids:
        return len(source_content_ids)
    source_item_ids = episode.source_item_ids or []
    return len(source_item_ids)


def _int_list_from_snapshot_values(values: list[Any]) -> list[int]:
    parsed_values: list[int] = []
    for value in values:
        parsed_value = _int_from_snapshot_value(value)
        if parsed_value is not None:
            parsed_values.append(parsed_value)
    return parsed_values


def _int_from_snapshot_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truncate_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
