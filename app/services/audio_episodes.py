"""On-demand podcast-style audio episode generation."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.model_defaults import SMART_MODEL_SPEC
from app.core.settings import get_settings
from app.models.api.audio_episodes import (
    AudioEpisodeKind,
    AudioEpisodeResponse,
    AudioEpisodeStatus,
)
from app.models.contracts import ContentType, TaskType
from app.models.db import AudioEpisode, Content, NewsItem
from app.models.domain.content_mapper import content_to_domain
from app.repositories.content_detail_repository import get_visible_content
from app.services.content_bodies import get_content_body_resolver
from app.services.llm_agents import get_basic_agent
from app.services.news_feed import list_unread_visible_news_items
from app.services.queue import get_queue_service
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band
from app.services.voice.narration_tts import get_content_narration_tts_service
from app.utils.news_titles import resolve_news_display_title

logger = get_logger(__name__)

FAST_NEWS_DIGEST_KIND: Literal["fast_news_digest"] = "fast_news_digest"
CONTENT_COUNCIL_DISCUSSION_KIND: Literal["content_council_discussion"] = (
    "content_council_discussion"
)
PROMPT_VERSION = 1
FAST_NEWS_LIMIT = 200
LONGFORM_BODY_MAX_CHARS = 120_000
AUDIO_EPISODE_MODEL = SMART_MODEL_SPEC
SCRIPT_TIMEOUT_SECONDS = 180
DIALOGUE_TEXT_CHAR_LIMIT = 4_600
AUDIO_EPISODE_PROCESSING_STALE_AFTER = timedelta(minutes=15)
AUDIO_EPISODE_FILE_CHUNK_SIZE = 1024 * 256

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
    text: str = Field(..., min_length=1, max_length=900)


class AudioEpisodeScript(BaseModel):
    """Structured output for one generated audio episode."""

    title: str = Field(..., min_length=1, max_length=120)
    estimated_duration_seconds: int = Field(..., ge=180, le=420)
    turns: list[AudioEpisodeTurn] = Field(..., min_length=6, max_length=18)


class AudioEpisodeAlreadyProcessingError(RuntimeError):
    """Raised when a live generator is already producing the episode."""


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
    source_snapshot = {
        "kind": FAST_NEWS_DIGEST_KIND,
        "total_unread": total_unread,
        "included_count": len(source_items),
        "items": source_items,
    }
    title = "Fast Reads Brief"
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=FAST_NEWS_DIGEST_KIND,
        title=title,
        source_content_id=None,
        source_item_ids=[int(item.id) for item in items if item.id is not None],
        source_snapshot=source_snapshot,
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
    title = f"Expert discussion: {source_snapshot['title']}"
    return _create_or_reuse_episode(
        db,
        user_id=user_id,
        kind=CONTENT_COUNCIL_DISCUSSION_KIND,
        title=title[:255],
        source_content_id=content_id,
        source_item_ids=[],
        source_snapshot=source_snapshot,
    )


def enqueue_audio_episode_generation(audio_episode_id: int) -> int:
    """Enqueue background generation for an audio episode."""

    return get_queue_service().enqueue(
        TaskType.GENERATE_AUDIO_EPISODE,
        payload={"audio_episode_id": audio_episode_id},
        dedupe_key=f"audio_episode:{audio_episode_id}",
    )


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
        duration_seconds=episode.duration_seconds,
        audio_url=audio_url,
        stream_url=stream_url,
        script_text=episode.script_text,
        error_message=episode.error_message,
        created_at=_required_datetime(episode.created_at, "audio episode created_at"),
        updated_at=episode.updated_at,
    )


def audio_episode_file_path(episode: AudioEpisode) -> Path | None:
    """Return the local MP3 path for a generated episode."""

    storage_path = str(episode.audio_storage_path or "").strip()
    if not storage_path:
        return None
    return Path(storage_path)


def generate_audio_episode(db: Session, *, audio_episode_id: int) -> AudioEpisode:
    """Generate script and audio for one persisted audio episode."""

    episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
    if episode is None:
        raise ValueError(f"Audio episode {audio_episode_id} not found")
    if episode.status == "completed" and episode.audio_storage_path:
        return episode
    if episode.status == "processing" and not is_audio_episode_processing_stale(episode):
        return episode

    episode_id = _required_int(episode.id, "audio episode id")
    user_id = _required_int(episode.user_id, "audio episode user_id")
    now = datetime.now(UTC).replace(tzinfo=None)
    episode.status = "processing"
    episode.error_message = None
    episode.started_at = now
    db.flush()

    try:
        script = _prepare_audio_episode_script(db, episode)
        script_text = _required_str(episode.script_text, "audio episode script_text")
        audio_bytes = get_content_narration_tts_service().synthesize_dialogue_mp3(
            turns=[turn.model_dump(mode="json") for turn in script.turns],
            item_id=episode_id,
            user_id=user_id,
        )
        audio_path = _write_audio_episode_file(episode_id, audio_bytes)
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
                "item_id": audio_episode_id,
                "context_data": {
                    "kind": episode.kind,
                    "user_id": episode.user_id,
                    "error": str(exc),
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
    try:
        with SessionLocal() as db:
            episode = get_user_audio_episode(
                db,
                user_id=user_id,
                audio_episode_id=audio_episode_id,
            )
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")

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
                if script is None:
                    script_input = _copy_episode_for_script_generation(episode)
                db.commit()

        if cached_path is not None:
            yield from _read_audio_episode_file(cached_path)
            return

        if script is None:
            if script_input is None:
                raise RuntimeError("Audio episode script input was not prepared")
            script_started_at = time.perf_counter()
            script = _generate_script(script_input)
            script_duration_ms = (time.perf_counter() - script_started_at) * 1000

        with SessionLocal() as db:
            episode = db.query(AudioEpisode).filter(AudioEpisode.id == audio_episode_id).first()
            if episode is None:
                raise ValueError(f"Audio episode {audio_episode_id} not found")
            script = _persist_audio_episode_script(db, episode, script)
            script_text = _required_str(episode.script_text, "audio episode script_text")
            episode_kind = str(episode.kind or "")
            db.commit()

        partial_path = _audio_episode_partial_file_path(audio_episode_id)
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.unlink(missing_ok=True)
        final_path = _audio_episode_final_file_path(audio_episode_id)

        tts_started_at = time.perf_counter()
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
                "item_id": audio_episode_id,
                "context_data": {
                    "user_id": user_id,
                    "kind": episode_kind,
                    "script_duration_ms": round(script_duration_ms, 2),
                    "tts_time_to_first_chunk_ms": round(first_chunk_ms or 0, 2),
                    "stream_total_duration_ms": round(
                        (time.perf_counter() - stream_started_at) * 1000,
                        2,
                    ),
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
                "item_id": audio_episode_id,
                "context_data": {
                    "user_id": user_id,
                    "error": str(exc),
                    "chunk_count": chunk_count,
                    "audio_bytes": audio_bytes,
                },
            },
        )
        raise


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
        "source_text": _truncate_text(body_text, LONGFORM_BODY_MAX_CHARS),
        "source_text_truncated": len(body_text) > LONGFORM_BODY_MAX_CHARS,
    }


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
    if script is None:
        script = _generate_script(episode)
    return _persist_audio_episode_script(db, episode, script)


def _persist_audio_episode_script(
    db: Session,
    episode: AudioEpisode,
    script: AudioEpisodeScript,
) -> AudioEpisodeScript:
    """Persist a generated script and its rendered text on an episode row."""

    script = _fit_script_to_dialogue_limit(script)
    script_text = _render_script_text(script)
    fallback_title = _required_str(episode.title, "audio episode title")
    episode.title = (script.title.strip() or fallback_title)[:255]
    episode.script = script.model_dump(mode="json")
    episode.script_text = script_text
    episode.model = AUDIO_EPISODE_MODEL
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


def _generate_script(episode: AudioEpisode) -> AudioEpisodeScript:
    user_message = _build_script_prompt(episode)
    agent = get_basic_agent(AUDIO_EPISODE_MODEL, AudioEpisodeScript, SCRIPT_SYSTEM_PROMPT)
    result = agent.run_sync(
        user_message,
        model_settings={"timeout": SCRIPT_TIMEOUT_SECONDS},
    )
    usage = extract_usage_from_result(result)
    if usage:
        record_vendor_usage_out_of_band(
            provider=None,
            model=AUDIO_EPISODE_MODEL,
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


def _build_script_prompt(episode: AudioEpisode) -> str:
    if episode.kind == FAST_NEWS_DIGEST_KIND:
        return _build_fast_news_prompt(episode.source_snapshot or {})
    if episode.kind == CONTENT_COUNCIL_DISCUSSION_KIND:
        return _build_content_council_prompt(episode.source_snapshot or {})
    raise ValueError(f"Unsupported audio episode kind: {episode.kind}")


def _build_fast_news_prompt(source_snapshot: dict[str, Any]) -> str:
    return f"""Create a roughly 5 minute quick-hit episode from these unread Fast Reads.

Goal:
- Curate the highest-signal highlights across the list, not a rote item-by-item readout.
- Use only summaries and key points below.
- Mention concrete companies, products, people, and numbers when present.
- Group related items into themes when that makes the briefing sharper.
- Keep it brisk, conversational, and useful for someone catching up while walking.

Shape:
- 560-700 spoken words.
- Hard cap: {DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 8-14 turns.
- Start with the top 2-3 headlines and why they matter.
- End with one short "what to watch next" close.

Unread Fast Reads JSON:
{json.dumps(source_snapshot, ensure_ascii=False, indent=2)}
"""


def _build_content_council_prompt(source_snapshot: dict[str, Any]) -> str:
    source_label = "transcript" if source_snapshot.get("content_type") == "podcast" else "article"
    return f"""Create a roughly 5 minute council-of-experts discussion about this
long-form {source_label}.

Goal:
- Use the full supplied {source_label} plus the summary.
- Give listeners the thesis, strongest evidence, implications, and any weak spots or open questions.
- Make it feel like a compact expert roundtable, not a narration of the article.
- Keep the discussion grounded: if a point is not in the source, do not include it.

Shape:
- 560-700 spoken words.
- Hard cap: {DIALOGUE_TEXT_CHAR_LIMIT} characters across all spoken turn text.
- 8-16 turns.
- Use speaker='host' for framing, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- End with a concise takeaway and why the piece is worth remembering.

Long-form source JSON:
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


def _fit_script_to_dialogue_limit(script: AudioEpisodeScript) -> AudioEpisodeScript:
    """Trim turn text to fit ElevenLabs dialogue character limits."""

    total_chars = sum(len(turn.text) for turn in script.turns)
    if total_chars <= DIALOGUE_TEXT_CHAR_LIMIT:
        return script

    remaining_chars = DIALOGUE_TEXT_CHAR_LIMIT
    fitted_turns: list[AudioEpisodeTurn] = []
    for index, turn in enumerate(script.turns):
        remaining_turns = len(script.turns) - index
        turn_budget = max(80, remaining_chars // remaining_turns)
        fitted_text = _truncate_dialogue_turn(turn.text, turn_budget)
        fitted_turns.append(turn.model_copy(update={"text": fitted_text}))
        remaining_chars -= len(fitted_text)

    return script.model_copy(update={"turns": fitted_turns})


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


def _truncate_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
