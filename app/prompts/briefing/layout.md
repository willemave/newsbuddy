---
description: Briefing tab layout composition prompts.
---

## System

You compose a native Newsly briefing layout for the `$tier` tier.

Return a flat JSON layout with blocks only. Allowed block types:
- `passage`: use `markdown` with source links and optional insight markers.
- `figure`: use `source_key`, `caption`, and `placement` (`full` or `inset`).
- `pullquote`: use `source_key` and short `text`.

Write grounded prose only from the provided sources. Source references must be markdown links like
`[Title](newsly://briefing/content/123)` or `[Title](newsly://briefing/news/456)`.
Never write bare source ids. Mark 2 or 3 useful deep-dive fragments with
`{{insight:short_id}}selected words{{/insight}}`. Keep insight ids short and unique inside this
window.

## Window

Lens: $lens_title
Tier: $tier

Sources:

$source_payload_json

Compose one readable briefing window. Cover every source at least once. Use a compact,
informational register. Prefer connective prose over lists.

For `audio` and `longform` tiers, treat each podcast or article as a full source, not a
headline. Use the provided `briefing_context` when present. Give each source its own
substantive treatment: target 3-5 sentences, roughly 110-170 words, covering the thesis,
the concrete supporting details, and why it matters to the reader. Do not reduce a
podcast episode or long article to a single sentence unless the provided source context is
truly minimal. You may compare sources after each source has been developed.

For `news` tiers, stay tighter: synthesize the cluster in a concise scan-friendly way while
still linking every source.

## Lens Naming

Name this cluster of unread Fast Reads. Return a short slug, title, and one-sentence deck.

$source_payload_json

## Masthead

Refresh the briefing masthead deck in two concise sentences.

Current deck:
$current_deck

New sources:
$source_titles
