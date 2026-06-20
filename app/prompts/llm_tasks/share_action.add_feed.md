---
id: llm_tasks/share_action.add_feed
description: VM guidance for discovering the best feed URL for a shared source.
prompt_type: markdown
---
# Share Action: Add Feed

Goal: find the best RSS, Atom, podcast RSS, newsletter, YouTube channel feed, or source feed for
the submitted URL.

Recommended sequence:
1. Inspect `input/request.json`.
2. Try the submitted URL directly as RSS/Atom.
3. Fetch HTML and inspect `<link rel="alternate">`, common feed paths, podcast metadata, and
   platform-specific hints.
4. For YouTube, prefer the channel feed when a channel identity is available.
5. Validate the chosen feed with `curl` and Python `feedparser` when possible.
6. Write `output/result.json`.

Do not subscribe directly. The host will validate `output/result.json` and apply the
`subscribe_to_feed` action if it matches this workflow.

Required `output/result.json`:

```json
{
  "action": "add_feed",
  "primary_url": "https://example.com",
  "feed_url": "https://example.com/feed.xml",
  "title": "Optional feed title",
  "rationale": "Why this feed is best",
  "sources_used": [],
  "confidence": 0.9
}
```

Bad output: a homepage URL when a valid RSS/Atom feed URL was discoverable.
