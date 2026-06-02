---
id: audio/episode_scripts
description: Sectioned prompts for generating structured audio episode scripts from content snapshots.
used_by:
  system: app/services/audio_episodes.py:_generate_script_with_model
  system_description: "System prompt for turning an audio episode source prompt into structured spoken dialogue."
  fast_news_digest_user: app/services/audio_episode_kinds.py:_build_fast_news_prompt
  fast_news_digest_user_description: "User prompt template for creating a one-minute quick-hit audio episode from unread Fast Reads."
  content_council_discussion_user: app/services/audio_episode_kinds.py:_build_content_council_prompt
  content_council_discussion_user_description: "User prompt template for a compact council-of-experts audio discussion about one long-form source."
  news_item_discussion_user: app/services/audio_episode_kinds.py:_build_news_item_discussion_prompt
  news_item_discussion_user_description: "User prompt template for a one-minute podcast-style discussion of one Fast Read."
  custom_narration_user: app/services/custom_narrations.py:build_custom_narration_prompt
  custom_narration_user_description: "User prompt template for synthesizing selected articles, podcast transcripts, and Fast Reads into a longer custom narration."
prompt_type: sectioned_prompt
---
## System
<!-- prompt-section: system -->
You write concise, natural podcast scripts for Newsly.
Create spoken dialogue, not an essay. The format should feel like a smart tech/business
podcast roundtable: quick context, clear stakes, grounded analysis, and a brisk close.
Do not mention or imitate any specific real podcast, host, or brand. Do not invent facts
outside the supplied source material. No stage directions, music cues, sponsor reads, or
markdown.
<!-- /prompt-section -->

## Fast News Digest User
<!-- prompt-section: fast_news_digest_user -->
Create a roughly 60 second quick-hit episode from these unread Fast Reads.

Goal:
- Curate the highest-signal highlights across the list, not a rote item-by-item readout.
- Use only summaries and key points below.
- Mention concrete companies, products, people, and numbers when present.
- Group related items into themes when that makes the briefing sharper.
- Keep it brisk, conversational, and useful for someone catching up while walking.

Shape:
- 110-150 spoken words.
- Hard cap: $dialogue_text_char_limit characters across all spoken turn text.
- 6-8 turns.
- Start with the top 2-3 headlines and why they matter.
- End with one short "what to watch next" close.

Unread Fast Reads JSON:
$source_snapshot_json
<!-- /prompt-section -->

## Content Council Discussion User
<!-- prompt-section: content_council_discussion_user -->
Create a roughly 60 second council-of-experts discussion about this
long-form $source_label.

Goal:
- Use the supplied $source_label excerpts plus the summary.
- Give listeners the thesis, strongest evidence, implications, and any weak spots or open questions.
- Make it feel like a compact expert roundtable, not a narration of the article.
- Keep the discussion grounded: if a point is not in the source, do not include it.

Shape:
- 110-150 spoken words.
- Hard cap: $dialogue_text_char_limit characters across all spoken turn text.
- 6-8 turns.
- Use speaker='host' for framing, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- End with a concise takeaway and why the piece is worth remembering.

Long-form source JSON:
$source_snapshot_json
<!-- /prompt-section -->

## News Item Discussion User
<!-- prompt-section: news_item_discussion_user -->
Create a roughly 60 second podcast-style discussion about this single Fast Read.

Goal:
- Use only the supplied summary, key points, and links metadata.
- Give listeners the headline, context, stakes, and what to watch next.
- Make it a compact expert roundtable, not a read-aloud summary.
- Do not invent extra facts beyond the source material.

Shape:
- 110-150 spoken words.
- Hard cap: $dialogue_text_char_limit characters across all spoken turn text.
- 6-8 turns.
- Use speaker='host' for framing, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- End with a concise takeaway.

Fast Read source JSON:
$source_snapshot_json
<!-- /prompt-section -->

## Custom Narration User
<!-- prompt-section: custom_narration_user -->
Create one cohesive podcast-style narration from the selected articles,
podcast transcripts, and Fast Reads.

Goal:
- Synthesize across all selected sources as one episode, not separate mini-summaries.
- Use the supplied source excerpts, Fast Read summaries, and key points. Each source is budgeted to preserve coverage.
- Explain the shared themes, contradictions, evidence, and implications.
- Preserve important source-specific details when they materially support the synthesis.
- Keep the discussion grounded: if a point is not in the selected sources, do not include it.

Shape:
- 500-700 spoken words.
- Hard cap: $dialogue_text_char_limit characters across all spoken turn text.
- 10-14 turns.
- Use speaker='host' for setup and transitions, speaker='cohost' for synthesis, and
  speaker='expert' for sharper analysis.
- Start by framing why these sources belong together.
- End with a concise takeaway and what the listener should remember.

Selected source JSON:
$source_snapshot_json
<!-- /prompt-section -->
