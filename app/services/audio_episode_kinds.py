"""Kind-specific audio episode generation policy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.core.model_defaults import ARTICLE_PODCAST_SUMMARY_MODEL_SPEC, CHEAP_GOOGLE_MODEL_SPEC
from app.services.custom_narrations import (
    CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT,
    CUSTOM_NARRATION_KIND,
    build_custom_narration_prompt,
)
from app.services.prompt_library import render_prompt

FAST_NEWS_DIGEST_KIND: Literal["fast_news_digest"] = "fast_news_digest"
CONTENT_COUNCIL_DISCUSSION_KIND: Literal["content_council_discussion"] = (
    "content_council_discussion"
)
NEWS_ITEM_DISCUSSION_KIND: Literal["news_item_discussion"] = "news_item_discussion"
AUDIO_EPISODE_MODEL = ARTICLE_PODCAST_SUMMARY_MODEL_SPEC
CUSTOM_NARRATION_MODEL = CHEAP_GOOGLE_MODEL_SPEC
DIALOGUE_TEXT_CHAR_LIMIT = 1_100


@dataclass(frozen=True)
class AudioEpisodeKindSpec:
    """Generation policy for one audio episode kind."""

    default_model: str
    dialogue_text_char_limit: int
    build_prompt: Callable[[dict[str, Any]], str]


def audio_episode_kind_spec(kind: str) -> AudioEpisodeKindSpec:
    try:
        return AUDIO_EPISODE_KIND_SPECS[kind]
    except KeyError:
        raise ValueError(f"Unsupported audio episode kind: {kind}") from None


def _build_fast_news_prompt(source_snapshot: dict[str, Any]) -> str:
    return render_prompt(
        "audio/episode_scripts#fast_news_digest_user",
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


def _build_content_council_prompt(source_snapshot: dict[str, Any]) -> str:
    source_label = "transcript" if source_snapshot.get("content_type") == "podcast" else "article"
    return render_prompt(
        "audio/episode_scripts#content_council_discussion_user",
        source_label=source_label,
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


def _build_news_item_discussion_prompt(source_snapshot: dict[str, Any]) -> str:
    return render_prompt(
        "audio/episode_scripts#news_item_discussion_user",
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        source_snapshot_json=json.dumps(source_snapshot, ensure_ascii=False, indent=2),
    )


AUDIO_EPISODE_KIND_SPECS: dict[str, AudioEpisodeKindSpec] = {
    FAST_NEWS_DIGEST_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        build_prompt=_build_fast_news_prompt,
    ),
    CONTENT_COUNCIL_DISCUSSION_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        build_prompt=_build_content_council_prompt,
    ),
    NEWS_ITEM_DISCUSSION_KIND: AudioEpisodeKindSpec(
        default_model=AUDIO_EPISODE_MODEL,
        dialogue_text_char_limit=DIALOGUE_TEXT_CHAR_LIMIT,
        build_prompt=_build_news_item_discussion_prompt,
    ),
    CUSTOM_NARRATION_KIND: AudioEpisodeKindSpec(
        default_model=CUSTOM_NARRATION_MODEL,
        dialogue_text_char_limit=CUSTOM_NARRATION_DIALOGUE_TEXT_CHAR_LIMIT,
        build_prompt=build_custom_narration_prompt,
    ),
}
