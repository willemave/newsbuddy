---
id: llm_tasks/share_action.chat
description: VM guidance for resolving a shared URL into a chat handoff intent.
prompt_type: markdown
---
# Share Action: Chat

Goal: resolve the content target for chat and preserve the user's initial question.

Recommended sequence:
1. Inspect `input/request.json`.
2. Canonicalize the shared URL if needed.
3. Preserve `chat_initial_message` exactly enough for the host chat workflow.
4. Write `output/result.json`.

Do not answer the chat question in the VM. The host will submit/process content, save it to
Knowledge, mark it read, and enqueue the existing dig-deeper chat flow.

The host will validate `output/result.json` and enqueue the chat action if it matches this
workflow.

Required `output/result.json`:

```json
{
  "action": "chat",
  "primary_url": "https://example.com/canonical",
  "chat": {
    "content_url": "https://example.com/canonical",
    "initial_message": "User question"
  },
  "title": "Optional title",
  "rationale": "Why this is the chat target",
  "sources_used": [],
  "confidence": 0.9
}
```

Bad output: a direct answer to the user's question instead of a handoff intent.
