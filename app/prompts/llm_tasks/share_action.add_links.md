---
id: llm_tasks/share_action.add_links
description: VM guidance for extracting meaningful linked content from a shared page.
prompt_type: markdown
---
# Share Action: Add Links

Goal: inspect the submitted page and return a ranked, deduped list of meaningful linked pages.

Recommended sequence:
1. Inspect `input/request.json`.
2. Fetch the page with `execute_bash` and parse HTML with Python libraries such as BeautifulSoup,
   trafilatura, readability, or selectolax.
3. Remove navigation, login, social, tag, author, pagination, and tracking links.
4. Keep links that represent substantive articles, papers, podcast episodes, docs, or source pages.
5. If `input.save_to_knowledge_and_mark_read` is true, keep the same action shape; the host will
   save each accepted candidate to Knowledge and mark it read after submission.
6. Write `output/result.json`.

Do not create content rows directly. Include `save_to_knowledge_and_mark_read` from
`input/request.json` in `output/result.json`; the host will validate and apply the `add_links`
action after ranking.

Required `output/result.json`:

```json
{
  "action": "add_links",
  "primary_url": "https://example.com/source-page",
  "save_to_knowledge_and_mark_read": false,
  "content_urls": [
    {
      "url": "https://example.com/linked-story",
      "title": "Optional title",
      "rationale": "Why this link matters"
    }
  ],
  "rationale": "Summary of extraction criteria",
  "sources_used": [],
  "confidence": 0.8
}
```

Bad output: every link on the page, including nav, footer, login, and social sharing links.
