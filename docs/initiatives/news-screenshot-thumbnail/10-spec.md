# News Screenshot Thumbnails (Playwright)

## Summary
Replace LLM-generated news thumbnails with Playwright screenshots of the normalized article URL. Add a new queue task after summarization to capture the screenshot, generate the existing 200px thumbnail, and fall back to a generic placeholder on failure. This applies only to `content_type=news`.

## Goals
- Use Playwright screenshots for news thumbnails (no Gemini image generation for news).
- Run as a new post-summary task on the existing processing queue.
- Reuse current thumbnail sizing logic and storage paths.
- Provide a generic thumbnail when screenshot capture fails.
- Provide a backfill script for existing news items.

## Non-Goals
- Changing API schemas or client behavior.
- Modifying non-news image generation workflows.
- Full-page screenshots or complex layout detection.

## Current Behavior (2025-12-29)
- `SUMMARIZE` enqueues `GENERATE_IMAGE` for all content.
- `ImageGenerationService` creates 1:1 news thumbnails via Gemini and writes to:
  - `{IMAGES_BASE_DIR}/news_thumbnails/{content_id}.png` (served at `/static/images/news_thumbnails/...`)
  - `{IMAGES_BASE_DIR}/thumbnails/{content_id}.png` (200px, served at `/static/images/thumbnails/...`)
- API `thumbnail_url` and `image_url` are derived from file existence, not metadata.

## Proposed Changes
### 1) New Task Type: `GENERATE_THUMBNAIL`
- Add `TaskType.GENERATE_THUMBNAIL = "generate_thumbnail"`.
- Add `GenerateThumbnailHandler` to `app/pipeline/handlers/` and dispatch via `TaskDispatcher`.
- Summarization stage:
  - If `content_type == news`, enqueue `GENERATE_THUMBNAIL`.
  - Otherwise enqueue `GENERATE_IMAGE` (unchanged).
- Safety guard: `GENERATE_IMAGE` handler should skip `content_type=news` to avoid any leftover/legacy tasks from generating AI images.

### 2) Screenshot Capture (Playwright)
Add a new service module, function-oriented (RORO), e.g. `app/services/news_thumbnail_screenshot.py`:

- **Input**: `NewsThumbnailRequest` (content_id, url, viewport, timeout)
- **Output**: `NewsThumbnailResult` (success, image_path, thumbnail_path, error_message)

**URL selection (normalized):**
Use the most canonical article URL in order:
1. `content.content_metadata["article"]["url"]`
2. `content.content_metadata["summary"]["final_url_after_redirects"]`
3. `content.url`

**Playwright behavior:**
- Use sync Playwright (`sync_playwright`) to match existing worker model.
- `browser = pw.chromium.launch(headless=True)`
- `context = browser.new_context(user_agent=NEWS_SCREENSHOT_USER_AGENT)`
- `page.set_viewport_size({"width": 1024, "height": 1024})` (square viewport)
- `page.goto(url, wait_until="domcontentloaded", timeout=NEWS_SCREENSHOT_TIMEOUT_MS)`
- `page.wait_for_load_state("networkidle", timeout=NEWS_SCREENSHOT_NETWORK_IDLE_MS)` (best-effort)
- Optional short `page.wait_for_timeout(1000)` to stabilize layout.
- `page.screenshot(path=NEWS_THUMBNAILS_DIR / f"{content_id}.png", full_page=False, type="png")`

**Output files:**
- Full screenshot: `{IMAGES_BASE_DIR}/news_thumbnails/{content_id}.png`
- 200px thumbnail: reuse `ImageGenerationService.generate_thumbnail` to create `{IMAGES_BASE_DIR}/thumbnails/{content_id}.png`

### 3) Generic Thumbnail Fallback
If screenshot fails or URL is missing:
- Copy a bundled placeholder image into `{IMAGES_BASE_DIR}/news_thumbnails/{content_id}.png`.
- Run `generate_thumbnail` to create the 200px thumbnail.
- Mark the task as **success** (avoid retries) but log the error with structured `extra` fields.

**Placeholder asset:**
- Use `static/images/placeholders/news_thumbnail.png` (square PNG, neutral visual).
- If missing, generate a neutral placeholder on demand (Pillow) and reuse it.

### 4) Metadata + Logging
- Continue setting `content_metadata["image_generated_at"] = now` on success (screenshot or placeholder) for parity with existing workflows.
- Log errors via `logger.error/exception` with `component="thumbnail_generation"`, `operation="screenshot"`, and `item_id`.

## Backfill Script
Create a backfill script to enqueue screenshot generation for existing news items, e.g.:
- `scripts/backfill_news_screenshots.py` (or update `scripts/backfill_thumbnails.py` to use the new task type).

Behavior:
- Query completed `content_type=news` items.
- Skip items that already have `{IMAGES_BASE_DIR}/news_thumbnails/{id}.png` (unless `--include-existing`).
- Enqueue `TaskType.GENERATE_THUMBNAIL`.
- Support `--days-back`, `--limit`, `--dry-run`.

## Tests
- **Unit**: URL selection logic chooses normalized URL in the correct priority order.
- **Unit**: Screenshot service handles Playwright errors and returns placeholder paths.
- **Integration**: Summarization enqueues `GENERATE_THUMBNAIL` for news, `GENERATE_IMAGE` for others.
- **Integration**: `GENERATE_IMAGE` handler skips news safely.

## Rollout Notes
- Ensure Playwright Chromium is installed in worker environments (already in `scripts/start_workers.sh`).
- No API contract changes; clients continue using `image_url` + `thumbnail_url` derived from filesystem.
