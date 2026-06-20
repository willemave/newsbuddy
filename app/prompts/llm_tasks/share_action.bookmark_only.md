---
id: llm_tasks/share_action.bookmark_only
description: VM guidance for canonicalizing a shared URL for Knowledge save and read-state update.
prompt_type: markdown
---
# Share Action: Bookmark Only

Goal: canonicalize the submitted URL and produce a save-to-Knowledge intent.

Recommended sequence:
1. Inspect `input/request.json`.
2. Resolve tracking/share URLs to the canonical content URL.
3. Fetch metadata if useful for title/platform/type hints.
4. Write `output/result.json`.

Do not save or mark read directly. The host will validate `output/result.json` and apply the
`save_to_knowledge` action if it matches this workflow.

Required `output/result.json`:

```json
{
  "action": "bookmark_only",
  "primary_url": "https://example.com/canonical",
  "title": "Optional title",
  "platform": "optional_platform",
  "content_type": "article",
  "rationale": "Why this canonical target should be saved",
  "sources_used": [],
  "confidence": 0.9
}
```

Bad output: the original tracking URL when a canonical URL is available.
