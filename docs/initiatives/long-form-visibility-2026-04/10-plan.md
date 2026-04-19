# Long-Form Visibility And Completion Plan

**Opened:** 2026-04-18  
**Closed:** 2026-04-18  
**Status:** Complete  
**Scope:** backend processing state machine, long-form list visibility, long-form unread stats, iOS long-form polling  
**Primary goal:** make long-form reads fast by deriving visibility only from inbox membership plus `contents.status`, with `completed` meaning artwork-ready for long-form content

---

## Contract

For long-form content (`article`, `podcast`):

- visible in the long-form inbox iff a `content_status` row exists with `status='inbox'`
- visible only when `contents.status='completed'`
- `completed` means the content is fully ready for the inbox, including artwork

This means long-form read paths must not inspect JSON metadata for visibility or readiness.

---

## Why this change

Current production behavior drifted away from the intended contract:

- long-form list reads still gate visibility on JSON metadata such as `digest_visibility`
- long-form list rendering still drops rows when `image_generated_at` is missing
- long-form stats still scan JSON metadata for `image_generated_at`
- long-form UI polls several orthogonal endpoints together
- long-form processing marks content `completed` before image generation finishes

Production evidence for user `1`:

- `/api/news/items` query shape executes in about `8.7ms` DB time
- the current long-form `/api/content/` query shape executes in about `878ms` DB time
- the current long-form stats query shape executes in about `818-903ms` DB time
- the dominant cost is JSON metadata predicates on `contents`, not the basic inbox join

---

## Plan

### 1. Add a status state machine for long-form readiness

- [x] Introduce an explicit non-terminal long-form status between summarized and visible, so `completed` is no longer overloaded.
- [x] Centralize allowed transitions in one state-machine module instead of scattering `if content_type == ...` checks across handlers.
- [x] Make long-form summarization transition into the new artwork-pending status.
- [x] Make image generation transition long-form content from artwork-pending to `completed`.
- [x] Add a defensive invariant: long-form content cannot transition directly to `completed` unless the state machine allows it.

### 2. Make long-form list visibility status-only

- [x] Remove JSON metadata visibility gates from the long-form read path.
- [x] Remove generated-artwork gating from long-form list rendering helpers.
- [x] Keep image URL resolution as presentation-only logic, not visibility logic.

### 3. Split long-form list reads onto a fast query path

- [x] Add a dedicated repository query for long-form inbox pages using only `content_status`, `contents`, and keyset pagination.
- [x] Apply unread filtering with `EXISTS` / `NOT EXISTS` against `content_read_status`.
- [x] Hydrate read/save flags only for page ids, not across the full candidate set.
- [x] Leave the shared content query path in place for mixed/all-content surfaces.

### 4. Reduce long-form stats to unread-oriented semantics

- [x] Remove JSON metadata predicates from long-form stats.
- [x] Stop computing processing count as part of the long-form stats call.
- [x] Return only the long-form counts the current UI actually needs for unread state.

### 5. Trim long-form client polling fanout

- [x] Stop polling scraper configs every 5 seconds on the long-form tab.
- [x] Do not block feed refresh on unrelated stats/config calls.
- [x] Keep long-form feed refresh independent from orthogonal counts.

### 6. Verify with tests and timings

- [x] Add backend tests for the new long-form state machine and visibility contract.
- [x] Add regression tests proving long-form list visibility no longer depends on JSON metadata keys.
- [x] Run targeted `pytest` and `ruff check` on touched files.
- [x] Re-check local and production timings after the query changes.

### Verification notes

- Local dev DB is only a smoke-check dataset right now: `42` contents total and `1` inbox row for user `1`.
- Local `EXPLAIN (ANALYZE, BUFFERS)` on the new long-form page SQL shape ran in about `0.11ms`.
- Local `EXPLAIN (ANALYZE, BUFFERS)` on the new unread-count SQL shape ran in about `0.07ms`.
- Production live-data `EXPLAIN (ANALYZE, BUFFERS)` on the new long-form page SQL shape for user `1` ran in about `42.4ms`.
- Production live-data `EXPLAIN (ANALYZE, BUFFERS)` on the new unread-count SQL shape for user `1` ran in about `22.7ms`.
- Earlier baseline production query shapes for the same user were about `878ms` for long-form list reads and about `903ms` for long-form stats-like counts.
- Production timing verification here is DB-plan level against the live dataset using the new SQL shape, not a post-deploy endpoint latency measurement.

---

## Implementation notes

- No backfill work in this initiative. Existing drifted rows are tolerated; the fix is forward-only.
- The state machine is the source of truth. Read paths should trust `contents.status`.
- `processing_count` remains available through the separate processing-count endpoint if needed elsewhere, but it should not be part of the long-form stats call.

## Outcome

- Long-form visibility is now derived from inbox membership plus `contents.status='completed'`.
- Long-form processing now uses an explicit `awaiting_image` state so `completed` means artwork-ready.
- Long-form list reads no longer depend on JSON metadata predicates for visibility.
- The long-form stats endpoint is now unread-only, matching the current UI need.
- The initiative is complete; any further work should be tracked as a separate follow-on plan.
