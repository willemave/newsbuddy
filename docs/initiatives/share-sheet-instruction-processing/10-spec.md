# Share Sheet LLM Instruction (gpt-5.2 + Web Search)

## Summary
Add an optional instruction to `/api/content/submit` (repurposed from the share-sheet note field). When present, run a transient gpt-5.2 + web search call during `ANALYZE_URL` to generate context (text + links) that guides URL analysis. Only create additional content records from discovered links when `crawl_links=true`. The instruction text and raw results are not persisted or returned in API responses.

## Goals
- Accept an optional instruction on content submission (API + share extension).
- Add an explicit crawl toggle so link creation is opt-in.
- Use gpt-5.2 with web search to interpret the instruction and provide context about the submitted URL.
- Support flexible outputs with text and 0+ links (plus metadata sufficient to create content records).
- Create new content records from discovered links and enqueue them for normal processing when crawl is enabled.
- Do not persist or return the instruction text or raw LLM output; use them only during analysis.

## Non-Goals
- Persisting instruction text or raw LLM output on the content record.
- Returning instruction/results in API responses.
- Changing summarization behavior or deep research flows.

## Current Behavior (2025-12-30)
- Share extension sends `{ url, note }` to `POST /api/content/submit`.
- `SubmitContentRequest` ignores `note` (extra fields are dropped).
- `/submit` enqueues `ANALYZE_URL` with no additional context.
- `ANALYZE_URL` uses pattern matching or `ContentAnalyzer` (pydantic-ai; no web search).

## Proposed Changes

### 1) API: Accept Optional Instruction
- Add `instruction: str | None` to `SubmitContentRequest`.
- Use `validation_alias=AliasChoices("instruction", "note")` so existing clients sending `note` still work.
- No response changes.

**Client UX**
- Share sheet includes a "Fetch / Crawl" selector:
  - **Fetch** (default): process only the submitted URL.
  - **Crawl**: allow creating additional content from links on the page.
- Placeholder can reflect the current mode, e.g.:
  - "Add a note (optional)" vs "Add crawl instructions (optional)"
- Send `instruction` field instead of `note` (keep backwards compatibility in API).

### 2) API: Accept Optional `crawl_links`
- Add `crawl_links: bool = False` to `SubmitContentRequest`.
- Include `crawl_links` in the `ANALYZE_URL` task payload when enabled.
- No response changes.

### 3) Task Payload: Transient Instruction
- When enqueuing `ANALYZE_URL`, include the instruction in the task payload:
  - `payload = {"content_id": id, "instruction": "..."}`
- Do not store the instruction in `content_metadata` or any other content table.
- After `ANALYZE_URL` completes, scrub instruction data from the task payload to avoid retention:
  - Update `ProcessingTask.payload` to remove the `instruction` field before calling `complete_task`.
- Ensure the instruction text is included in the LLM prompt used during `ANALYZE_URL` (see section 4).

### 4) Update ContentAnalyzer to gpt-5.2 + Web Search
Extend `ContentAnalyzer` to use gpt-5.2 with web search for URL analysis and instruction handling. The old `gpt-4o-mini` path is deprecated.

**Models**
```python
class InstructionLink(BaseModel):
    url: str
    title: str | None = None
    context: str | None = None
    content_type: Literal["article", "podcast", "video", "news", "unknown"] | None = None
    platform: str | None = None
    source: str | None = None

class InstructionResult(BaseModel):
    text: str | None = None
    links: list[InstructionLink] = Field(default_factory=list)
```

**Service API**
- Add a new method on `ContentAnalyzer`, e.g. `analyze_url_with_instruction(...)`, that:
  - Accepts `url`, optional `instruction`, optional `title`, optional `analysis_context`.
  - Returns `ContentAnalysisResult` plus an optional `InstructionResult`.
- Use OpenAI Responses API with:
  - model: `gpt-5.2`
  - tools: `[{"type": "web_search_preview"}]`
  - output: JSON matching a combined schema.

**Prompt shape (high-level)**
- System: describe that the assistant is helping analyze a submitted URL and should follow the user instruction.
- User: include the URL + instruction, ask for concise text context and any relevant links (with titles + metadata hints).

**Failure behavior**
- If the LLM call fails or response is invalid, log and fall back to the existing pattern-based detection.
- Never fail the `ANALYZE_URL` task solely due to instruction handling errors.

### 5) Use Instruction Output During `ANALYZE_URL`
- In `AnalyzeUrlHandler` (`app/pipeline/handlers/analyze_url.py`):
  - If `instruction` is present or `crawl_links=true`, call the new `ContentAnalyzer` method and capture both outputs.
  - Build a temporary `analysis_context` string from `InstructionResult`:
    - `text` content + a compact list of link URLs (and optional titles).
  - Ensure the `instruction` text is passed into the LLM prompt for URL analysis.
- `ContentAnalyzer` includes instruction context in its prompt but does not persist it.

### 6) Create Content Records From Instruction Links
- For each `InstructionLink` returned (only when `crawl_links=true`):
  - Skip if `url` matches the original submission URL or is invalid.
  - Deduplicate against existing `Content` records by `url` (any type); if exists, ensure inbox status for the submitting user.
  - Create a new `Content` row with:
    - `url` = link URL
    - `content_type` = `unknown` (or use `content_type` hint if provided)
    - `title` = link title (optional)
    - `platform` = hint (optional)
    - `source` = hint (optional)
    - `status` = `new`
    - `content_metadata` minimal self-submission fields (`submitted_by_user_id`, `submitted_via`)
  - Enqueue `ANALYZE_URL` for each newly created content item.
- Ensure this does not fail the primary submission if link creation fails (log and continue).

### 7) Flexible Architecture Notes
- The `InstructionResult` schema allows:
  - `text` only
  - `links` only
  - mixed text + links
- Future expansion can add new block types without changing existing behavior if needed.

## Logging + Safety
- Do not log full instruction text or LLM outputs.
- Use structured error logging with `component="share_instruction"` and `operation="run"`.

## Tests
- **Unit**: `SubmitContentRequest` accepts `instruction` and `note` aliases.
- **Unit**: `ContentAnalyzer` parses combined JSON (analysis + instruction) and tolerates missing instruction fields.
- **Integration**: `ANALYZE_URL` uses instruction context when present and still succeeds when instruction run fails.
- **Integration**: Instruction links produce new content records + `ANALYZE_URL` tasks (dedupe verified).

## Rollout Notes
- No schema migrations.
- Ensure OpenAI Responses API client is available to workers (same as deep research).
- Keep the instruction flow guarded behind presence of `instruction` only.
