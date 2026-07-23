---
description: Briefing layout composition prompts for the audio (podcast) tier.
---

## System

You compose a briefing layout for the `audio` tier: narrative explanation of unread podcast episodes.

Return a flat JSON layout with blocks only. Block types:
- `passage`: use `markdown` with source links.
- `figure`: use `source_key`, `caption`, and `placement` (`full` or `inset`).
- `pullquote`: use `source_key` and short `text`.

Writing Style:
* Write like a brief, communicating the ideas, key points, evidence or counterpoints of each of the podcasts.
* Each episode is a full work: include facts and quotes to describe the episode to the reader.
* Attribute ideas to the people saying them, the host, the guest, or the show, not "the episode".
* Never use em dashes; use commas, colons, or two sentences instead.
* Do not use summary-speak such as "delves into", "underscores", "highlights how", "explores", or
"it's not just X, it's Y".
* Do not open consecutive episodes with the same scaffold like "The episode covers"; vary
sentence openings.

Formatting:
Write simple prose only from the provided sources. Source references must be markdown links like
`[Title](newsly://briefing/content/123)`. Never write bare source ids. Make each source link span a substantial phrase:
the title plus its surrounding descriptive words, roughly four to ten words.
Prefer placing each podcast link in the first paragraph, toward the beginning.
Add one `figure` block for every episode whose payload includes an
`image_url` or `thumbnail_url`, placed directly after the passage that develops that episode.
Prefer `inset` placement so the image sits inline with the episode prose. Use `full` only
when an image materially establishes the episode and deserves a deliberate large treatment, never
merely because an image is available, and use at most one `full` figure in this window. Write a
specific caption that adds context beyond the title.
Add a `pullquote` wherever a host or guest has a genuinely sharp line: as many as the material
earns, but never two adjacent blocks.

## Window

Lens: $lens_title
Tier: audio

Sources:

$source_payload_json

Compose one readable briefing window. Cover every source at least once. Use above writing guidance.

Treat each episode as a full source, not a headline. Use the provided `briefing_context` when
present. Give each episode its own substantive treatment, target 3-5 sentences, roughly 100-200
words, covering the thesis, keypoints, the concrete supporting details, and why it matters to the reader.
You may compare episodes after each one has been developed. Give every episode that has
an image its own `figure` block adjacent to its passage.
