"""Audio episode API presentation, delivery, and read-on-play behavior."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.api.audio_episodes import (
    AudioEpisodeDelivery,
    AudioEpisodeKind,
    AudioEpisodeResponse,
    AudioEpisodeStatus,
)
from app.models.db import AudioEpisode
from app.repositories import read_status_repository
from app.services.audio_episode_kinds import (
    AUDIO_EPISODE_KIND_SPECS,
    BRIEFING_NARRATION_KIND,
    CONTENT_COUNCIL_DISCUSSION_KIND,
    CUSTOM_NARRATION_KIND,
    FAST_NEWS_DIGEST_KIND,
    NEWS_ITEM_DISCUSSION_KIND,
)
from app.services.audio_episodes.creation import enqueue_audio_episode_generation
from app.services.audio_episodes.generation import (
    finalize_audio_episode_failure,
    generate_audio_episode,
)
from app.services.audio_episodes.shared import (
    PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE,
    int_from_snapshot_value,
    int_list_from_snapshot_values,
    required_datetime,
    required_int,
    required_str,
)
from app.services.news_feed import bulk_mark_news_items_read


def present_audio_episode(episode: AudioEpisode) -> AudioEpisodeResponse:
    """Build the safe client-visible representation of an episode."""

    episode_id = required_int(episode.id, "audio episode id")
    audio_url = None
    if episode.status == "completed" and episode.audio_storage_path:
        audio_url = f"/api/content/audio-episodes/{episode_id}/audio"
    return AudioEpisodeResponse(
        id=episode_id,
        kind=_audio_episode_kind(episode.kind),
        status=_audio_episode_status(episode.status),
        title=required_str(episode.title, "audio episode title"),
        source_content_id=episode.source_content_id,
        source_item_ids=[int(item_id) for item_id in (episode.source_item_ids or [])],
        source_content_ids=_episode_source_content_ids(episode),
        source_count=_episode_source_count(episode),
        source_titles=_episode_source_titles(episode),
        read_on_play_content_ids=_episode_read_on_play_content_ids(episode),
        read_on_play_news_item_ids=_episode_read_on_play_news_item_ids(episode),
        duration_seconds=episode.duration_seconds,
        audio_url=audio_url,
        stream_url=f"/api/content/audio-episodes/{episode_id}/stream",
        script_text=episode.script_text,
        error_message=(PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE if episode.status == "failed" else None),
        created_at=required_datetime(episode.created_at, "audio episode created_at"),
        updated_at=episode.updated_at,
    )


def commit_audio_episode_delivery(
    db: Session,
    episode: AudioEpisode,
    *,
    delivery: AudioEpisodeDelivery,
) -> AudioEpisodeResponse:
    """Commit creation, then enqueue or explicitly run inline generation."""

    db.commit()
    db.refresh(episode)
    if delivery == "background" and episode.status != "completed":
        enqueue_audio_episode_generation(required_int(episode.id, "audio episode id"))
    elif delivery == "inline" and episode.status != "completed":
        episode_id = required_int(episode.id, "audio episode id")
        try:
            episode = generate_audio_episode(db, audio_episode_id=episode_id)
        except Exception as exc:
            finalize_audio_episode_failure(
                db,
                audio_episode_id=episode_id,
                error=exc,
                retry_scheduled=False,
            )
            db.commit()
            raise
        db.commit()
        db.refresh(episode)
    return present_audio_episode(episode)


def mark_audio_episode_sources_read_on_play(
    db: Session,
    *,
    episode: AudioEpisode,
) -> dict[str, Any]:
    """Mark narration source rows read when the owner starts playback."""

    spec = AUDIO_EPISODE_KIND_SPECS.get(str(episode.kind))
    if spec is None or not spec.marks_sources_read_on_play:
        return {
            "content_marked_count": 0,
            "content_failed_ids": [],
            "news_marked_count": 0,
            "news_failed_ids": [],
        }

    user_id = required_int(episode.user_id, "audio episode user_id")
    content_ids = _episode_read_on_play_content_ids(episode)
    news_item_ids = _episode_read_on_play_news_item_ids(episode)
    content_marked_count = 0
    content_failed_ids: list[int] = []
    if content_ids:
        content_marked_count, content_failed_ids = read_status_repository.mark_contents_as_read(
            db,
            content_ids,
            user_id,
        )

    news_marked_count = 0
    news_failed_ids: list[int] = []
    if news_item_ids:
        result = bulk_mark_news_items_read(
            db,
            user_id=user_id,
            news_item_ids=news_item_ids,
        )
        news_marked_count = result.marked_count
        news_failed_ids = int_list_from_snapshot_values(result.failed_ids)
    return {
        "content_marked_count": content_marked_count,
        "content_failed_ids": content_failed_ids,
        "news_marked_count": news_marked_count,
        "news_failed_ids": news_failed_ids,
    }


def _audio_episode_kind(value: str | None) -> AudioEpisodeKind:
    mapping = {
        FAST_NEWS_DIGEST_KIND: AudioEpisodeKind.FAST_NEWS_DIGEST,
        CONTENT_COUNCIL_DISCUSSION_KIND: AudioEpisodeKind.CONTENT_COUNCIL_DISCUSSION,
        NEWS_ITEM_DISCUSSION_KIND: AudioEpisodeKind.NEWS_ITEM_DISCUSSION,
        CUSTOM_NARRATION_KIND: AudioEpisodeKind.CUSTOM_NARRATION,
        BRIEFING_NARRATION_KIND: AudioEpisodeKind.BRIEFING_NARRATION,
    }
    try:
        return mapping[str(value)]
    except KeyError:
        raise ValueError(f"Unsupported audio episode kind: {value}") from None


def _audio_episode_status(value: str | None) -> AudioEpisodeStatus:
    try:
        return AudioEpisodeStatus(str(value))
    except ValueError:
        raise ValueError(f"Unsupported audio episode status: {value}") from None


def _episode_source_content_ids(episode: AudioEpisode) -> list[int]:
    if episode.source_content_id is not None:
        return [int(episode.source_content_id)]
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    raw_content_ids = snapshot.get("content_ids")
    if isinstance(raw_content_ids, list):
        return int_list_from_snapshot_values(raw_content_ids)
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []
    return [
        parsed_id
        for item in raw_items
        if isinstance(item, dict)
        and (parsed_id := int_from_snapshot_value(item.get("content_id"))) is not None
    ]


def _episode_source_titles(episode: AudioEpisode) -> list[str]:
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    if episode.source_content_id is not None:
        title = snapshot.get("title")
        return [str(title)] if title else []
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []
    return [
        title
        for item in raw_items
        if isinstance(item, dict) and (title := str(item.get("title") or "").strip())
    ]


def _episode_source_count(episode: AudioEpisode) -> int:
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    raw_count = snapshot.get("source_count")
    if isinstance(raw_count, int) and raw_count >= 0:
        return raw_count
    source_content_ids = _episode_source_content_ids(episode)
    return len(source_content_ids) if source_content_ids else len(episode.source_item_ids or [])


def _episode_read_on_play_content_ids(episode: AudioEpisode) -> list[int]:
    raw_ids = _episode_read_on_play_policy(episode).get("content_ids")
    return int_list_from_snapshot_values(raw_ids) if isinstance(raw_ids, list) else []


def _episode_read_on_play_news_item_ids(episode: AudioEpisode) -> list[int]:
    raw_ids = _episode_read_on_play_policy(episode).get("news_item_ids")
    return int_list_from_snapshot_values(raw_ids) if isinstance(raw_ids, list) else []


def _episode_read_on_play_policy(episode: AudioEpisode) -> dict[str, Any]:
    snapshot = episode.source_snapshot if isinstance(episode.source_snapshot, dict) else {}
    read_policy = snapshot.get("read_on_play")
    return read_policy if isinstance(read_policy, dict) else {}


__all__ = [
    "commit_audio_episode_delivery",
    "mark_audio_episode_sources_read_on_play",
    "present_audio_episode",
]
