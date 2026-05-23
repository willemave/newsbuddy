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
- Use the supplied {source_label} excerpts plus the summary.
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
