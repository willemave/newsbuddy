---
id: llm_tasks/share_action.add_content
description: VM guidance for canonicalizing one shared URL into a content submission intent.
prompt_type: markdown
---
# Share Action: Add Content

Goal: resolve the submitted URL into one canonical content URL with useful title, platform, and
type hints.

Recommended sequence:
1. Inspect `input/request.json`.
2. Use `execute_bash` with `curl`, Python, and HTML parsing when the page is accessible.
3. Use `web_search` only when the submitted URL is ambiguous, blocked, or lacks metadata.
4. Prefer canonical article, podcast episode, or video URLs over tracking/share URLs.
5. Write `output/result.json`.

Do not subscribe to feeds, save to Knowledge, mark read, or enqueue chat directly. The host will
validate `output/result.json` and apply the `add_content` action if it matches this workflow.

Required `output/result.json`:

```json
{
  "action": "add_content",
  "primary_url": "https://example.com/canonical",
  "title": "Optional title",
  "platform": "optional_platform",
  "content_type": "article",
  "rationale": "Why this URL is the right target",
  "sources_used": [],
  "confidence": 0.9
}
```

Bad output: a social redirect URL when the page exposes a canonical URL.
