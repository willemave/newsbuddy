---
description: Briefing news-lens naming prompt.
---

## System

Name one semantic cluster of unread Fast Reads for Newsly.

Return structured JSON with:

- `key`: a stable URL-safe key starting with `news-`
- `title`: a specific reader-facing title under 40 characters
- `deck`: one sentence explaining what connects these sources

The title must describe the shared theme across most sources, not the most vivid single story.
If the sources are only loosely related, choose a broad but honest title that can cover the
whole cluster, such as "Public Infrastructure" or "Work & Institutions".
Do not use vague labels such as "Updates", "Briefs", "News", or "Misc" unless the sources are
genuinely mixed. Do not name a lens after one source unless nearly every source is about that
same topic.

## User

$source_payload_json
