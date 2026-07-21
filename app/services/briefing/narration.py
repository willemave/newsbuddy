from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.models.api.audio_episodes import AudioEpisodeDelivery, AudioEpisodeResponse
from app.models.api.briefing import BriefingNarrationResponse
from app.models.contracts import AudioEpisodeKind, AudioEpisodeStatus
from app.models.db import AudioEpisode, BriefingLens, BriefingSegment
from app.services.audio_episodes import (
    commit_audio_episode_deliveries,
    commit_audio_episode_delivery,
    present_audio_episode,
)
from app.services.audio_episodes.scripting import estimate_duration_seconds
from app.services.audio_episodes.shared import required_str
from app.services.briefing.source_keys import parse_source_key

BRIEFING_NARRATION_PROMPT_VERSION = 3
LEGACY_BRIEFING_NARRATION_PROMPT_VERSION = 2
BRIEFING_NARRATION_CHAPTER_TARGET_SECONDS = 5 * 60


@dataclass(frozen=True)
class BriefingNarrationChapterPlan:
    index: int
    segment_ids: tuple[int, ...]
    source_keys: tuple[str, ...]
    narration_text: str
    duration_seconds: int


def create_or_reuse_briefing_narration(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
    delivery: AudioEpisodeDelivery,
) -> BriefingNarrationResponse:
    lens, segments = _load_lens_and_segments(db, user_id=user_id, lens_key=lens_key)
    plans = plan_briefing_narration_chapters(segments)
    if not plans:
        raise HTTPException(status_code=400, detail="No briefing narration is available")

    resolved_lens_key = required_str(lens.key, "briefing lens key")
    resolved_lens_title = required_str(lens.title, "briefing lens title")
    group_id = _episode_group_id(
        lens_key=resolved_lens_key,
        lens_title=resolved_lens_title,
        plans=plans,
    )
    episodes = [
        _create_or_reuse_chapter(
            db,
            user_id=user_id,
            lens_key=resolved_lens_key,
            lens_title=resolved_lens_title,
            episode_group_id=group_id,
            chapter_count=len(plans),
            plan=plan,
        )
        for plan in plans
    ]
    delivered = commit_audio_episode_deliveries(db, episodes, delivery=delivery)
    return present_briefing_narration(delivered)


def create_or_reuse_legacy_briefing_narration(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
    delivery: AudioEpisodeDelivery,
) -> AudioEpisodeResponse:
    """Preserve the original one-row response for installed app versions."""

    lens, segments = _load_lens_and_segments(db, user_id=user_id, lens_key=lens_key)
    script_text = _script_text(segments)
    if not script_text:
        raise HTTPException(status_code=400, detail="No briefing narration is available")

    source_keys = _source_keys(segments)
    source_snapshot: dict[str, object] = {
        "kind": AudioEpisodeKind.BRIEFING_NARRATION.value,
        "lens_key": lens.key,
        "lens_title": lens.title,
        "source_count": len(source_keys),
        "segment_ids": [_required_segment_id(segment) for segment in segments],
        "source_keys": source_keys,
        "read_on_play": _read_on_play(source_keys),
        "script_text": script_text,
    }
    input_hash = _hash_payload(
        {
            "prompt_version": LEGACY_BRIEFING_NARRATION_PROMPT_VERSION,
            "source_snapshot": source_snapshot,
        }
    )
    episode = (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == AudioEpisodeKind.BRIEFING_NARRATION.value,
            AudioEpisode.input_hash == input_hash,
        )
        .first()
    )
    title = f"{lens.title} briefing"
    script_payload = _script_payload(
        title=title,
        text=script_text,
        estimated_duration_seconds=max(30, round(len(script_text) / 14)),
    )
    if episode is None:
        episode = AudioEpisode(
            user_id=user_id,
            kind=AudioEpisodeKind.BRIEFING_NARRATION.value,
            status=AudioEpisodeStatus.PENDING.value,
            title=title,
            input_hash=input_hash,
            source_item_ids=[],
            source_snapshot=source_snapshot,
            script=script_payload,
            script_text=script_text,
            prompt_version=LEGACY_BRIEFING_NARRATION_PROMPT_VERSION,
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

    episode.title = title
    episode.input_hash = input_hash
    episode.source_item_ids = []
    episode.source_snapshot = source_snapshot
    episode.script = script_payload
    episode.script_text = script_text
    episode.prompt_version = LEGACY_BRIEFING_NARRATION_PROMPT_VERSION
    episode.model = "deterministic"
    if episode.status == AudioEpisodeStatus.FAILED.value:
        episode.status = AudioEpisodeStatus.PENDING.value
        episode.error_message = None
        episode.audio_storage_path = None
        episode.duration_seconds = None
        episode.started_at = None
        episode.completed_at = None
    return commit_audio_episode_delivery(db, episode, delivery=delivery)


def _load_lens_and_segments(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
) -> tuple[BriefingLens, list[BriefingSegment]]:
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
    return lens, segments


def get_briefing_narration(
    db: Session,
    *,
    user_id: int,
    episode_group_id: str,
) -> BriefingNarrationResponse | None:
    episodes = (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == AudioEpisodeKind.BRIEFING_NARRATION.value,
            AudioEpisode.episode_group_id == episode_group_id,
        )
        .order_by(AudioEpisode.chapter_index.asc(), AudioEpisode.id.asc())
        .all()
    )
    if not episodes:
        return None
    return present_briefing_narration(episodes)


def plan_briefing_narration_chapters(
    segments: list[BriefingSegment],
    *,
    target_seconds: int = BRIEFING_NARRATION_CHAPTER_TARGET_SECONDS,
) -> list[BriefingNarrationChapterPlan]:
    """Pack newest-first segments near the target without splitting a segment."""

    if target_seconds < 1:
        raise ValueError("Briefing narration chapter target must be positive")

    speakable = [segment for segment in segments if str(segment.narration_text or "").strip()]
    grouped: list[list[BriefingSegment]] = []
    current: list[BriefingSegment] = []
    current_duration = 0
    for segment in speakable:
        segment_text = str(segment.narration_text or "").strip()
        segment_duration = max(1, estimate_duration_seconds(segment_text))
        combined_duration = current_duration + segment_duration
        current_distance = abs(target_seconds - current_duration)
        combined_distance = abs(target_seconds - combined_duration)
        if current and current_distance < combined_distance:
            grouped.append(current)
            current = [segment]
            current_duration = segment_duration
            continue
        current.append(segment)
        current_duration = combined_duration
    if current:
        grouped.append(current)

    plans: list[BriefingNarrationChapterPlan] = []
    for index, chapter_segments in enumerate(grouped):
        narration_text = _script_text(chapter_segments)
        plans.append(
            BriefingNarrationChapterPlan(
                index=index,
                segment_ids=tuple(_required_segment_id(segment) for segment in chapter_segments),
                source_keys=tuple(_source_keys(chapter_segments)),
                narration_text=narration_text,
                duration_seconds=max(1, estimate_duration_seconds(narration_text)),
            )
        )
    return plans


def present_briefing_narration(episodes: list[AudioEpisode]) -> BriefingNarrationResponse:
    ordered = sorted(
        episodes,
        key=lambda episode: (
            int(episode.chapter_index) if episode.chapter_index is not None else 0,
            int(episode.id or 0),
        ),
    )
    if not ordered:
        raise ValueError("Briefing narration has no chapters")

    first = ordered[0]
    snapshot = first.source_snapshot if isinstance(first.source_snapshot, dict) else {}
    group_id = str(first.episode_group_id or "").strip()
    lens_key = str(snapshot.get("lens_key") or "").strip()
    lens_title = str(snapshot.get("lens_title") or "Briefing").strip()
    if not group_id or not lens_key:
        raise ValueError("Briefing narration chapter metadata is incomplete")

    playable = first.status == AudioEpisodeStatus.COMPLETED.value
    if all(episode.status == AudioEpisodeStatus.COMPLETED.value for episode in ordered):
        status = AudioEpisodeStatus.COMPLETED
    elif first.status == AudioEpisodeStatus.FAILED.value:
        status = AudioEpisodeStatus.FAILED
    elif playable or any(
        episode.status == AudioEpisodeStatus.PROCESSING.value for episode in ordered
    ):
        status = AudioEpisodeStatus.PROCESSING
    else:
        status = AudioEpisodeStatus.PENDING

    return BriefingNarrationResponse(
        episode_group_id=group_id,
        lens_key=lens_key,
        title=f"{lens_title} briefing",
        status=status,
        playable=playable,
        duration_seconds=sum(max(int(episode.duration_seconds or 0), 0) for episode in ordered),
        chapters=[present_audio_episode(episode) for episode in ordered],
    )


def _create_or_reuse_chapter(
    db: Session,
    *,
    user_id: int,
    lens_key: str,
    lens_title: str,
    episode_group_id: str,
    chapter_count: int,
    plan: BriefingNarrationChapterPlan,
) -> AudioEpisode:
    source_snapshot: dict[str, object] = {
        "kind": AudioEpisodeKind.BRIEFING_NARRATION.value,
        "episode_group_id": episode_group_id,
        "chapter_index": plan.index,
        "chapter_count": chapter_count,
        "lens_key": lens_key,
        "lens_title": lens_title,
        "source_count": len(plan.source_keys),
        "segment_ids": list(plan.segment_ids),
        "source_keys": list(plan.source_keys),
        "read_on_play": _read_on_play(list(plan.source_keys)),
        "script_text": plan.narration_text,
    }
    input_hash = _chapter_input_hash(
        episode_group_id=episode_group_id,
        chapter_index=plan.index,
        source_snapshot=source_snapshot,
    )
    episode = (
        db.query(AudioEpisode)
        .filter(
            AudioEpisode.user_id == user_id,
            AudioEpisode.kind == AudioEpisodeKind.BRIEFING_NARRATION.value,
            AudioEpisode.input_hash == input_hash,
        )
        .first()
    )
    title = f"{lens_title} briefing — Chapter {plan.index + 1}"
    script_payload = _script_payload(
        title=title,
        text=plan.narration_text,
        estimated_duration_seconds=plan.duration_seconds,
    )
    if episode is None:
        episode = AudioEpisode(
            user_id=user_id,
            kind=AudioEpisodeKind.BRIEFING_NARRATION.value,
            status=AudioEpisodeStatus.PENDING.value,
            title=title,
            input_hash=input_hash,
            episode_group_id=episode_group_id,
            chapter_index=plan.index,
            source_item_ids=[],
            source_snapshot=source_snapshot,
            script=script_payload,
            script_text=plan.narration_text,
            prompt_version=BRIEFING_NARRATION_PROMPT_VERSION,
            model="deterministic",
            duration_seconds=plan.duration_seconds,
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

    _synchronize_chapter(
        episode,
        title=title,
        input_hash=input_hash,
        episode_group_id=episode_group_id,
        plan=plan,
        source_snapshot=source_snapshot,
        script_payload=script_payload,
    )
    return episode


def _synchronize_chapter(
    episode: AudioEpisode,
    *,
    title: str,
    input_hash: str,
    episode_group_id: str,
    plan: BriefingNarrationChapterPlan,
    source_snapshot: dict[str, object],
    script_payload: dict[str, object],
) -> None:
    episode.title = title
    episode.input_hash = input_hash
    episode.episode_group_id = episode_group_id
    episode.chapter_index = plan.index
    episode.source_item_ids = []
    episode.source_snapshot = source_snapshot
    episode.script = script_payload
    episode.script_text = plan.narration_text
    episode.prompt_version = BRIEFING_NARRATION_PROMPT_VERSION
    episode.model = "deterministic"
    if episode.status == AudioEpisodeStatus.FAILED.value:
        episode.status = AudioEpisodeStatus.PENDING.value
        episode.error_message = None
        episode.audio_storage_path = None
        episode.started_at = None
        episode.completed_at = None
    if episode.status != AudioEpisodeStatus.COMPLETED.value:
        episode.duration_seconds = plan.duration_seconds


def _episode_group_id(
    *,
    lens_key: str,
    lens_title: str,
    plans: list[BriefingNarrationChapterPlan],
) -> str:
    payload = {
        "prompt_version": BRIEFING_NARRATION_PROMPT_VERSION,
        "kind": AudioEpisodeKind.BRIEFING_NARRATION.value,
        "lens_key": lens_key,
        "lens_title": lens_title,
        "chapters": [
            {
                "chapter_index": plan.index,
                "segment_ids": list(plan.segment_ids),
                "source_keys": list(plan.source_keys),
                "script_text": plan.narration_text,
            }
            for plan in plans
        ],
    }
    return _hash_payload(payload)


def _chapter_input_hash(
    *,
    episode_group_id: str,
    chapter_index: int,
    source_snapshot: dict[str, object],
) -> str:
    return _hash_payload(
        {
            "prompt_version": BRIEFING_NARRATION_PROMPT_VERSION,
            "episode_group_id": episode_group_id,
            "chapter_index": chapter_index,
            "source_snapshot": source_snapshot,
        }
    )


def _hash_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def _script_payload(
    *,
    title: str,
    text: str,
    estimated_duration_seconds: int,
) -> dict[str, object]:
    return {
        "title": title,
        "estimated_duration_seconds": estimated_duration_seconds,
        "turns": [{"speaker": "host", "text": text}],
    }


def _required_segment_id(segment: BriefingSegment) -> int:
    if segment.id is None:
        raise ValueError("Briefing narration segment is missing an id")
    return int(segment.id)


__all__ = [
    "BRIEFING_NARRATION_CHAPTER_TARGET_SECONDS",
    "BRIEFING_NARRATION_PROMPT_VERSION",
    "LEGACY_BRIEFING_NARRATION_PROMPT_VERSION",
    "BriefingNarrationChapterPlan",
    "create_or_reuse_briefing_narration",
    "create_or_reuse_legacy_briefing_narration",
    "get_briefing_narration",
    "plan_briefing_narration_chapters",
    "present_briefing_narration",
]
