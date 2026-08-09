---
id: llm_tasks/share_action.add_to_briefing
description: VM guidance for resolving a shared URL to a Briefing source or item.
prompt_type: markdown
---
# Share Action: Add to Briefing

Goal: resolve the submitted URL to exactly one Briefing outcome: subscribe to a continuing
source, or ingest one individual article or podcast episode.

Resolution order:
1. Inspect `input/request.json` and resolve redirects, share links, and tracking parameters.
2. Decide whether the target is an individual item or a continuing publication, show, channel,
   newsletter, or feed.
3. For a continuing source, discover and validate its best RSS, Atom, podcast RSS, newsletter,
   YouTube channel, or other supported feed URL. Prefer the canonical feed over a homepage.
4. For an individual item, return the canonical article or podcast-episode URL with useful type
   hints.
5. If feed discovery fails but the URL is clearly a valid individual item, return the content
   target. Never ingest an arbitrary homepage merely to avoid failure.
6. If neither a valid feed nor Briefing-eligible item can be resolved, return `no_action` with a
   clear rationale.
7. Write `output/result.json`.

Do not subscribe or ingest directly. The host validates the discriminated target and applies the
single `add_to_briefing` action through Newsly's existing subscription or content pipeline.

Feed result:

```json
{
  "action": "add_to_briefing",
  "briefing_target": {
    "kind": "feed",
    "url": "https://example.com/feed.xml",
    "title": "Example Publication",
    "rationale": "Validated canonical RSS feed for this source"
  },
  "sources_used": [],
  "confidence": 0.9
}
```

Individual-item result:

```json
{
  "action": "add_to_briefing",
  "briefing_target": {
    "kind": "content",
    "url": "https://example.com/story",
    "title": "Example Story",
    "content_type": "article",
    "rationale": "Canonical individual article"
  },
  "sources_used": [],
  "confidence": 0.9
}
```

Unsupported result:

```json
{
  "action": "no_action",
  "rationale": "The shared page is neither a valid source nor a Briefing-eligible item",
  "sources_used": [],
  "confidence": 0.8
}
```
