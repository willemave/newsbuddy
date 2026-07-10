"""Structured script preparation for generated and preauthored audio episodes."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import AudioEpisode
from app.services.audio_episode_errors import AudioEpisodeInputError
from app.services.audio_episode_kinds import audio_episode_kind_spec
from app.services.audio_episodes.shared import (
    PROMPT_VERSION,
    duration_ms,
    required_int,
    required_str,
)
from app.services.llm_agents import get_basic_agent
from app.services.prompt_library import load_prompt
from app.services.vendor_costs import extract_usage_from_result, record_vendor_usage_out_of_band

logger = get_logger(__name__)

SCRIPT_TIMEOUT_SECONDS = 180
SCRIPT_SYSTEM_PROMPT = load_prompt("audio/episode_scripts#system")


class AudioEpisodeTurn(BaseModel):
    """One complete spoken thought in an audio script."""

    speaker: Literal["host", "cohost", "expert"] = Field(
        ...,
        description="Speaker role for this turn.",
    )
    text: str = Field(..., min_length=1)


class AudioEpisodeScript(BaseModel):
    """Persisted script shape shared by generated and preauthored episodes."""

    title: str = Field(..., min_length=1)
    estimated_duration_seconds: int = Field(..., ge=1)
    turns: list[AudioEpisodeTurn] = Field(..., min_length=1)


@dataclass(frozen=True)
class AudioEpisodeScriptGeneration:
    """Structured script plus the model that produced it."""

    script: AudioEpisodeScript
    model: str


def prepare_audio_episode_script(db: Session, episode: AudioEpisode) -> AudioEpisodeScript:
    """Generate, rebuild, or reuse the structured script for an episode."""

    kind_spec = audio_episode_kind_spec(str(episode.kind or ""))
    if kind_spec.script_mode == "preauthored":
        return persist_audio_episode_script(
            db,
            episode,
            _preauthored_script(episode),
            model=kind_spec.default_model,
        )

    script = script_from_episode(episode)
    model_spec = str(episode.model or kind_spec.default_model)
    if script is None:
        generated = generate_script(episode)
        script = generated.script
        model_spec = generated.model
    return persist_audio_episode_script(db, episode, script, model=model_spec)


def persist_audio_episode_script(
    db: Session,
    episode: AudioEpisode,
    script: AudioEpisodeScript,
    *,
    model: str,
) -> AudioEpisodeScript:
    """Persist a logical script without truncating its turns."""

    kind_spec = audio_episode_kind_spec(str(episode.kind or ""))
    script_text = (
        _preauthored_script_text(episode)
        if kind_spec.script_mode == "preauthored"
        else render_script_text(script)
    )
    fallback_title = required_str(episode.title, "audio episode title")
    episode.title = (script.title.strip() or fallback_title)[:255]
    episode.script = script.model_dump(mode="json")
    episode.script_text = script_text
    episode.model = model
    episode.duration_seconds = estimate_duration_seconds(script_text)
    db.flush()
    return script


def script_from_episode(episode: AudioEpisode) -> AudioEpisodeScript | None:
    payload = episode.script
    if not isinstance(payload, dict):
        return None
    try:
        return AudioEpisodeScript.model_validate(payload)
    except ValidationError:
        return None


def generate_script(episode: AudioEpisode) -> AudioEpisodeScriptGeneration:
    """Generate dialogue for a generated-dialogue episode kind."""

    kind_spec = audio_episode_kind_spec(str(episode.kind or ""))
    if kind_spec.script_mode != "generated_dialogue":
        raise RuntimeError(f"Audio episode kind {episode.kind!r} uses a preauthored script")

    user_message = build_script_prompt(episode)
    model_spec = kind_spec.default_model
    started_at = time.perf_counter()
    try:
        script = generate_script_with_model(episode, user_message, model_spec)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Audio episode script generation failed",
            extra={
                "component": "audio_episodes",
                "operation": "generate_script",
                "status": "failed",
                "duration_ms": duration_ms(started_at),
                "item_id": episode.id,
                "user_id": episode.user_id,
                "context_data": {
                    "kind": episode.kind,
                    "model": model_spec,
                    "error": str(exc),
                },
            },
        )
        raise

    logger.info(
        "Audio episode script generation completed",
        extra={
            "component": "audio_episodes",
            "operation": "generate_script",
            "status": "completed",
            "duration_ms": duration_ms(started_at),
            "item_id": episode.id,
            "user_id": episode.user_id,
            "context_data": {
                "kind": episode.kind,
                "model": model_spec,
                "turn_count": len(script.turns),
                "text_chars": sum(len(turn.text) for turn in script.turns),
            },
        },
    )
    return AudioEpisodeScriptGeneration(script=script, model=model_spec)


def generate_script_with_model(
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
            user_id=required_int(episode.user_id, "audio episode user_id"),
            content_id=episode.source_content_id,
            metadata={
                "audio_episode_id": episode.id,
                "kind": episode.kind,
                "prompt_version": PROMPT_VERSION,
            },
        )
    return AudioEpisodeScript.model_validate(result.output)


def build_script_prompt(episode: AudioEpisode) -> str:
    spec = audio_episode_kind_spec(str(episode.kind or ""))
    if spec.script_mode != "generated_dialogue" or spec.build_prompt is None:
        raise RuntimeError(f"Audio episode kind {episode.kind!r} has no generation prompt")
    return spec.build_prompt(episode.source_snapshot or {})


def render_script_text(script: AudioEpisodeScript) -> str:
    lines = [script.title.strip()]
    for turn in script.turns:
        label = {
            "host": "Host",
            "cohost": "Cohost",
            "expert": "Expert",
        }.get(turn.speaker, "Speaker")
        lines.append(f"{label}: {turn.text.strip()}")
    return "\n\n".join(line for line in lines if line.strip())


def estimate_duration_seconds(script_text: str) -> int:
    word_count = len(script_text.split())
    if word_count <= 0:
        return 0
    return int(math.ceil((word_count / 145) * 60))


def _preauthored_script(episode: AudioEpisode) -> AudioEpisodeScript:
    text = _preauthored_script_text(episode)
    return AudioEpisodeScript(
        title=required_str(episode.title, "audio episode title"),
        estimated_duration_seconds=max(1, estimate_duration_seconds(text)),
        turns=[AudioEpisodeTurn(speaker="host", text=text)],
    )


def _preauthored_script_text(episode: AudioEpisode) -> str:
    snapshot = episode.source_snapshot
    snapshot_text = snapshot.get("script_text") if isinstance(snapshot, dict) else None
    raw_text = snapshot_text if snapshot_text is not None else episode.script_text
    if raw_text is None or not str(raw_text).strip():
        raise AudioEpisodeInputError("Preauthored audio episode narration is empty")
    return str(raw_text)


__all__ = [
    "AudioEpisodeScript",
    "AudioEpisodeScriptGeneration",
    "AudioEpisodeTurn",
    "build_script_prompt",
    "estimate_duration_seconds",
    "generate_script",
    "generate_script_with_model",
    "persist_audio_episode_script",
    "prepare_audio_episode_script",
    "render_script_text",
    "script_from_episode",
]
