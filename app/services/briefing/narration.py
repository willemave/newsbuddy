from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.models.api.audio_episodes import AudioEpisodeDelivery, AudioEpisodeResponse
from app.models.contracts import AudioEpisodeKind
from app.models.db import AudioEpisode, BriefingLens, BriefingSegment
from app.services.audio_episodes import (
    commit_audio_episode_delivery,
)
from app.services.briefing.source_keys import parse_source_key

BRIEFING_NARRATION_PROMPT_VERSION = 2


def create_or_reuse_briefing_narration(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
    delivery: AudioEpisodeDelivery,
) -> AudioEpisodeResponse:
    lens = (
        db.query(BriefingLens)
        .options(load_only(BriefingLens.id, BriefingLens.key, BriefingLens.title))
        .filter(
            BriefingLens.user_id == user_id,
            BriefingLens.key == lens_key,
            BriefingLens.status == "active",
        )
        .first()
    )
    if lens is None:
        raise HTTPException(status_code=404, detail="Briefing lens not found")
    segments = (
        db.query(BriefingSegment)
        .options(
            load_only(
                BriefingSegment.id,
                BriefingSegment.narration_text,
                BriefingSegment.source_keys,
            )
        )
        .filter(BriefingSegment.lens_id == lens.id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .order_by(BriefingSegment.created_at.desc(), BriefingSegment.id.desc())
        .all()
    )
    script_text = _script_text(segments)
    if not script_text:
        raise HTTPException(status_code=400, detail="No briefing narration is available")
    source_keys = _source_keys(segments)
    source_snapshot: dict[str, object] = {
        "kind": AudioEpisodeKind.BRIEFING_NARRATION.value,
        "lens_key": lens.key,
        "lens_title": lens.title,
        "source_count": len(source_keys),
        "segment_ids": [int(segment.id) for segment in segments if segment.id is not None],
        "source_keys": source_keys,
        "read_on_play": _read_on_play(source_keys),
        "script_text": script_text,
    }
    input_hash = _source_snapshot_hash(source_snapshot)
    episode = (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == AudioEpisodeKind.BRIEFING_NARRATION.value,
            AudioEpisode.input_hash == input_hash,
        )
        .first()
    )
    if episode is None:
        episode = AudioEpisode(
            user_id=user_id,
            kind=AudioEpisodeKind.BRIEFING_NARRATION.value,
            status="pending",
            title=f"{lens.title} briefing",
            input_hash=input_hash,
            source_item_ids=[],
            source_snapshot=source_snapshot,
            script=_script_payload(title=f"{lens.title} briefing", text=script_text),
            script_text=script_text,
            prompt_version=BRIEFING_NARRATION_PROMPT_VERSION,
            model="deterministic",
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
                    AudioEpisode.kind == AudioEpisodeKind.BRIEFING_NARRATION.value,
                    AudioEpisode.input_hash == input_hash,
                )
                .one()
            )
    _synchronize_episode(
        episode,
        title=f"{lens.title} briefing",
        input_hash=input_hash,
        source_snapshot=source_snapshot,
        script_text=script_text,
    )
    return commit_audio_episode_delivery(db, episode, delivery=delivery)


def _synchronize_episode(
    episode: AudioEpisode,
    *,
    title: str,
    input_hash: str,
    source_snapshot: dict[str, object],
    script_text: str,
) -> None:
    """Apply current-hash content and make an exact failed episode retryable."""

    episode.title = title
    episode.input_hash = input_hash
    episode.source_item_ids = []
    episode.source_snapshot = source_snapshot
    episode.script = _script_payload(title=title, text=script_text)
    episode.script_text = script_text
    episode.prompt_version = BRIEFING_NARRATION_PROMPT_VERSION
    episode.model = "deterministic"
    if episode.status == "failed":
        episode.status = "pending"
        episode.error_message = None
        episode.audio_storage_path = None
        episode.duration_seconds = None
        episode.started_at = None
        episode.completed_at = None


def _script_text(segments: list[BriefingSegment]) -> str:
    parts = [str(segment.narration_text or "").strip() for segment in segments]
    return "\n\n".join(part for part in parts if part)


def _source_keys(segments: list[BriefingSegment]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for segment in segments:
        for raw_key in segment.source_keys or []:
            key = str(raw_key)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys


def _read_on_play(source_keys: list[str]) -> dict[str, list[int]]:
    parsed = [parse_source_key(key) for key in source_keys]
    return {
        "content_ids": sorted({key.source_id for key in parsed if key and key.kind == "content"}),
        "news_item_ids": sorted({key.source_id for key in parsed if key and key.kind == "news"}),
    }


def _source_snapshot_hash(source_snapshot: dict[str, object]) -> str:
    payload = {
        "prompt_version": BRIEFING_NARRATION_PROMPT_VERSION,
        "source_snapshot": source_snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _script_payload(*, title: str, text: str) -> dict[str, object]:
    return {
        "title": title,
        "estimated_duration_seconds": max(30, round(len(text) / 14)),
        "turns": [{"speaker": "host", "text": text}],
    }
