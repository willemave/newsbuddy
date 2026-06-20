---
id: llm_tasks/share_action.presentation
description: VM guidance for resolving a shared URL into a presentation generation intent.
prompt_type: markdown
---
# Share Action: Presentation

Goal: resolve the source that should be used for presentation generation.

Recommended sequence:
1. Inspect `input/request.json`.
2. Canonicalize the source URL and gather a title/source type when useful.
3. Do not build the final deck in this phase unless the host explicitly asks for direct artifacts.
4. Preserve `interests_prompt` for the Learning Deck workflow.
5. Write `output/result.json`.

Do not create a Learning Deck directly. The host will validate `output/result.json` and create or
rerun the Learning Deck if it matches this workflow.

Required `output/result.json`:

```json
{
  "action": "presentation",
  "primary_url": "https://example.com/canonical",
  "presentation": {
    "source_url": "https://example.com/canonical",
    "title": "Optional title",
    "interests_prompt": "Optional interests",
    "artifact_mode": "learning_deck_handoff"
  },
  "rationale": "Why this source should become the presentation",
  "sources_used": [],
  "confidence": 0.9
}
```

Bad output: public deck files that bypass the host artifact validator.
