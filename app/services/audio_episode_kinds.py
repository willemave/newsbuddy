"""Kind-specific audio episode generation policy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.core.model_defaults import CHEAP_MODEL_SPEC
from app.services.audio_episode_errors import AudioEpisodeInputError
from app.services.custom_narrations import (
    CUSTOM_NARRATION_KIND,
    build_custom_narration_prompt,
)
from app.services.prompt_library import render_prompt

FAST_NEWS_DIGEST_KIND: Literal["fast_news_digest"] = "fast_news_digest"
CONTENT_COUNCIL_DISCUSSION_KIND: Literal["content_council_discussion"] = (
    "content_council_discussion"
)
NEWS_ITEM_DISCUSSION_KIND: Literal["news_item_discussion"] = "news_item_discussion"
BRIEFING_NARRATION_KIND: Literal["briefing_narration"] = "briefing_narration"
AUDIO_EPISODE_MODEL = CHEAP_MODEL_SPEC
CUSTOM_NARRATION_MODEL = AUDIO_EPISODE_MODEL
AudioEpisodeScriptMode = Literal["generated_dialogue", "preauthored"]


@dataclass(frozen=True)
class AudioEpisodeKindSpec:
    """Generation policy for one audio episode kind."""

    default_model: str
    build_prompt: Callable[[dict[str, Any]], str] | None = None
    script_mode: AudioEpisodeScriptMode = "generated_dialogue"
    marks_sources_read_on_play: bool = False
    marks_sources_read_on_finish: bool = False


def audio_episode_kind_spec(kind: str) -> AudioEpisodeKindSpec:
    try:
        return AUDIO_EPISODE_KIND_SPECS[kind]
    except KeyError:
        raise AudioEpisodeInputError(f"Unsupported audio episode kind: {kind}") from None


def _build_fast_news_prompt(source_snapshot: dict[str, Any]) -> str:
    return render_prompt(
        "audio/episode_scripts#fast_news_digest_user",
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


def _build_content_council_prompt(source_snapshot: dict[str, Any]) -> str:
    source_label = "transcript" if source_snapshot.get("content_type") == "podcast" else "article"
    return render_prompt(
        "audio/episode_scripts#content_council_discussion_user",
        source_label=source_label,
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


def _build_news_item_discussion_prompt(source_snapshot: dict[str, Any]) -> str:
    return render_prompt(
        "audio/episode_scripts#news_item_discussion_user",
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


AUDIO_EPISODE_KIND_SPECS: dict[str, AudioEpisodeKindSpec] = {
    FAST_NEWS_DIGEST_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        build_prompt=_build_fast_news_prompt,
    ),
    CONTENT_COUNCIL_DISCUSSION_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        build_prompt=_build_content_council_prompt,
    ),
    NEWS_ITEM_DISCUSSION_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        build_prompt=_build_news_item_discussion_prompt,
    ),
    CUSTOM_NARRATION_KIND: AudioEpisodeKindSpec(
        default_model=CUSTOM_NARRATION_MODEL,
        build_prompt=build_custom_narration_prompt,
        marks_sources_read_on_play=True,
    ),
    BRIEFING_NARRATION_KIND: AudioEpisodeKindSpec(
        default_model="deterministic",
        script_mode="preauthored",
        marks_sources_read_on_finish=True,
    ),
}
